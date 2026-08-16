"""Ordered, resumable entry point for the emissions-allocation pipeline.

Stages follow ``docs/METHODOLOGY.md`` §0 to §8. Each is independently runnable, and
API responses are cached under ``data/raw/`` so a rerun costs nothing and a partial
run picks up where it stopped.

Usage::

    python scripts/run_pipeline.py --stage check      # config + client + DuckDB
    python scripts/run_pipeline.py --stage activity   # §1
    python scripts/run_pipeline.py --all

Requires ``GFW_TOKEN`` in ``.env`` or the environment for any stage that calls the
API. ``--stage check`` is the cheapest way to confirm the environment is sound.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from emissions_allocation import Database, GFWClient, load_config  # noqa: E402
from emissions_allocation.gfw import assert_presence, year_bounds  # noqa: E402

log = logging.getLogger("pipeline")

STAGES = ["check", "select", "activity", "specs", "fuel", "emissions",
          "allocation", "baselines", "impacts", "validate"]


def stage_check(cfg, args) -> None:
    """Config, DuckDB and the GFW client, proven against a known-good year.

    The proof is deliberately a re-fetch rather than a mock: one calendar year of
    presence for vessel A must reproduce the record count captured during
    investigation (``data/sample/api/round4/C_year_meta.json``), and must survive
    all three assertions.
    """
    print(f"study period    {cfg.start_date} to {cfg.end_date} "
          f"({cfg.elapsed_hours:,} elapsed hours)")
    print(f"vessels         {[v.imo for v in cfg]}")
    print(f"scenarios       {len(cfg.scenarios())} "
          f"({len(cfg.run['power_estimates'])} power x "
          f"{len(cfg.run['hk_treatments'])} HK x "
          f"{len(cfg.run['smoothing_windows'])} windows)")

    with Database() as db:
        db.register_config_tables(cfg.factors)
        n = db.query("SELECT count(*) AS n FROM imo_table17").fetchone()[0]
        print(f"DuckDB spatial  loaded; IMO Table 17 registered ({n} rows)")

        db.execute(
            "00_register_views",
            power_estimates=cfg.run["power_estimates"],
            hk_treatments=cfg.run["hk_treatments"],
            smoothing_windows=cfg.run["smoothing_windows"],
        )
        scenarios = db.query("SELECT count(*) AS n FROM scenario").fetchone()[0]
        print(f"scenario table  {scenarios} rows")

    if args.offline:
        print("\n--offline: skipping the live API check")
        return

    year = args.year
    client = GFWClient.from_env(cache_dir=cfg.path("raw") / "gfw_cache")
    for vessel in cfg:
        print(f"\nfetching {year} presence for IMO {vessel.imo} "
              f"({', '.join(vessel.shipnames)}) ...")
        records = client.presence_year(vessel.shipnames, year)
        start, end = year_bounds(year)
        kept = assert_presence(
            records, vessel.imo, start, end,
            coverage_min=cfg.run["hour_coverage_min"],
            context=f"{year} world extent",
        )
        observed = sum(float(r.get("hours") or 0) for r in kept)
        elapsed = (end - start).total_seconds() / 3600
        print(f"  {len(kept):,} records, {observed:,.0f} h of {elapsed:,.0f} "
              f"= {observed / elapsed:.2%} coverage")

        expected = (cfg.validation.get("expected") or {}).get(vessel.imo, {})
        target = expected.get(f"presence_records_{year}")
        if target:
            status = "MATCH" if len(kept) == target else "MISMATCH"
            print(f"  captured investigation figure: {target:,} -> {status}")
            if len(kept) != target:
                raise SystemExit(
                    f"record count {len(kept):,} does not match the captured "
                    f"{target:,}. Investigate before trusting downstream output."
                )


def _not_yet(name: str):
    def run(cfg, args) -> None:
        raise SystemExit(
            f"stage {name!r} is not yet implemented. Implemented so far: check."
        )
    return run


HANDLERS = {"check": stage_check, **{s: _not_yet(s) for s in STAGES if s != "check"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=STAGES, help="run one stage")
    parser.add_argument("--all", action="store_true", help="run every stage in order")
    parser.add_argument("--year", type=int, default=2024,
                        help="year used by --stage check (default: 2024)")
    parser.add_argument("--offline", action="store_true",
                        help="skip live API calls")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not args.stage and not args.all:
        parser.error("give --stage STAGE or --all")

    cfg = load_config()
    for stage in STAGES if args.all else [args.stage]:
        print(f"\n{'=' * 70}\n{stage}\n{'=' * 70}")
        HANDLERS[stage](cfg, args)


if __name__ == "__main__":
    main()

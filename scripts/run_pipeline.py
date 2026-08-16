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
            coverage_floor=cfg.run["hour_coverage_floor"],
            coverage_warn=cfg.run["hour_coverage_warn"],
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


def stage_activity(cfg, args) -> None:
    """§1 -- presence, port visits, speed derivation and smoothing.

    The 8-year presence pull is ~44 s per year per vessel and is cached, so the
    first run costs about six minutes a hull and every rerun is free.
    """
    from datetime import datetime, timedelta

    import pandas as pd

    from emissions_allocation import activity

    client = GFWClient.from_env(cache_dir=cfg.path("raw") / "gfw_cache")
    interim = cfg.path("interim")
    start = datetime.combine(cfg.start_date, datetime.min.time())
    end = datetime.combine(cfg.end_date, datetime.min.time()) + timedelta(days=1)

    with Database() as db:
        for vessel in cfg:
            print(f"\nIMO {vessel.imo} ({vessel.label}) -- {', '.join(vessel.shipnames)}")

            print("  presence ...", flush=True)
            presence = activity.load_presence(client, cfg, vessel)
            print(f"    {len(presence):,} observed vessel-hours")

            speeds = activity.derive_speed(presence)
            spine = activity.build_spine(speeds, start, end, vessel.imo)

            print("  port visits ...", flush=True)
            port_calls = activity.load_port_visits(client, cfg, vessel)
            confidences = sorted(set(port_calls["confidence"].dropna()))
            print(f"    {len(port_calls):,} port calls, confidence {confidences}, "
                  f"{port_calls['port_iso3'].nunique()} countries")

            # Classify gaps BEFORE any coverage correction. A contiguous absence
            # must not be scaled up as if it were missed reception.
            spine, inactivity = activity.classify_gaps(
                spine, port_calls, cfg.run["inactivity_gap_days"]
            )
            filled = int(spine["is_interpolated"].sum())
            inactive = int(spine["is_inactive"].sum())
            print(f"    spine {len(spine):,} hours, {filled:,} unobserved "
                  f"({filled / len(spine):.1%}), {inactive:,} of those out of service")
            if not inactivity.empty:
                print("  out-of-service windows (no presence AND no port calls):")
                for gap in inactivity.itertuples():
                    print(f"    {gap.start_ts:%Y-%m-%d} -> {gap.end_ts:%Y-%m-%d}  "
                          f"{int(gap.hours):,} h ({int(gap.hours) // 24} d)")

            # Smoothing runs only now, so a centred window never straddles an
            # out-of-service boundary.
            spine = activity.add_smoothed_speeds(spine, cfg.run["smoothing_windows"])

            print("  v^3 bias by smoothing window:")
            for window in cfg.run["smoothing_windows"]:
                bias = activity.cubic_bias(spine.loc[~spine["is_inactive"], f"sog_w{window}"])
                print(f"    w={window}: {bias:.3f}x")

            db.register_frame("vessel_hour", spine)
            db.register_frame("port_call", port_calls)
            db.table_from("voyage_leg", "12_voyage_leg", eu27=list(activity.EU27))

            legs = db.query("SELECT * FROM voyage_leg").df()
            eu_eu = int(legs["is_eu_eu"].sum())
            print(f"    {len(legs):,} voyage legs, {eu_eu} EU->EU, "
                  f"{int(legs['is_international'].sum())} international")

            coverage = activity.coverage_by_year(spine)
            print("  coverage by year (raw = of elapsed; active = of in-service):")
            for row in coverage.itertuples():
                flag = "  <- low" if row.coverage_active < cfg.run["hour_coverage_warn"] else ""
                print(f"    {row.year}: observed {row.observed_hours:,} | "
                      f"raw {row.coverage_raw:6.2%} | active {row.coverage_active:6.2%}"
                      f"{flag}")

            _check_expectations(cfg, vessel, port_calls, coverage)

            spine.to_parquet(interim / f"vessel_hour_{vessel.imo}.parquet", index=False)
            port_calls.to_parquet(interim / f"port_call_{vessel.imo}.parquet", index=False)
            legs.to_parquet(interim / f"voyage_leg_{vessel.imo}.parquet", index=False)
            coverage.to_parquet(interim / f"coverage_{vessel.imo}.parquet", index=False)
            print(f"  wrote 4 tables to {interim}")


def _check_expectations(cfg, vessel, port_calls, coverage) -> None:
    """Assert against the figures captured during investigation.

    A mismatch means something upstream moved, and downstream output should not be
    trusted until it is understood.
    """
    expected = (cfg.validation.get("expected") or {}).get(vessel.imo)
    if not expected:
        return

    failures = []
    if "port_calls_total" in expected and len(port_calls) != expected["port_calls_total"]:
        failures.append(
            f"port calls: got {len(port_calls)}, expected {expected['port_calls_total']}"
        )

    want_confidence = expected.get("port_calls_all_confidence")
    got_confidence = set(port_calls["confidence"].dropna())
    if want_confidence and got_confidence != {want_confidence}:
        failures.append(f"confidences: got {sorted(got_confidence)}, expected [{want_confidence}]")

    by_year = dict(zip(coverage["year"], coverage["observed_hours"]))
    for year, want in (expected.get("observed_hours_by_year") or {}).items():
        got = int(by_year.get(int(year), 0))
        if got != want:
            failures.append(f"{year} observed hours: got {got:,}, expected {want:,}")

    if failures:
        raise SystemExit("  VALIDATION FAILED\n    " + "\n    ".join(failures))
    print("  validated against the captured investigation figures: MATCH")


def stage_specs(cfg, args) -> None:
    """§2 -- TEU inversion and the three power/speed estimates."""
    from emissions_allocation import specs

    for vessel in cfg:
        print(f"\nIMO {vessel.imo} ({vessel.label})")
        teu = specs.resolve_teu(vessel, cfg)
        print(f"  TEU (inverted from beam {vessel.require_spec('beam_m')} m): "
              f"{teu:,.0f}  [estimated]")

        print("  Cepowski & Chorab DWT relations vs the observed hull:")
        for name, r in specs.validate_hull_relations(vessel, cfg.defaults).items():
            print(f"    {name:8s} predicted {r['predicted']:8.1f}  "
                  f"observed {r['observed']:8.1f}  error {r['error_pct']:+6.1f}%")

        print("  power/speed estimates (no primary -- the spread is the result):")
        for estimate in specs.build_estimates(vessel, cfg).values():
            print("    " + estimate.describe())

        print("  NOTE estimate C (sourced installed power and service speed) is "
              "OPEN ITEM 4 and absent.")


def stage_fuel(cfg, args) -> None:
    """§3 -- ECA point-in-polygon, EU->EU legs, fuel assignment."""
    import pandas as pd

    from emissions_allocation import activity, fuel

    interim = cfg.path("interim")
    with Database() as db:
        n = fuel.register_eca(db, cfg)
        areas = db.query("SELECT area FROM eca_polygons ORDER BY area").df()["area"].tolist()
        print(f"ECA polygons: {n}")
        for a in areas:
            print(f"  - {a}")
        print("  (no Mediterranean -- in force May 2025, after the study period)")

        for vessel in cfg:
            print(f"\nIMO {vessel.imo} ({vessel.label})")
            fuel.assert_build_year_in_range(vessel, cfg.factors)
            print(f"  engine {vessel.require_spec('engine_type')}, "
                  f"high-speed: {fuel.is_high_speed(vessel)}")

            spine = pd.read_parquet(interim / f"vessel_hour_{vessel.imo}.parquet")
            legs = pd.read_parquet(interim / f"voyage_leg_{vessel.imo}.parquet")
            db.register_frame("vessel_hour", spine)
            db.register_frame("voyage_leg", legs)

            print("  point-in-polygon over ECAs ...", flush=True)
            assignment = fuel.assign_fuel(db, cfg, vessel)

            total = len(assignment)
            in_eca = int(assignment["in_eca"].sum())
            eu_eu = int(assignment["is_eu_eu_leg"].sum())
            print(f"  {total:,} vessel-hours")
            print(f"    in an ECA:        {in_eca:,} ({in_eca / total:.1%})")
            print(f"    on an EU->EU leg: {eu_eu:,} ({eu_eu / total:.1%})")
            print("  fuel split:")
            for f, n_hours in assignment["fuel_type"].value_counts().items():
                ef = fuel.emission_factor(cfg.factors, f)
                print(f"    {f:5s} {n_hours:>7,} h ({n_hours / total:5.1%})  "
                      f"EF {ef} g CO2/g fuel")
            by_area = assignment[assignment["in_eca"]]["eca_area"].value_counts()
            if not by_area.empty:
                print("  ECA hours by area:")
                for area, n_hours in by_area.items():
                    print(f"    {area:34s} {n_hours:>7,} h")

            assignment.to_parquet(
                interim / f"fuel_assignment_{vessel.imo}.parquet", index=False
            )
            print(f"  wrote fuel_assignment_{vessel.imo}.parquet")


def _not_yet(name: str):
    def run(cfg, args) -> None:
        raise SystemExit(
            f"stage {name!r} is not yet implemented. "
            "Implemented so far: check, activity, specs, fuel."
        )
    return run


HANDLERS = {
    "check": stage_check,
    "activity": stage_activity,
    "specs": stage_specs,
    "fuel": stage_fuel,
    **{s: _not_yet(s) for s in STAGES
       if s not in ("check", "activity", "specs", "fuel")},
}


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

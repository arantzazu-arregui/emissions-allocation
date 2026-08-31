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
          f"{len(cfg.run['smoothing_windows'])} windows)")

    with Database() as db:
        db.register_config_tables(cfg.factors)
        n = db.query("SELECT count(*) AS n FROM imo_table17").fetchone()[0]
        print(f"DuckDB spatial  loaded; IMO Table 17 registered ({n} rows)")

        db.execute(
            "00_register_views",
            power_estimates=cfg.run["power_estimates"],
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
    """§§1–2 -- presence, SOG derivation, port visits, and voyage legs.

    The 8-year presence pull is ~44 s per year per vessel and is cached, so the
    first run costs about six minutes a hull and every rerun is free.
    """
    from datetime import datetime, timedelta

    from emissions_allocation import activity

    client = GFWClient.from_env(cache_dir=cfg.path("raw") / "gfw_cache")
    interim = cfg.path("interim")
    start = datetime.combine(cfg.start_date, datetime.min.time())
    end = datetime.combine(cfg.end_date, datetime.min.time()) + timedelta(days=1)

    with Database() as db:
        for vessel in cfg:
            print(f"\nIMO {vessel.imo} ({vessel.label}) -- {', '.join(vessel.shipnames)}")

            print("  GFW observed-activity archive ...", flush=True)
            observed_presence = activity.load_observed_presence(client, cfg, vessel)
            observed_activity = activity.observed_activity_by_year(
                observed_presence,
                vessel,
                cfg.gfw_observation_start_date,
                cfg.gfw_observation_end_date,
                cfg.gfw_observed_activity["min_observed_hours"],
                cfg.gfw_observed_activity["min_observed_days"],
            )
            activity.assert_study_years_observed_active(observed_activity, cfg, vessel)
            n_observed = int((observed_activity["activity_state"] == "observed_active").sum())
            print(f"    {n_observed} observed-active years; "
                  f"{len(observed_activity) - n_observed} unobserved years")

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
            spine = activity.add_smoothed_speeds(
                spine, cfg.run["smoothing_windows"], port_calls
            )

            sensitivity = cfg.run.get("imo2020_sog_sensitivity", {})
            imo2020_audit = None
            if sensitivity.get("enabled", False):
                gap_bounds = sensitivity["missing_gap_bounds_hours"]
                spine, imo2020_audit = activity.add_imo2020_port_phase_sensitivity(
                    spine,
                    port_calls,
                    cfg.run["smoothing_windows"],
                    transition_hours=int(sensitivity["transition_hours"]),
                    min_gap_hours=float(gap_bounds[0]),
                    max_gap_hours=float(gap_bounds[1]),
                )
                row = imo2020_audit.iloc[0]
                print("  IMO 2020 port-phase SOG sensitivity (non-primary):")
                print(f"    threshold {row.missing_gap_threshold_hours:.1f} h; "
                      f"phase-filled {int(row.short_gap_hours):,} h in "
                      f"{int(row.short_gap_runs):,} short gaps; "
                      f"retained primary treatment for {int(row.long_gap_hours):,} h "
                      f"in {int(row.long_gap_runs):,} long gaps")

            print("  v^3 bias by smoothing window:")
            for window in cfg.run["smoothing_windows"]:
                bias = activity.cubic_bias(spine.loc[~spine["is_inactive"], f"sog_w{window}"])
                print(f"    w={window}: {bias:.3f}x")

            db.register_frame("vessel_hour", spine)
            db.register_frame("port_call", port_calls)
            db.table_from(
                "voyage_leg", "12_voyage_leg", eu27=list(activity.EU27),
                uk_in_eu_through=activity.UK_IN_EU_THROUGH,
            )

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
            if imo2020_audit is not None:
                imo2020_audit.to_parquet(
                    interim / f"imo2020_sog_sensitivity_{vessel.imo}.parquet", index=False
                )
            observed_activity.to_parquet(
                interim / f"gfw_observed_activity_{vessel.imo}.parquet", index=False
            )
            print(f"  wrote {6 if imo2020_audit is not None else 5} tables to {interim}")


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
    """§3 -- ship specifications and the configured power/speed estimates."""
    from emissions_allocation import specs

    for vessel in cfg:
        print(f"\nIMO {vessel.imo} ({vessel.label})")
        ship_type, size, unit = specs.size_for_table17(vessel, cfg)
        print(f"  ship type {ship_type}; IMO Table 17 indexed by {unit} = {size:,.0f}"
              + ("  [estimated -- inverted from beam]" if unit == "TEU" else "  [observed]"))

        # Section 3's beam-to-TEU and Admiralty relations are container-specific.
        if unit == "TEU":
            print("  Cepowski & Chorab DWT relations vs the observed hull:")
            for name, r in specs.validate_hull_relations(vessel, cfg.defaults).items():
                print(f"    {name:8s} predicted {r['predicted']:8.1f}  "
                      f"observed {r['observed']:8.1f}  error {r['error_pct']:+6.1f}%")

        print("  power/speed estimates (no primary -- the spread is the result):")
        for estimate in specs.build_estimates(vessel, cfg).values():
            print("    " + estimate.describe())

        excluded = sorted(set(cfg.run["power_estimates"])
                          - set(vessel.resolve_power_estimates(cfg.run["power_estimates"])))
        if excluded:
            print(f"  excluded for this hull form: {excluded} "
                  "(no calibration published -- see config/pilot.yaml)")
        print("  NOTE estimate C (sourced installed power and service speed) is "
              "OPEN ITEM 4 and absent for both hulls.")


def stage_fuel(cfg, args) -> None:
    """§5 -- ECA point-in-polygon, EU-to-EU legs, and fuel assignment."""
    import pandas as pd

    from emissions_allocation import fuel

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


def _load_emissions_year(cfg):
    """Read the Section 5 output, or explain precisely why it is not there yet."""
    import pandas as pd

    path = cfg.path("interim") / "emissions_year.parquet"
    if not path.exists():
        raise SystemExit(
            "Section 5 has not produced emissions_year.parquet yet.\n"
            "  Section 4 is blocked on the coastline layer (OPEN ITEM 3): the Table 16\n"
            "  operating-mode matrix needs distance-to-coast per vessel-hour.\n"
            "  Download Marine and Land Zones v4 from marineregions.org/downloads.php,\n"
            "  put the zip in data/external/marineregions/, and set spatial.coastline\n"
            "  in config/pilot.yaml. Then: run_pipeline.py --stage emissions"
        )
    return pd.read_parquet(path)


def stage_baselines(cfg, args) -> None:
    """§7 -- Global Carbon Budget baselines."""
    from emissions_allocation import baselines

    frame = baselines.build_baselines(cfg)
    frame.to_parquet(cfg.path("interim") / "baseline.parquet", index=False)

    print(f"{len(frame):,} country-year baselines, "
          f"{frame['country'].nunique()} countries, "
          f"{frame['year'].min()}-{frame['year'].max()}")
    print("  units converted MtC -> Mt CO2 (x3.664); national columns exclude bunkers")

    check = baselines.shipping_cross_check(cfg, 2024)
    print(f"\n  Section 7 cross-check: GCB International Shipping 2024 = "
          f"{check['mtc']:.2f} MtC = {check['mtco2']:.0f} Mt CO2")
    print("  (an independent global total to sanity-check a fleet-scale result)")


def stage_allocation(cfg, args) -> None:
    """§6 -- allocate ship-year CO2 to countries under each rule."""
    import pandas as pd

    from emissions_allocation import activity, allocation as alloc

    print("Allocation keys per vessel (the qualitative result at n=1):")
    for row in alloc.summarise_options(cfg).itertuples():
        keys = "  ".join(f"{o}={getattr(row, o)}" for o in alloc.ALLOCATION_OPTIONS)
        verdict = (
            "DEGENERATE (all options -> one budget)" if row.is_degenerate
            else f"{row.n_distinct_countries} distinct budgets"
        )
        print(f"    IMO {row.imo}: {keys}")
        print(f"      -> {verdict}")

    interim = cfg.path("interim")
    required = [
        interim / f"emissions_hour_{vessel.imo}.parquet" for vessel in cfg
    ] + [
        interim / f"voyage_leg_{vessel.imo}.parquet" for vessel in cfg
    ] + [
        interim / f"coverage_{vessel.imo}.parquet" for vessel in cfg
    ]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        raise SystemExit(
            "Section 6 voyage-based allocation requires completed activity and emissions "
            f"outputs; missing: {', '.join(missing)}"
        )

    hourly = pd.concat(
        [pd.read_parquet(interim / f"emissions_hour_{vessel.imo}.parquet") for vessel in cfg],
        ignore_index=True,
    )
    legs = pd.concat(
        [pd.read_parquet(interim / f"voyage_leg_{vessel.imo}.parquet") for vessel in cfg],
        ignore_index=True,
    )
    if "label_end_ts" not in legs.columns:
        # This schema addition changes only the deterministic port-call-derived
        # voyage labels. Rebuild it locally instead of requiring the expensive
        # activity stage (and its GFW archive requests) to run again.
        print("voyage_leg parquet files predate label_end_ts; rebuilding from port calls")
        port_calls = pd.concat(
            [pd.read_parquet(interim / f"port_call_{vessel.imo}.parquet") for vessel in cfg],
            ignore_index=True,
        )
        with Database(spatial=False) as db:
            db.register_frame("port_call", port_calls)
            db.table_from(
                "voyage_leg", "12_voyage_leg", eu27=list(activity.EU27),
                uk_in_eu_through=activity.UK_IN_EU_THROUGH,
            )
            legs = db.query("SELECT * FROM voyage_leg").df()
        for vessel in cfg:
            legs.loc[legs["imo"] == vessel.imo].to_parquet(
                interim / f"voyage_leg_{vessel.imo}.parquet", index=False
            )
    coverage = pd.concat(
        [pd.read_parquet(interim / f"coverage_{vessel.imo}.parquet") for vessel in cfg],
        ignore_index=True,
    )
    with Database() as db:
        # Section 2 EEZ domestic/international diagnostic. The allocation itself
        # uses the port-to-port labels calculated below.
        alloc.register_eez(db, cfg)
        spines = [pd.read_parquet(cfg.path("interim") / f"vessel_hour_{v.imo}.parquet")
                  for v in cfg]
        db.register_frame("vessel_hour", pd.concat(spines, ignore_index=True))
        db.table_from("eez_hour", "20_eez_join")
        domestic = alloc.domestic_test(db, cfg)
        print("\nSection 2 international/domestic test:")
        for row in domestic.itertuples():
            print(f"  IMO {row.imo}: dominant EEZ {row.dominant_eez_iso3} "
                  f"{row.dominant_eez_share:.1%} of in-EEZ hours -> "
                  f"{'DOMESTIC' if row.is_domestic else 'INTERNATIONAL'}"
                  f"  ({row.hours_disputed:,} h in disputed/joint-regime waters)")
        domestic.to_csv(cfg.path("out") / "domestic_test.csv", index=False)

        international = alloc.international_emissions_year(db, hourly, legs, coverage, cfg)
        result = alloc.allocate(db, cfg, international)
    international.to_parquet(interim / "international_emissions_year.parquet", index=False)
    result.to_parquet(interim / "allocation.parquet", index=False)
    diagnostics = international.groupby("imo", as_index=False).agg(
        modelled_hours=("modelled_hours", "first"),
        labelled_hours=("labelled_hours", "first"),
        unallocated_hours=("unallocated_hours", "first"),
        international_hour_share=("international_hour_share", "first"),
    )
    diagnostics.to_csv(cfg.path("out") / "international_voyage_diagnostics.csv", index=False)
    print("\nSection 6 voyage-based international attribution:")
    for row in diagnostics.itertuples():
        print(f"  IMO {row.imo}: {row.international_hour_share:.1%} international "
              f"of labelled hours; {row.unallocated_hours:,} boundary/unknown hours")
    print(f"\n{len(result):,} allocation rows written")


def stage_impacts(cfg, args) -> None:
    """§7 -- dE, dE% and rank against national budgets."""
    import pandas as pd

    from emissions_allocation import baselines, impacts

    interim = cfg.path("interim")
    allocation = pd.read_parquet(interim / "allocation.parquet")
    baseline = baselines.build_baselines(cfg)

    # Impact calculations use tabular joins only; avoid initializing the spatial
    # extension for this downstream, fully offline stage.
    with Database(spatial=False) as db:
        result = impacts.compute_impacts(db, allocation, baseline)
    result.to_parquet(interim / "impacts.parquet", index=False)

    print(f"{len(result):,} impact rows")
    print("\n  NOTE ranking and concentration shares are structurally meaningless")
    print("  at n=1 -- the code path is exercised, not interpreted.")

    # Section 7 -- the groupings the paper reports.
    with Database(spatial=False) as db2:
        regional = impacts.impacts_by_region(db2, cfg, allocation, baseline)
    regional.to_parquet(interim / "impacts_by_region.parquet", index=False)

    headline = regional[
        (regional.year == 2024) & (regional.smoothing_window == 3)
        & (regional.region.isin(["OECD", "Non-OECD", "EU27", "KP Annex B", "Non KP Annex B"]))
    ]
    if not headline.empty:
        print("Section 7 by grouping, 2024, w=3 (Mt CO2):")
        pivot = headline.pivot_table(index="region", columns="option",
                                     values="delta_e_mt", aggfunc="sum")
        print(pivot.to_string(float_format=lambda v: f"{v:.4f}"))
        print("  (a country belongs to several groups; totals are per group, not additive)")

    spread = impacts.scenario_spread(result)
    spread.to_parquet(interim / "scenario_spread.parquet", index=False)
    print(f"\n  scenario spread written ({len(spread):,} rows)")


def stage_emissions(cfg, args) -> None:
    """§§4–5 -- operating mode, power demand, SFC correction, and CO2."""
    import pandas as pd

    from emissions_allocation import activity, emissions, specs
    from emissions_allocation.allocation import register_eez

    interim = cfg.path("interim")
    with Database() as db:
        db.register_config_tables(cfg.factors)
        register_eez(db, cfg)

        for vessel in cfg:
            print(f"\nIMO {vessel.imo} ({vessel.label})")
            spine = pd.read_parquet(interim / f"vessel_hour_{vessel.imo}.parquet")
            port_calls = pd.read_parquet(interim / f"port_call_{vessel.imo}.parquet")
            fuel_assignment = pd.read_parquet(
                interim / f"fuel_assignment_{vessel.imo}.parquet"
            )
            coverage = activity.coverage_by_year(spine)

            # Allow Sections 4–5 to resume from a spine written before the optional
            # sensitivity was introduced.  Rebuilding this deterministic branch
            # here avoids an opaque KeyError after the expensive distance step.
            sensitivity = cfg.run.get("imo2020_sog_sensitivity", {})
            if sensitivity.get("enabled", False):
                required_speed_columns = {
                    f"sog_imo2020_w{window}" for window in cfg.run["smoothing_windows"]
                }
                missing_speed_columns = required_speed_columns - set(spine.columns)
                if missing_speed_columns:
                    gap_bounds = sensitivity["missing_gap_bounds_hours"]
                    print("  rebuilding missing IMO 2020 port-phase SOG sensitivity "
                          "columns from the saved activity inputs ...", flush=True)
                    spine, imo2020_audit = activity.add_imo2020_port_phase_sensitivity(
                        spine,
                        port_calls,
                        cfg.run["smoothing_windows"],
                        transition_hours=int(sensitivity["transition_hours"]),
                        min_gap_hours=float(gap_bounds[0]),
                        max_gap_hours=float(gap_bounds[1]),
                    )
                    spine.to_parquet(interim / f"vessel_hour_{vessel.imo}.parquet", index=False)
                    imo2020_audit.to_parquet(
                        interim / f"imo2020_sog_sensitivity_{vessel.imo}.parquet", index=False
                    )

            db.register_frame("vessel_hour", spine)
            db.register_frame("port_call", port_calls)

            print("  distances to port and coast (the slow step) ...", flush=True)
            distances = emissions.register_distance_layers(db, cfg, spine)
            near_coast = distances["coast_nm"].notna().sum()
            near_port = distances["port_nm"].notna().sum()
            print(f"    {len(distances):,} distinct positions; "
                  f"{near_coast:,} within ~7 nm of coast, {near_port:,} of a port")

            estimates = specs.build_estimates(vessel, cfg)
            print(f"  running {len(estimates) * len(cfg.run['smoothing_windows'])} "
                  f"scenarios ...", flush=True)
            hourly, yearly = emissions.annual_emissions(
                db, cfg, vessel, spine, fuel_assignment, coverage, estimates
            )

            if sensitivity.get("enabled", False):
                imo2020_hourly, imo2020_yearly = emissions.annual_emissions(
                    db,
                    cfg,
                    vessel,
                    spine,
                    fuel_assignment,
                    coverage,
                    estimates,
                    speed_prefix="sog_imo2020",
                    gap_treatment="imo2020_port_phase",
                )
                imo2020_hourly.to_parquet(
                    interim / f"emissions_hour_imo2020_port_phase_{vessel.imo}.parquet",
                    index=False,
                )
                imo2020_yearly.to_parquet(
                    interim / f"emissions_year_imo2020_port_phase_{vessel.imo}.parquet",
                    index=False,
                )
                print("  wrote separate IMO 2020 port-phase sensitivity emissions; "
                      "primary allocation inputs remain unchanged")

            print("\n  operating-mode split (scenario A, w=3):")
            sample = hourly[hourly["scenario_id"] == "A_w3"]
            if not sample.empty:
                for mode, n in sample["operating_mode"].value_counts().items():
                    print(f"    {mode:16s} {n:>7,} h ({n / len(sample):5.1%})")

            treatment = (
                "coverage-scaled observed hours"
                if cfg.run["coverage_correction"]
                else "interpolated active hours (not coverage-scaled)"
            )
            print(f"\n  annual CO2 (t), {treatment}, by power estimate:")
            pivot = yearly[yearly["smoothing_window"] == 3].pivot_table(
                index="year", columns="power_estimate", values="co2_tonnes"
            )
            for year, row in pivot.iterrows():
                cells = "  ".join(f"{k}={v:>10,.0f}" for k, v in row.items())
                low = yearly[(yearly["year"] == year)]["is_low_confidence"].any()
                print(f"    {year}  {cells}{'   <- low confidence' if low else ''}")

            total = yearly[yearly["smoothing_window"] == 3].groupby(
                "power_estimate")["co2_tonnes"].sum()
            print("\n  8-year total CO2 (t), w=3:")
            for k, v in total.items():
                print(f"    estimate {k}: {v:>12,.0f}")
            if len(total) > 1:
                print(f"    spread: {total.max() / total.min():.2f}x")

            hourly.to_parquet(interim / f"emissions_hour_{vessel.imo}.parquet", index=False)
            yearly.to_parquet(interim / f"emissions_year_{vessel.imo}.parquet", index=False)

    frames = [
        pd.read_parquet(interim / f"emissions_year_{v.imo}.parquet") for v in cfg
    ]
    pd.concat(frames, ignore_index=True).to_parquet(
        interim / "emissions_year.parquet", index=False
    )
    print(f"\nwrote emissions_year.parquet ({sum(len(f) for f in frames):,} rows)")


def stage_validate(cfg, args) -> None:
    """§8 -- validate, inspect, and extend."""
    import pandas as pd

    from emissions_allocation import activity, specs, validate

    interim = cfg.path("interim")
    for vessel in cfg:
        print(f"\nIMO {vessel.imo} ({vessel.label})")
        spine = pd.read_parquet(interim / f"vessel_hour_{vessel.imo}.parquet")
        port_calls = pd.read_parquet(interim / f"port_call_{vessel.imo}.parquet")
        legs = pd.read_parquet(interim / f"voyage_leg_{vessel.imo}.parquet")
        hourly = pd.read_parquet(interim / f"emissions_hour_{vessel.imo}.parquet")
        yearly = pd.read_parquet(interim / f"emissions_year_{vessel.imo}.parquet")
        coverage = activity.coverage_by_year(spine)
        estimates = specs.build_estimates(vessel, cfg)

        checks = validate.run_all(
            cfg, vessel, spine, port_calls, legs, coverage, hourly, yearly, estimates
        )
        for check in checks:
            print("  " + check.line())
            if check.basis:
                print(f"            basis: {check.basis}")

        summary = validate.summarise(checks)
        summary.to_csv(cfg.path("out") / f"validation_{vessel.imo}.csv", index=False)

        counts = summary["status"].value_counts().to_dict()
        print(f"\n  {counts}")
        if validate.FAIL in counts:
            print("  NOTE the fleet-envelope FAIL is the EXPECTED result for "
                  "estimate A -- see Section 3.")


def stage_select(cfg, args) -> None:
    """§0 -- candidate discovery and criteria filtering.

    Steps 1-4 run here. Step 5 -- criterion 7, registered-owner country != flag
    country -- needs an Equasis login and cannot be automated, so this produces a
    ranked shortlist and hands over.

    Skipped by --all once every configured vessel has its specs, because it costs
    eight world-extent API calls and its output is a hand-off document, not a
    pipeline input. Force it with --reselect.
    """
    from emissions_allocation import selection

    if not args.reselect and all(v.allocation_keys for v in cfg):
        print(f"every configured vessel already has allocation keys "
              f"({[v.imo for v in cfg]}); skipping discovery.")
        print("  run with --reselect to search for more candidates.")
        return

    client = GFWClient.from_env(cache_dir=cfg.path("raw") / "gfw_cache")
    print("§0.2 steps 1-3: pooling candidates from world-extent presence ...", flush=True)
    candidates = selection.discover_candidates(client, cfg)
    print(f"  pool: {len(candidates):,} distinct IMOs")

    kept = selection.apply_presence_criteria(candidates, len(cfg.years))
    print(f"  distinct name AND present in all {len(cfg.years)} sampled years: {len(kept):,}")

    print(f"§0.2 step 4: port calls for the top {args.shortlist} ...", flush=True)
    enriched = selection.enrich_with_port_calls(client, cfg, kept, limit=args.shortlist)

    out = cfg.path("out") / "vessel_candidates.csv"
    frame = selection.shortlist(enriched, out)
    if frame.empty:
        print("  no candidates survived; widen the flags or sample days.")
        return

    passing = frame[frame.passes_api_criteria]
    print(f"\n  {len(passing)} of {len(frame)} pass criteria 5 and 6:")
    for row in passing.head(12).itertuples():
        print(f"    {row.imo}  {row.shipname[:24]:<24} {row.flag}  "
              f"{row.port_calls:>4} calls  {row.port_countries:>2} countries  "
              f"{row.eu_port_calls:>3} EU")
    print(f"\n  wrote {out}")
    print("  CRITERION 7 IS NOT CLOSED HERE: registered-owner country must differ")
    print("  from flag country, which needs an Equasis lookup per candidate.")


def _not_yet(name: str):
    """Placeholder for a stage with no handler.

    The message lists the handler table rather than a hand-written string, so it
    cannot drift out of date the way the previous one did -- it went on claiming Section 4
    was blocked on the coastline layer for eight commits after that stopped being
    true.
    """
    def run(cfg, args) -> None:
        done = sorted(k for k, v in HANDLERS.items() if v.__name__ != "run")
        raise SystemExit(
            f"stage {name!r} has no handler. Implemented: {', '.join(done)}."
        )
    return run


HANDLERS = {
    "check": stage_check,
    "select": stage_select,
    "activity": stage_activity,
    "specs": stage_specs,
    "fuel": stage_fuel,
    "emissions": stage_emissions,
    "baselines": stage_baselines,
    "allocation": stage_allocation,
    "impacts": stage_impacts,
    "validate": stage_validate,
    **{s: _not_yet(s) for s in STAGES if s not in (
        "check", "select", "activity", "specs", "fuel", "emissions",
        "baselines", "allocation", "impacts", "validate")},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=STAGES, help="run one stage")
    parser.add_argument("--all", action="store_true", help="run every stage in order")
    parser.add_argument("--year", type=int, default=2024,
                        help="year used by --stage check (default: 2024)")
    parser.add_argument("--reselect", action="store_true",
                        help="force Section 0.2 discovery even when the vessel list is complete")
    parser.add_argument("--shortlist", type=int, default=18,
                        help="how many candidates to enrich with port calls (default: 18)")
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

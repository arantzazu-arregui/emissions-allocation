"""§2 specifications and §3 fuel assignment.

The §2 tests pin the three estimates to the figures in docs/METHODOLOGY.md §2.2,
including the one that is *supposed* to fail: estimate A returns 28.92 kn, above the
24.5 kn maximum of the modern container fleet. That failure is a reported result and
is asserted as such -- a future change that quietly brought it inside the envelope
would be hiding the finding, not fixing it.
"""

from __future__ import annotations

import pytest

from emissions_allocation import specs
from emissions_allocation.config import ConfigError, MissingParameter, load_config
from emissions_allocation.fuel import (
    assert_build_year_in_range,
    emission_factor,
    is_high_speed,
    sfc_base,
)

VESSEL_A = "9516454"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def vessel(cfg):
    return cfg.vessel(VESSEL_A)


# ---------------------------------------------------------------------------
# 2.1 -- TEU inversion
# ---------------------------------------------------------------------------


def test_teu_inversion_matches_the_documented_value(vessel, cfg) -> None:
    """B = 3.27 * TEU^0.29 inverted at B = 51.20 m gives 13,174 TEU."""
    assert specs.resolve_teu(vessel, cfg) == pytest.approx(13_174, abs=5)


def test_teu_inversion_round_trips() -> None:
    assert 3.27 * specs.teu_from_beam(51.20) ** 0.29 == pytest.approx(51.20, abs=0.001)


def test_teu_inversion_is_monotonic() -> None:
    assert specs.teu_from_beam(60.0) > specs.teu_from_beam(51.2) > specs.teu_from_beam(40.0)


def test_teu_inversion_rejects_nonsense_beam() -> None:
    with pytest.raises(ValueError, match="positive"):
        specs.teu_from_beam(0.0)


def test_vessel_a_lands_in_the_12000_14499_band(vessel, cfg) -> None:
    """The band whose Table 17 row §4.3 quotes."""
    ship_type, size, unit = specs.size_for_table17(vessel, cfg)
    assert (ship_type, unit) == ("container", "TEU")
    assert 12_000 <= size <= 14_499


def test_hull_relations_validate_within_a_few_percent(vessel, cfg) -> None:
    """The basis for trusting the beam inversion."""
    for name, result in specs.validate_hull_relations(vessel, cfg.defaults).items():
        assert abs(result["error_pct"]) < 5.0, f"{name} off by {result['error_pct']:.1f}%"


# ---------------------------------------------------------------------------
# 2.2 -- estimate A
# ---------------------------------------------------------------------------


def test_containership_capacity_is_capped_at_80000_dwt(vessel, cfg) -> None:
    """MEPC.333(76) caps the containership capacity parameter at 80,000 DWT.

    Vessel A is 156,610 DWT, so the cap bites. Missing it gave 28.89 kn instead of
    25.55 kn -- and because load goes as (SOG/V)^3, that understated main-engine
    load by 1.45x. docs/METHODOLOGY.md §2.2 quotes the uncapped figure.
    """
    estimate = specs.estimate_a_eexi(vessel, cfg.defaults)
    assert estimate.variants["capacity"] == 156_610
    assert estimate.variants["capacity_used"] == 80_000
    assert estimate.design_speed_kn == pytest.approx(25.55, abs=0.01)
    assert estimate.mcr_kw == pytest.approx(113_004, rel=0.001)


def test_uncapped_speed_would_understate_load_by_45_percent(vessel, cfg) -> None:
    row = cfg.defaults["eexi_curve_fit"]["speed"]["containership"]
    uncapped = row["A"] * vessel.require_spec("dwt") ** row["C"]
    capped = specs.estimate_a_eexi(vessel, cfg.defaults).design_speed_kn
    assert (uncapped / capped) ** 3 == pytest.approx(1.45, abs=0.02)


def test_estimate_a_fails_the_fleet_envelope(vessel, cfg) -> None:
    """28.92 kn exceeds the 24.5 kn maximum across 215 designs built since 2015.

    This is a REPORTED RESULT. If this test ever starts failing because the estimate
    moved inside the envelope, the finding has been hidden, not fixed.
    """
    assert specs.estimate_a_eexi(vessel, cfg.defaults).within_fleet_envelope is False


def test_estimate_a_is_marked_estimated(vessel, cfg) -> None:
    assert specs.estimate_a_eexi(vessel, cfg.defaults).estimated is True


def test_speed_constants_cover_twelve_ship_types(cfg) -> None:
    speed = cfg.defaults["eexi_curve_fit"]["speed"]
    assert len(speed) == 12
    assert {"bulk_carrier", "tanker", "vehicle", "ro_ro", "lng_carrier"} <= set(speed)


def test_vehicle_carrier_speed_resolves(cfg) -> None:
    """RCC AMERICA, 21,182 DWT -> ~20 kn, plausible for a car carrier."""
    row = cfg.defaults["eexi_curve_fit"]["speed"]["vehicle"]
    assert row["A"] * 21182 ** row["C"] == pytest.approx(19.96, abs=0.02)


def test_power_table_is_containership_only_and_raises_otherwise(cfg) -> None:
    """The P_ME table is separate and only its containership row is transcribed.

    Borrowing it for another type would be an invented value.
    """
    assert set(cfg.defaults["eexi_curve_fit"]["power"]) == {"containership"}


def test_eexi_power_is_nearly_linear_in_deadweight(cfg) -> None:
    """D = 1.030 is why the curve fit breaks at the top of the container range."""
    assert cfg.defaults["eexi_curve_fit"]["power"]["containership"]["D"] == pytest.approx(1.03)


# ---------------------------------------------------------------------------
# 2.2 -- estimate B
# ---------------------------------------------------------------------------


def test_estimate_b_speed_range_matches_the_documentation(vessel, cfg) -> None:
    """21.5-23.8 kn at L_BP = 345 m over Fn 0.19-0.21."""
    low, high = specs.estimate_b_admiralty(vessel, cfg.defaults).variants["speed_kn_range"]
    assert low == pytest.approx(21.5, abs=0.1)
    assert high == pytest.approx(23.8, abs=0.1)


def test_estimate_b_displacement_routes_bracket_a_real_difference(vessel, cfg) -> None:
    """176,807 t geometric against 195,762 t from the DWT ratio -- ~11% apart.

    Charchalis's own worked example runs ~18% above C_B*L*B*T*rho, so the two routes
    bracket a convention difference rather than a rounding error, and both are carried.
    """
    variants = specs.estimate_b_admiralty(vessel, cfg.defaults).variants
    geometric = variants["displacement_geometric_t"]
    ratio = variants["displacement_ratio_t"]
    assert geometric == pytest.approx(176_807, rel=0.005)
    assert ratio == pytest.approx(195_762, rel=0.001)
    assert ratio > geometric


def test_estimate_b_sits_inside_the_fleet_envelope(vessel, cfg) -> None:
    """The contrast with estimate A that makes the spread interpretable."""
    assert specs.estimate_b_admiralty(vessel, cfg.defaults).within_fleet_envelope is True


def test_estimate_b_power_is_far_below_estimate_a(vessel, cfg) -> None:
    """~78,000 kW against 113,004 kW -- a 1.4x spread carried as uncertainty."""
    a = specs.estimate_a_eexi(vessel, cfg.defaults)
    b = specs.estimate_b_admiralty(vessel, cfg.defaults)
    assert b.mcr_kw < a.mcr_kw
    assert a.mcr_kw / b.mcr_kw == pytest.approx(1.44, abs=0.05)


def test_admiralty_relation_scales_as_speed_cubed() -> None:
    single = specs.admiralty_power_kw(180_000, 11.0, 482)
    double = specs.admiralty_power_kw(180_000, 22.0, 482)
    assert double / single == pytest.approx(8.0)


def test_froude_speed_matches_a_hand_calculation() -> None:
    """Fn 0.21 at L_BP 345 m -> 23.75 kn."""
    assert specs.froude_speed_kn(0.21, 345.0) == pytest.approx(23.75, abs=0.05)


def test_c_adm_calibration_sits_in_the_textbook_band(cfg) -> None:
    c_adm = cfg.defaults["admiralty"]["c_adm"]
    assert 400 <= c_adm["median"] <= 600
    assert c_adm["min"] < c_adm["median"] < c_adm["max"]


# ---------------------------------------------------------------------------
# 2.2 -- estimate C
# ---------------------------------------------------------------------------


def test_estimate_c_raises_rather_than_guessing(vessel, cfg) -> None:
    """OPEN ITEM 4. No free source supplies installed power or design speed."""
    with pytest.raises(MissingParameter):
        specs.estimate_c_sourced(vessel, cfg.defaults)


def test_estimate_c_message_says_where_to_put_the_values(vessel, cfg) -> None:
    with pytest.raises(MissingParameter, match="power_estimates.C"):
        specs.estimate_c_sourced(vessel, cfg.defaults)


def test_build_estimates_returns_only_configured_ones(vessel, cfg) -> None:
    built = specs.build_estimates(vessel, cfg)
    assert set(built) == {"A", "B"}


def test_requesting_an_unknown_estimate_raises(vessel, cfg) -> None:
    import dataclasses

    broken = dataclasses.replace(cfg, run={**cfg.run, "power_estimates": ["Z"]})
    with pytest.raises(MissingParameter, match="unknown power estimate"):
        specs.build_estimates(vessel, broken)


# ---------------------------------------------------------------------------
# 3 -- fuel
# ---------------------------------------------------------------------------


def test_pilot_hull_is_not_high_speed(vessel) -> None:
    """§3.1 condition 1 never fires for a large slow-speed-diesel ship."""
    assert is_high_speed(vessel) is False


def test_emission_factors_resolve(cfg) -> None:
    assert emission_factor(cfg.factors, "HFO") == 3.114
    assert emission_factor(cfg.factors, "MDO") == 3.206


def test_unknown_fuel_raises(cfg) -> None:
    with pytest.raises(ConfigError, match="no emission factor"):
        emission_factor(cfg.factors, "kerosene")


def test_sfc_lookups_resolve(cfg) -> None:
    assert sfc_base(cfg.factors, "SSD", "HFO") == 175
    assert sfc_base(cfg.factors, "auxiliary_engine", "MDO") == 185
    assert sfc_base(cfg.factors, "boiler", "HFO") == 340


def test_unknown_engine_raises(cfg) -> None:
    with pytest.raises(ConfigError, match="no SFC row"):
        sfc_base(cfg.factors, "steam_donkey", "HFO")


def test_build_year_guard_accepts_a_2014_hull(vessel, cfg) -> None:
    assert_build_year_in_range(vessel, cfg.factors)


def test_build_year_guard_rejects_a_pre_2001_hull(vessel, cfg) -> None:
    """Only the 2001+ SFC column is transcribed; older hulls would be understated."""
    import dataclasses

    from emissions_allocation.config import Parameter

    old = dataclasses.replace(
        vessel,
        specs={**vessel.specs, "year_built": Parameter(name="year_built", value=1995)},
    )
    with pytest.raises(ConfigError, match="2001"):
        assert_build_year_in_range(old, cfg.factors)


def test_eca_polygon_list_excludes_the_mediterranean(cfg) -> None:
    """In force May 2025, after the study period. Its absence must stay deliberate."""
    areas = cfg.factors["fuel_assignment"]["eca"]["polygons"]
    assert len(areas) == 6
    assert not any("editerranean" in a for a in areas)
    assert cfg.factors["fuel_assignment"]["eca"]["mediterranean_included"] is False


def test_fuel_assignment_rule_has_three_conditions(cfg) -> None:
    conditions = cfg.factors["fuel_assignment"]["distillate_when"]
    assert set(conditions) == {
        "main_engine_is_high_speed", "inside_eca", "voyage_leg_is_eu_to_eu"
    }


# ---------------------------------------------------------------------------
# §4.1 -- At berth vs Anchored, via port-visit intervals
# ---------------------------------------------------------------------------


def test_port_visit_interval_rule_is_enabled(cfg) -> None:
    """Table 16 splits berth from anchor by distance because the IMO study had no
    better signal. A GFW port-visit event is a better one, from another endpoint."""
    assert cfg.run["use_port_visit_intervals"] is True


def test_at_berth_uses_port_visit_intervals(cfg) -> None:
    """A stationary hour inside a port visit is At berth however far the nearest
    anchorage COORDINATE happens to be -- GFW anchorage points sit off the berth."""
    import pandas as pd

    from emissions_allocation.db import Database

    ts = pd.Timestamp("2024-01-11 06:00")
    with Database() as db:
        db.register_frame("hour_load", pd.DataFrame({
            "imo": ["9516454"], "ts": [ts], "lat": [33.75], "lon": [-118.20],
            "sog": [0.2], "me_load": [0.0],
        }))
        # Far from any anchorage point, but inside a port-visit interval.
        db.register_frame("position_distance", pd.DataFrame({
            "lat": [33.75], "lon": [-118.20], "port_nm": [4.2], "coast_nm": [0.8],
        }))
        db.register_frame("port_visit_hour", pd.DataFrame({
            "imo": ["9516454"], "ts": [ts],
            "in_port_visit": [True], "visit_at_dock": [True],
        }))
        mode = db.sql(
            "40_operating_mode", is_liquid_tanker=False, use_port_visit_intervals=True
        ).df()["operating_mode"].iloc[0]
    assert mode == "at_berth"


def test_strict_table_16_still_reads_anchored(cfg) -> None:
    """The flag genuinely switches behaviour, so the two can be compared."""
    import pandas as pd

    from emissions_allocation.db import Database

    ts = pd.Timestamp("2024-01-11 06:00")
    with Database() as db:
        db.register_frame("hour_load", pd.DataFrame({
            "imo": ["9516454"], "ts": [ts], "lat": [33.75], "lon": [-118.20],
            "sog": [0.2], "me_load": [0.0],
        }))
        db.register_frame("position_distance", pd.DataFrame({
            "lat": [33.75], "lon": [-118.20], "port_nm": [4.2], "coast_nm": [0.8],
        }))
        db.register_frame("port_visit_hour", pd.DataFrame({
            "imo": ["9516454"], "ts": [ts],
            "in_port_visit": [True], "visit_at_dock": [True],
        }))
        mode = db.sql(
            "40_operating_mode", is_liquid_tanker=False, use_port_visit_intervals=False
        ).df()["operating_mode"].iloc[0]
    assert mode == "anchored"


def test_at_berth_draws_less_auxiliary_than_anchored(cfg) -> None:
    """Why the classification matters: 1,300 kW against 1,800 kW."""
    modes = cfg.factors["auxiliary_boiler_power"]["modes"]
    band = next(
        b for b in cfg.factors["auxiliary_boiler_power"]["ship_types"]["container"]["bands"]
        if b["min"] == 12000
    )
    assert band["auxiliary"][modes.index("at_berth")] == 1300
    assert band["auxiliary"][modes.index("anchored")] == 1800

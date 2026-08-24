"""§2 specifications and §3 fuel assignment.

The §2 tests pin the implemented estimates to the figures in docs/METHODOLOGY.md §2.2.
Estimate A returns 25.55 kn once MEPC.333(76)'s containership caps are applied. That
remains above the 24.5 kn observed-fleet maximum and is asserted as a documented
limitation rather than hidden by an uncapped regression.
"""

from __future__ import annotations

import pytest

from emissions_allocation import specs
from emissions_allocation.config import ConfigError, MissingParameter, load_config
from emissions_allocation.fuel import (
    allocate_and_infill_imo_main_fuel,
    allocate_imo_main_fuel,
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


def test_both_containership_caps_bind_for_vessel_a(vessel, cfg) -> None:
    """MEPC.333(76) caps the containership capacity parameter twice, differently:
    80,000 DWT for speed and 95,000 DWT for power. Vessel A is 156,610 DWT so both
    bind, giving the figures docs/METHODOLOGY.md §2.2 states."""
    estimate = specs.estimate_a_eexi(vessel, cfg.defaults, cfg)
    assert estimate.variants["capacity_speed"] == 80_000
    assert estimate.variants["capacity_power"] == 95_000
    assert estimate.design_speed_kn == pytest.approx(25.55, abs=0.01)
    assert estimate.mcr_kw == pytest.approx(67_912, rel=0.001)
    assert estimate.load_at_reference == pytest.approx(0.75)
    assert estimate.speed_exponent == pytest.approx(3.0)


def test_uncapped_values_reproduce_the_documented_error(vessel, cfg) -> None:
    """Uncapped gives 28.89 kn / 113,673 kW -- a 1.6x error in installed power, and
    the reason an earlier reading concluded the method fails for large boxships."""
    s = cfg.eexi["speed"]["containership"]; p = cfg.eexi["power"]["containership"]
    dwt = vessel.require_spec("dwt")
    assert s["A"] * dwt ** s["C"] == pytest.approx(28.89, abs=0.02)
    assert p["D"] * dwt ** p["F"] == pytest.approx(113_673, rel=0.005)


def test_methodology_validation_examples_reproduce(cfg) -> None:
    """§2.2's own worked examples, as an independent check on the tables."""
    s, p = cfg.eexi["speed"], cfg.eexi["power"]
    assert s["bulk_carrier"]["A"] * 80_000 ** s["bulk_carrier"]["C"] == pytest.approx(14.46, abs=0.02)
    assert p["bulk_carrier"]["D"] * 80_000 ** p["bulk_carrier"]["F"] == pytest.approx(10_672, rel=0.01)
    assert s["tanker"]["A"] * 300_000 ** s["tanker"]["C"] == pytest.approx(16.08, abs=0.05)


def test_all_twelve_types_have_both_speed_and_power(cfg) -> None:
    assert set(cfg.eexi["speed"]) == set(cfg.eexi["power"])
    assert len(cfg.eexi["speed"]) == 12


def test_cruise_row_uses_gt_not_dwt(cfg) -> None:
    """Passing deadweight for that row silently returns a wrong number."""
    assert cfg.eexi["speed"]["cruise"]["capacity"] == "gt"
    assert cfg.eexi["power"]["cruise"]["capacity"] == "gt"


def test_unmapped_ship_type_raises(cfg) -> None:
    """§2.4 rule 3: no falling through to a default."""
    with pytest.raises(MissingParameter, match="does not map"):
        cfg.eexi_type("submarine")


def test_estimate_a_fails_the_fleet_envelope(vessel, cfg) -> None:
    """25.55 kn exceeds the 24.5 kn maximum across 215 designs built since 2015.

    This is a REPORTED RESULT. If this test ever starts failing because the estimate
    moved inside the envelope, the finding has been hidden, not fixed.
    """
    assert specs.estimate_a_eexi(vessel, cfg.defaults, cfg).within_fleet_envelope is False


def test_estimate_a_is_marked_estimated(vessel, cfg) -> None:
    assert specs.estimate_a_eexi(vessel, cfg.defaults, cfg).estimated is True


def test_speed_constants_cover_twelve_ship_types(cfg) -> None:
    speed = cfg.eexi["speed"]
    assert len(speed) == 12
    assert {"bulk_carrier", "tanker", "vehicle", "ro_ro", "lng_carrier"} <= set(speed)


def test_vehicle_carrier_speed_resolves(cfg) -> None:
    """RCC AMERICA, 21,182 DWT -> ~20 kn, plausible for a car carrier."""
    row = cfg.eexi["speed"]["vehicle"]
    assert row["A"] * 21182 ** row["C"] == pytest.approx(19.96, abs=0.02)


def test_vehicle_carrier_fully_resolves(cfg) -> None:
    """RCC AMERICA, 21,182 DWT -- both speed and power now available."""
    s, p = cfg.eexi["speed"]["vehicle"], cfg.eexi["power"]["vehicle"]
    assert s["A"] * 21182 ** s["C"] == pytest.approx(19.96, abs=0.02)
    assert p["D"] * 21182 ** p["F"] == pytest.approx(14_086, rel=0.01)


def test_eexi_power_is_nearly_linear_in_deadweight(cfg) -> None:
    """D = 1.030 is why the curve fit breaks at the top of the container range."""
    assert cfg.eexi["power"]["containership"]["F"] == pytest.approx(1.03046)


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


def test_estimates_agree_on_installed_power_but_not_on_speed(vessel, cfg) -> None:
    """The corrected EEXI power (67,912 kW) sits just below Estimate B's Admiralty
    range (69,600-85,100 kW) -- close agreement, as §2.2 says.

    But they disagree on DESIGN SPEED, 25.55 against 22.62 kn, and load goes as
    1/V^3. Agreeing on installed power is therefore not enough for the emissions to
    agree: the 1.44x speed-cubed ratio is what drives them apart.
    """
    a = specs.estimate_a_eexi(vessel, cfg.defaults, cfg)
    b = specs.estimate_b_admiralty(vessel, cfg.defaults)
    assert a.mcr_kw / b.mcr_kw == pytest.approx(0.87, abs=0.03)
    assert (a.design_speed_kn / b.design_speed_kn) ** 3 == pytest.approx(1.44, abs=0.03)


def test_admiralty_relation_scales_as_speed_cubed() -> None:
    single = specs.admiralty_power_kw(180_000, 11.0, 482)
    double = specs.admiralty_power_kw(180_000, 22.0, 482)
    assert double / single == pytest.approx(8.0)


def test_froude_speed_matches_a_hand_calculation() -> None:
    """Fn 0.21 at L_BP 345 m -> 23.75 kn."""
    assert specs.froude_speed_kn(0.21, 345.0) == pytest.approx(23.75, abs=0.05)


def test_c_adm_calibration_sits_in_the_textbook_band(cfg) -> None:
    c_adm = cfg.defaults["admiralty"]["by_ship_type"]["container"]["c_adm"]
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


# ---------------------------------------------------------------------------
# 2.2 -- estimate D
# ---------------------------------------------------------------------------


def test_estimate_d_epa_regression_matches_the_worked_value(vessel, cfg) -> None:
    """EPA's container regression gives 124,539 hp = 92,868 kW for vessel A."""
    estimate = specs.estimate_d_epa_dwt(vessel, cfg.defaults)
    assert estimate.mcr_kw == pytest.approx(92_868, abs=1)
    assert estimate.variants["epa_main_engine_hp"] == pytest.approx(124_539, abs=1)
    assert estimate.variants["epa_extrapolated"] is True
    assert estimate.design_speed_kn == pytest.approx(22.62, abs=0.02)
    assert estimate.load_at_reference == pytest.approx(0.83)


def test_estimate_d_uses_epa_pooled_vehicle_power_and_eexi_speed(cfg) -> None:
    """RCC AMERICA uses EPA's recommended auto/RoRo pooled MCR regression.

    At 21,182 DWT, ``(0.719 * DWT + 2,581) hp`` is 13,281 kW. EPA does not
    estimate speed, so D deliberately shares estimate A's 19.96 kn EEXI speed.
    """
    vehicle = cfg.vessel("9277802")
    estimate = specs.estimate_d_epa_dwt(vehicle, cfg.defaults, cfg)
    assert estimate.mcr_kw == pytest.approx(13_281, abs=1)
    assert estimate.design_speed_kn == pytest.approx(19.96, abs=0.02)
    assert estimate.load_at_reference == pytest.approx(0.75)
    assert estimate.variants["speed_pairing"] == "eexi"
    assert estimate.variants["epa_extrapolated"] is False
    assert set(specs.build_estimates(vehicle, cfg)) == {"A", "D"}


def test_build_estimates_returns_only_configured_ones(vessel, cfg) -> None:
    built = specs.build_estimates(vessel, cfg)
    assert set(built) == {"A", "B", "D"}


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


@pytest.mark.parametrize(
    ("fuel_1", "fuel_2", "propulsion", "ship_type", "expected"),
    [
        ("Residual Fuel", "Distilled Fuel", "Oil Engine", "Container", "HFO"),
        ("Residual Fuel", "NA", "Steam Turbine", "Liquefied Gas Tanker", "LNG"),
        ("Distilled Fuel", "NA", "Oil Engine", "General Cargo", "MDO"),
        ("Coal", "Distilled Fuel", "Oil Engine", "General Cargo", "MDO"),
        ("Methanol", "Distilled Fuel", "Oil Engine", "Tanker", "Methanol"),
        ("Gas boil-off", "Distilled Fuel", "Steam", "Liquefied Gas Tanker", "LNG"),
        ("Nuclear", "NA", "Nuclear", "Icebreaker", "Nuclear"),
        ("Coal", "NA", "Steam", "Other", "Coal"),
        ("NA", "NA", "Oil Engine", "Container", None),
    ],
)
def test_imo_table_9_main_fuel_allocation(
    fuel_1, fuel_2, propulsion, ship_type, expected,
) -> None:
    """Printed p. 47 Table 9; p. 46 describes the subsequent group infill."""
    assert allocate_imo_main_fuel(fuel_1, fuel_2, propulsion, ship_type) == expected


def test_imo_main_fuel_fallback_infills_only_unresolved_type_size_peers() -> None:
    import pandas as pd

    records = pd.DataFrame({
        "vessel_type": ["Container", "Container", "Container", "Tanker"],
        "size_bin": ["large", "large", "large", "large"],
        "fuel_type_1_first": ["Residual Fuel", "Residual Fuel", "NA", "NA"],
        "fuel_type_2_second": ["NA", "NA", "NA", "NA"],
        "propulsion_type": ["Oil Engine"] * 4,
    })
    result = allocate_and_infill_imo_main_fuel(records)
    assert result["main_fuel"].tolist()[:3] == ["HFO", "HFO", "HFO"]
    assert pd.isna(result["main_fuel"].iloc[3])
    assert result["main_fuel_assignment_method"].tolist()[:3] == [
        "table_9", "table_9", "type_size_mode",
    ]
    assert pd.isna(result["main_fuel_assignment_method"].iloc[3])


def test_imo_main_fuel_fallback_rejects_a_tied_group_mode() -> None:
    import pandas as pd

    records = pd.DataFrame({
        "vessel_type": ["Container", "Container", "Container"],
        "size_bin": ["large"] * 3,
        "fuel_type_1_first": ["Residual Fuel", "Distilled Fuel", "NA"],
        "fuel_type_2_second": ["NA"] * 3,
        "propulsion_type": ["Oil Engine"] * 3,
    })
    with pytest.raises(ConfigError, match="tied modal fuels"):
        allocate_and_infill_imo_main_fuel(records)


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
                "coast_layer_loaded": [True],
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
                "coast_layer_loaded": [True],
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



# ---------------------------------------------------------------------------
# Multi-type support -- the pipeline must price ANY hull, or say why not
# ---------------------------------------------------------------------------


def _hull(base, ship_type, dwt, gt):
    import dataclasses

    from emissions_allocation.config import Parameter

    return dataclasses.replace(base, specs={
        **base.specs,
        "ship_type": Parameter("ship_type", ship_type),
        "dwt": Parameter("dwt", dwt),
        "gt": Parameter("gt", gt),
    })


@pytest.mark.parametrize("ship_type,dwt,gt", [
    ("container", 156610, 154592), ("vehicle", 21182, 57718),
    ("bulk_carrier", 80000, 45000), ("oil_tanker", 300000, 160000),
    ("cruise", 12000, 120000), ("ferry_ropax", 9000, 25000),
    ("general_cargo", 12000, 9000), ("ro_ro", 15000, 25000),
])
def test_estimate_a_resolves_for_every_ship_type(vessel, cfg, ship_type, dwt, gt) -> None:
    """All twelve MEPC.333(76) categories, so a hull is a config lookup."""
    estimate = specs.estimate_a_eexi(_hull(vessel, ship_type, dwt, gt), cfg.defaults, cfg)
    assert 5 < estimate.design_speed_kn < 40
    assert estimate.mcr_kw > 0


def test_table17_size_basis_follows_the_table_not_an_assumption(vessel, cfg) -> None:
    """Container ships are indexed by TEU, most types by DWT, and ferries, cruise
    ships and yachts by GT. Passing deadweight to a GT-indexed row lands in the
    wrong band and returns a plausible wrong number."""
    assert specs.size_for_table17(_hull(vessel, "container", 156610, 154592), cfg)[2] == "TEU"
    assert specs.size_for_table17(_hull(vessel, "vehicle", 21182, 57718), cfg)[1] == 21182
    ship_type, size, unit = specs.size_for_table17(_hull(vessel, "cruise", 12000, 120000), cfg)
    assert (unit, size) == ("gt", 120000)


def test_table17_gas_carriers_use_configured_cargo_volume(vessel, cfg) -> None:
    """Table 17's gas-carrier bands are cubic metres, never deadweight."""
    import dataclasses

    from emissions_allocation.config import Parameter

    gas = dataclasses.replace(
        _hull(vessel, "liquefied_gas_tanker", 70_000, 50_000),
        specs={
            **_hull(vessel, "liquefied_gas_tanker", 70_000, 50_000).specs,
            "cbm_capacity": Parameter("cbm_capacity", 90_000),
        },
    )
    assert specs.size_for_table17(gas, cfg) == ("liquefied_gas_tanker", 90_000, "cbm")


def test_table17_gas_carriers_reject_missing_cargo_volume(vessel, cfg) -> None:
    gas = _hull(vessel, "liquefied_gas_tanker", 70_000, 50_000)
    with pytest.raises(MissingParameter, match="cbm_capacity"):
        specs.size_for_table17(gas, cfg)


def test_estimate_b_raises_for_uncalibrated_hull_forms(vessel, cfg) -> None:
    """C_adm, the block coefficient and the Froude range are hull-form specific.

    Charchalis published container ships only. Borrowing those numbers for a car
    carrier would return a confident wrong figure -- the failure the three-estimate
    design exists to expose, not to commit.
    """
    for ship_type in ("vehicle", "bulk_carrier", "oil_tanker", "cruise"):
        with pytest.raises(MissingParameter, match="no Admiralty calibration"):
            specs.estimate_b_admiralty(_hull(vessel, ship_type, 20000, 50000), cfg.defaults)


def test_fleet_envelope_is_unknown_rather_than_false_off_type(vessel, cfg) -> None:
    """6.0-24.5 kn is the CONTAINER fleet's range and says nothing about a ro-ro."""
    assert specs.check_fleet_envelope(20.0, cfg.defaults, "container") is True
    assert specs.check_fleet_envelope(30.0, cfg.defaults, "container") is False
    assert specs.check_fleet_envelope(20.0, cfg.defaults, "vehicle") is None


def test_unknown_ship_type_raises_at_table_17(vessel, cfg) -> None:
    with pytest.raises(MissingParameter, match="Table 17 has no rows"):
        specs.size_for_table17(_hull(vessel, "submarine", 5000, 5000), cfg)


def test_liquid_tanker_list_comes_from_config(cfg) -> None:
    """Table 16's tanker-only column, as data rather than a hardcoded tuple."""
    types = cfg.factors["operating_mode_matrix"]["liquid_tanker_types"]
    assert "oil_tanker" in types and "container" not in types

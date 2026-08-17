"""Configuration loading and validation.

Ground rules 1, 5 and 6: no vessel-specific values in code, every estimated
parameter carries provenance, and a missing parameter raises rather than defaulting.
"""

from __future__ import annotations

import pytest

from emissions_allocation.config import (
    ConfigError,
    MissingParameter,
    Parameter,
    load_config,
)

VESSEL_A = "9516454"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_config_loads(cfg) -> None:
    assert cfg.vessels


def test_study_period_matches_the_methodology(cfg) -> None:
    assert cfg.start_date.isoformat() == "2017-01-01"
    assert cfg.end_date.isoformat() == "2024-12-31"
    assert cfg.years == [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]


def test_elapsed_hours_matches_the_documented_total(cfg) -> None:
    """§1: 70,128 vessel-hours maximum over the eight-year period."""
    assert cfg.elapsed_hours == 70128


def test_year_range_is_end_exclusive(cfg) -> None:
    assert cfg.year_range(2024) == ("2024-01-01", "2025-01-01")


# ---------------------------------------------------------------------------
# Vessels
# ---------------------------------------------------------------------------


def test_vessel_a_is_present(cfg) -> None:
    assert cfg.vessel(VESSEL_A).label == "A"


def test_vessel_a_hull_dimensions(cfg) -> None:
    vessel = cfg.vessel(VESSEL_A)
    assert vessel.require_spec("dwt") == 156610
    assert vessel.require_spec("beam_m") == 51.20
    assert vessel.require_spec("ship_type") == "container"


def test_unknown_imo_raises_with_the_known_list(cfg) -> None:
    with pytest.raises(ConfigError, match="not in config/pilot.yaml"):
        cfg.vessel("0000000")


def test_shipnames_are_configured(cfg) -> None:
    """The presence filter matches on name; an empty list would silently return zero rows."""
    assert cfg.vessel(VESSEL_A).shipnames == ("COSCO ITALY",)


def test_fleet_iteration_is_the_scaling_path(cfg) -> None:
    """Ground rule 5: scaling to the fleet is a loop over the config, not an edit."""
    assert [v.imo for v in cfg] == [v.imo for v in cfg.vessels]


def test_vessel_b_is_selected(cfg) -> None:
    """Open item 1, closed. RCC AMERICA, selected by §0.2 and confirmed in Equasis."""
    vessel = cfg.vessel("9277802")
    assert vessel.label == "B"
    assert vessel.require_spec("ship_type") == "vehicle"
    assert vessel.require_spec("dwt") == 21182


def test_vessel_b_narrows_its_power_estimates(cfg) -> None:
    """No Admiralty calibration exists for a vehicle carrier, so 'B' is excluded in
    config rather than left to raise mid-run."""
    assert cfg.vessel("9277802").resolve_power_estimates(cfg.run["power_estimates"]) == ["A"]
    assert cfg.vessel(VESSEL_A).resolve_power_estimates(cfg.run["power_estimates"]) == ["A", "B"]


def test_vessel_b_has_no_invented_hull_dimensions(cfg) -> None:
    """LOA, beam and draught feed estimate B and the TEU inversion, neither of which
    applies to this hull. Recording guesses would add risk and nothing else."""
    vessel = cfg.vessel("9277802")
    for name in ("loa_m", "beam_m", "draught_m", "lbp_m"):
        assert name not in vessel.specs


def test_vessel_b_gives_the_divergence_vessel_a_cannot(cfg) -> None:
    """The point of a second hull: flag, owner and manager on three different
    national budgets, where vessel A's four options collapse onto one."""
    from emissions_allocation.allocation import summarise_options

    for treatment in cfg.run["hk_treatments"]:
        row = summarise_options(cfg, treatment).set_index("imo").loc["9277802"]
        assert row["n_distinct_countries"] == 3
        assert not row["is_degenerate"]


# ---------------------------------------------------------------------------
# Provenance -- ground rule 6
# ---------------------------------------------------------------------------


def test_every_estimated_spec_carries_source_and_method(cfg) -> None:
    """Any estimated parameter that HAS a value must say where it came from.

    Unresolved open items are the exception: estimate C is written with a null
    value precisely so it cannot be mistaken for a real number, and demanding
    provenance for a number that does not exist would force a placeholder source
    to be invented.
    """
    for vessel in cfg:
        for name, parameter in vessel.estimated_specs().items():
            if parameter.is_missing:
                continue
            assert parameter.source, f"{name} is estimated but has no source"
            assert parameter.method, f"{name} is estimated but has no method"


def test_open_items_are_null_rather_than_placeheld(cfg) -> None:
    """An unresolved parameter must be absent, not filled with a plausible guess."""
    for vessel in cfg:
        for name, parameter in vessel.estimated_specs().items():
            if parameter.is_missing:
                assert parameter.source is None, (
                    f"{name} has no value but carries a source -- if it was sourced "
                    "it should have a value; if not, the source is fictional"
                )


def test_estimated_parameter_without_source_is_rejected() -> None:
    with pytest.raises(ConfigError, match="source"):
        Parameter(name="mcr_kw", value=113004, estimated=True, method="curve fit")


def test_estimated_parameter_without_method_is_rejected() -> None:
    with pytest.raises(ConfigError, match="method"):
        Parameter(name="mcr_kw", value=113004, estimated=True, source="MEPC.333(76)")


def test_observed_parameter_needs_no_provenance() -> None:
    assert Parameter(name="dwt", value=156610).value == 156610


def test_lbp_is_marked_estimated(cfg) -> None:
    """It is derived as 0.95 x LOA, not published for this hull."""
    assert cfg.vessel(VESSEL_A).spec("lbp_m").estimated is True


def test_engine_type_is_marked_as_assigned(cfg) -> None:
    """§2.3: assigned from the IMO default, not observed."""
    engine = cfg.vessel(VESSEL_A).spec("engine_type")
    assert engine.value == "SSD"
    assert engine.estimated is True


def test_observed_hull_dimensions_are_not_marked_estimated(cfg) -> None:
    for name in ("dwt", "beam_m", "loa_m", "draught_m", "year_built"):
        assert cfg.vessel(VESSEL_A).spec(name).estimated is False


def test_label_marks_estimated_values_visibly(cfg) -> None:
    """A reader should never be unsure whether a number was observed or derived."""
    assert "[estimated]" in cfg.vessel(VESSEL_A).spec("lbp_m").label()
    assert "[estimated]" not in cfg.vessel(VESSEL_A).spec("dwt").label()


# ---------------------------------------------------------------------------
# Missing parameters -- ground rule 1
# ---------------------------------------------------------------------------


def test_estimate_c_is_absent_and_raises_on_use(cfg) -> None:
    """Open item 4. No free source supplies installed power or design speed."""
    vessel = cfg.vessel(VESSEL_A)
    assert vessel.spec("power_C_mcr_kw").is_missing
    with pytest.raises(MissingParameter):
        vessel.require_spec("power_C_mcr_kw")


def test_estimate_c_is_not_in_the_default_scenario_set(cfg) -> None:
    """Listing C without supplying it would raise; it is left out until sourced."""
    assert "C" not in cfg.run["power_estimates"]
    assert cfg.run["power_estimates"] == ["A", "B"]


def test_missing_parameter_message_refuses_to_default(cfg) -> None:
    with pytest.raises(MissingParameter, match="No default is substituted"):
        cfg.vessel(VESSEL_A).require_spec("power_C_design_speed_kn")


def test_coastline_layer_is_configured(cfg) -> None:
    """Open item 3, resolved. §4.1 needs distance-to-coast per vessel-hour."""
    assert cfg.spatial_layer("coastline").exists()
    assert cfg.spatial_inner("coastline").endswith(".shp")


def test_unconfigured_spatial_layer_still_raises_with_guidance(cfg) -> None:
    """The no-default rule holds for any layer that has not been chosen."""
    with pytest.raises(MissingParameter, match="OPEN ITEM"):
        cfg.spatial_layer("bathymetry")


def test_eez_layer_points_at_polygons_not_boundaries(cfg) -> None:
    """The archive holds both; the boundaries would silently match nothing."""
    assert cfg.spatial_inner("eez") == "World_EEZ_v12_20231025_gpkg/eez_v12.gpkg"


def test_configured_spatial_layers_exist(cfg) -> None:
    for key in ("eez", "high_seas", "eca_sox_pm"):
        assert cfg.spatial_layer(key).exists()


def test_bunker_allocation_is_explicitly_not_computable(cfg) -> None:
    """§5.1: out of scope by construction, not by omission."""
    vessel = cfg.vessel(VESSEL_A)
    assert vessel.allocation_country("bunker") is None
    assert "not computable" in vessel.allocation_keys["bunker"]["method"]


# ---------------------------------------------------------------------------
# Allocation keys
# ---------------------------------------------------------------------------


def test_vessel_a_allocation_keys_are_degenerate(cfg) -> None:
    """§5.1: flag is Hong Kong; all three commercial roles resolve to China."""
    vessel = cfg.vessel(VESSEL_A)
    assert vessel.allocation_country("flag") == "HKG"
    assert vessel.allocation_country("owner") == "CHN"
    assert vessel.allocation_country("manager") == "CHN"
    assert vessel.allocation_country("operator") == "CHN"


def test_operator_is_flagged_as_a_proxy(cfg) -> None:
    """§5.2: Equasis has no operator field; commercial manager stands in."""
    operator = cfg.vessel(VESSEL_A).allocation_keys["operator"]
    assert "PROXY" in operator["method"]
    assert operator["estimated"] is True


def test_company_imo_numbers_are_recorded(cfg) -> None:
    """A stable join key for the fleet-scale version, instead of fuzzy name matching."""
    keys = cfg.vessel(VESSEL_A).allocation_keys
    assert keys["owner"]["company_imo"] == "4178111"
    assert keys["manager"]["company_imo"] == "5193283"
    assert keys["operator"]["company_imo"] == "1043944"


def test_unknown_allocation_option_raises(cfg) -> None:
    with pytest.raises(ConfigError, match="allocation key"):
        cfg.vessel(VESSEL_A).allocation_country("charterer")


# ---------------------------------------------------------------------------
# Scenario space -- §8.1
# ---------------------------------------------------------------------------


def test_scenarios_are_the_cross_join(cfg) -> None:
    expected = (
        len(cfg.run["power_estimates"])
        * len(cfg.run["hk_treatments"])
        * len(cfg.run["smoothing_windows"])
    )
    assert len(cfg.scenarios()) == expected


def test_scenario_ids_are_unique(cfg) -> None:
    ids = [s["scenario_id"] for s in cfg.scenarios()]
    assert len(set(ids)) == len(ids)


def test_unsmoothed_baseline_is_carried(cfg) -> None:
    """w=1 is the unsmoothed series that shows the 1.67x v^3 bias."""
    assert 1 in cfg.run["smoothing_windows"]


def test_both_hong_kong_treatments_are_carried(cfg) -> None:
    """§6.4: decisive for vessel A and not obviously correct either way."""
    assert set(cfg.run["hk_treatments"]) == {"separate", "folded_into_china"}


def test_even_smoothing_window_is_rejected(tmp_path) -> None:
    """A centred moving average needs an odd width."""
    import shutil
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "config"
    for name in ("pilot.yaml", "vessel_specs.yaml", "emission_factors.yaml",
                 "eexi_parameters.yaml"):
        shutil.copy(source / name, tmp_path / name)

    pilot = (tmp_path / "pilot.yaml").read_text(encoding="utf-8")
    (tmp_path / "pilot.yaml").write_text(
        pilot.replace("smoothing_windows: [1, 3, 5, 7]", "smoothing_windows: [1, 4]"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must be odd"):
        load_config(tmp_path)

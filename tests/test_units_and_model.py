"""Unit conversions and the load-correction curve.

Two of the checks here exist because they are cheap and catch transcription errors
that would otherwise be invisible in the output:

* The IMO's ``CF_L`` quadratic must minimise at 0.78, matching the study's stated
  ~80% MCR optimum. If a coefficient is mistyped the minimum moves, and nothing
  else in the pipeline would notice.
* The Global Carbon Budget reports million tonnes of *carbon*. Forgetting the 3.664
  factor understates every national baseline by a factor of 3.7, which would make
  every ΔE% figure wrong in the same direction and so look plausible.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACTORS = yaml.safe_load(
    (PROJECT_ROOT / "config" / "emission_factors.yaml").read_text(encoding="utf-8")
)


# ---------------------------------------------------------------------------
# §4.4 -- IMO equation (10), the main-engine load correction
# ---------------------------------------------------------------------------


def cf_l(load: float) -> float:
    """``0.455*Load^2 - 0.710*Load + 1.280`` -- IMO equation (10)'s parenthetic term."""
    c = FACTORS["load_correction"]
    return c["a"] * load**2 + c["b"] * load + c["c"]


def test_coefficients_match_the_source() -> None:
    c = FACTORS["load_correction"]
    assert (c["a"], c["b"], c["c"]) == (0.455, -0.710, 1.280)


def test_curve_minimises_at_0_78_analytically() -> None:
    """``-b / 2a`` = 0.710 / (2 x 0.455) = 0.7802."""
    c = FACTORS["load_correction"]
    assert -c["b"] / (2 * c["a"]) == pytest.approx(0.78, abs=0.001)


def test_curve_minimises_at_0_78_numerically() -> None:
    """Independent of the algebra: scan and find the minimum."""
    grid = [i / 10000 for i in range(1, 10001)]
    assert min(grid, key=cf_l) == pytest.approx(0.78, abs=0.001)


def test_config_records_the_minimum_consistently() -> None:
    c = FACTORS["load_correction"]
    assert c["minimises_at"] == pytest.approx(-c["b"] / (2 * c["a"]))


def test_minimum_matches_the_studys_stated_80_percent_optimum() -> None:
    """The internal check that the coefficients are transcribed correctly."""
    assert 0.75 <= FACTORS["load_correction"]["minimises_at"] <= 0.82


def test_correction_is_a_convex_parabola() -> None:
    assert FACTORS["load_correction"]["a"] > 0
    assert cf_l(0.78) < cf_l(0.4)
    assert cf_l(0.78) < cf_l(1.0)


def test_correction_penalises_low_load() -> None:
    """SFC is highest at low load -- the behaviour the curve exists to capture."""
    assert cf_l(0.1) > cf_l(0.5) > cf_l(0.78)


def test_correction_near_unity_at_the_optimum() -> None:
    """At the optimum the correction is ~1.003 -- SFC is essentially SFC_base there.

    That is the point of the parameterisation: ``SFC_base`` is quoted at the
    efficient load, and ``CF_L`` penalises departures from it in both directions.
    """
    assert cf_l(0.78) == pytest.approx(1.003, abs=0.001)


# ---------------------------------------------------------------------------
# §3.5 / §4.2 -- cutoffs
# ---------------------------------------------------------------------------


def test_main_engine_cutoff_is_seven_percent() -> None:
    """p.70: below 7% MCR the main engine reports no fuel and no emissions."""
    assert FACTORS["load_correction"]["main_engine_cutoff_load"] == 0.07


def test_co2_low_load_factors_are_unity_at_every_load() -> None:
    """CO2 varies directly with fuel consumption, so no LLF is applied."""
    assert set(FACTORS["low_load_factors"]["CO2"].values()) == {1.00}
    assert FACTORS["low_load_factors"]["applied"] is False


def test_auxiliaries_are_not_load_corrected() -> None:
    """IMO equation (11): ``FC_AE|BO,i = SFC_base * W_AE|BO,i``, no ``CF_L``."""
    assert FACTORS["load_correction"]["aux_boiler_load_corrected"] is False


# ---------------------------------------------------------------------------
# §6.1 -- unit conversions
# ---------------------------------------------------------------------------


def test_mtc_to_mtco2_factor() -> None:
    assert FACTORS["conversions"]["mtc_to_mtco2"]["value"] == 3.664


def test_mtc_to_mtco2_matches_the_molar_mass_ratio() -> None:
    assert 44.009 / 12.011 == pytest.approx(3.664, abs=0.001)


def test_hong_kong_baseline_conversion() -> None:
    """§6.4: 9.09 MtC -> 33.3 MtCO2."""
    assert 9.09 * 3.664 == pytest.approx(33.3, abs=0.05)


def test_china_baseline_conversion() -> None:
    """§6.4: 3,354 MtC -> 12,289 MtCO2."""
    assert 3354 * 3.664 == pytest.approx(12289, rel=0.001)


def test_hong_kong_china_baselines_differ_by_about_370x() -> None:
    """The gap that makes the HK treatment decisive for vessel A."""
    assert (3354 * 3.664) / (9.09 * 3.664) == pytest.approx(369, rel=0.02)


def test_international_shipping_cross_check() -> None:
    """§6.2: GCB's 170.15 MtC for 2024 -> 623 MtCO2."""
    assert 170.15 * 3.664 == pytest.approx(623, abs=1.0)


def test_undata_thousand_tonnes_to_tonnes() -> None:
    """The UNdata unit string is 'Metric tons, thousand'."""
    factor = FACTORS["conversions"]["undata_thousand_tonnes_to_tonnes"]["value"]
    assert factor == 1000
    assert 42.5 * factor == 42500


# ---------------------------------------------------------------------------
# §3.3 -- Table 21 emission factors
# ---------------------------------------------------------------------------


def test_emission_factors_match_table_21() -> None:
    fuels = FACTORS["emission_factors"]["fuels"]
    assert fuels["HFO"]["ef_f"] == 3.114
    assert fuels["MDO"]["ef_f"] == 3.206
    assert fuels["LNG"]["ef_f"] == 2.750
    assert fuels["Methanol"]["ef_f"] == 1.375


def test_low_sulphur_hfo_carries_the_same_factor_as_hfo() -> None:
    """§3.2: the IMO 2020 sulphur cap is immaterial to CO2."""
    fuels = FACTORS["emission_factors"]["fuels"]
    assert fuels["LSHFO_1.0"]["ef_f"] == fuels["HFO"]["ef_f"]
    assert fuels["LSHFO_1.0"]["carbon_content"] == fuels["HFO"]["carbon_content"]


@pytest.mark.parametrize("fuel", ["HFO", "MDO", "LNG", "Methanol", "LSHFO_1.0"])
def test_emission_factor_is_consistent_with_carbon_content(fuel: str) -> None:
    """``EF_f`` should be carbon content x 44.009/12.011."""
    spec = FACTORS["emission_factors"]["fuels"][fuel]
    assert spec["carbon_content"] * (44.009 / 12.011) == pytest.approx(
        spec["ef_f"], rel=0.005
    )


# ---------------------------------------------------------------------------
# §3.4 -- Table 19 base SFC
# ---------------------------------------------------------------------------


def test_sfc_base_matches_table_19_for_2001_builds() -> None:
    engines = FACTORS["sfc_base"]["engines"]
    assert engines["SSD"] == {"HFO": 175, "MDO": 165}
    assert engines["auxiliary_engine"]["HFO"] == 195
    assert engines["auxiliary_engine"]["MDO"] == 185
    assert engines["boiler"]["HFO"] == 340
    assert engines["boiler"]["MDO"] == 320


def test_distillate_burns_less_than_residual_per_kwh() -> None:
    engines = FACTORS["sfc_base"]["engines"]
    for engine in ("SSD", "auxiliary_engine", "boiler"):
        assert engines[engine]["MDO"] < engines[engine]["HFO"]


# ---------------------------------------------------------------------------
# §4.3 -- Table 17
# ---------------------------------------------------------------------------


def _band(ship_type: str, size: float) -> dict:
    spec = FACTORS["auxiliary_boiler_power"]["ship_types"][ship_type]
    for band in spec["bands"]:
        if band["min"] <= size and (band["max"] is None or size <= band["max"]):
            return band
    raise AssertionError(f"no {ship_type} band covers {size}")


def test_vessel_a_falls_in_the_12000_14499_teu_band() -> None:
    """13,174 TEU. Values quoted in §4.3."""
    band = _band("container", 13174)
    assert (band["min"], band["max"]) == (12000, 14499)
    assert band["boiler"] == [630, 630, 630, 0]
    assert band["auxiliary"] == [1300, 1800, 3250, 2050]


def test_boiler_output_is_zero_at_sea_for_containerships() -> None:
    modes = FACTORS["auxiliary_boiler_power"]["modes"]
    assert modes == ["at_berth", "anchored", "manoeuvring", "sea"]
    for band in FACTORS["auxiliary_boiler_power"]["ship_types"]["container"]["bands"]:
        assert band["boiler"][modes.index("sea")] == 0


def test_anchored_draws_more_auxiliary_than_at_berth() -> None:
    """Why the berth/anchored distinction matters: 1,300 vs 1,800 kW."""
    modes = FACTORS["auxiliary_boiler_power"]["modes"]
    band = _band("container", 13174)
    assert band["auxiliary"][modes.index("anchored")] > band["auxiliary"][modes.index("at_berth")]


def test_bands_are_contiguous_and_non_overlapping() -> None:
    for ship_type, spec in FACTORS["auxiliary_boiler_power"]["ship_types"].items():
        bands = spec["bands"]
        for lower, upper in zip(bands, bands[1:]):
            assert lower["max"] is not None, f"{ship_type}: only the last band may be open"
            assert upper["min"] == lower["max"] + 1, (
                f"{ship_type}: gap or overlap between {lower['max']} and {upper['min']}"
            )
        assert bands[-1]["max"] is None, f"{ship_type}: last band must be open-ended"


def test_every_band_has_a_value_for_every_mode() -> None:
    n = len(FACTORS["auxiliary_boiler_power"]["modes"])
    for spec in FACTORS["auxiliary_boiler_power"]["ship_types"].values():
        for band in spec["bands"]:
            assert len(band["boiler"]) == n
            assert len(band["auxiliary"]) == n


def test_small_ship_overrides_do_not_apply_to_either_pilot_hull() -> None:
    """Both thresholds sit far below any large merchant hull's installed power."""
    overrides = FACTORS["auxiliary_boiler_power"]["overrides"]
    assert overrides[0]["mcr_max"] == 150
    assert overrides[1]["mcr_max"] == 500


# ---------------------------------------------------------------------------
# §4.1 -- Table 16
# ---------------------------------------------------------------------------


def test_port_1_to_5_column_is_restricted_to_liquid_tankers() -> None:
    """The footnote docs/METHODOLOGY.md §4.1 drops.

    Liquid tankers are often lightered offshore and so can berth within 5 nm of
    port. Applying that column to a container ship would let vessel A be "At berth"
    up to 5 nm out, understating auxiliary demand.
    """
    columns = FACTORS["operating_mode_matrix"]["columns"]
    by_key = {c["key"]: c for c in columns}
    assert by_key["port_1_5"]["applies_to"] == "liquid_tankers_only"
    assert by_key["port_le_1"]["applies_to"] == "all"
    for key in ("coast_le_1", "coast_1_5", "coast_ge_5"):
        assert by_key[key]["applies_to"] == "all"


def test_container_is_not_a_liquid_tanker_type() -> None:
    assert "container" not in FACTORS["operating_mode_matrix"]["liquid_tanker_types"]


def test_speed_bands_partition_the_range_without_gaps() -> None:
    rows = FACTORS["operating_mode_matrix"]["rows"]
    edges = sorted({r.get("sog_min") for r in rows if r.get("sog_min") is not None})
    assert edges == [1.0, 3.0, 5.0]
    assert rows[0].get("sog_min") is None and rows[0]["sog_max"] == 1.0


def test_every_matrix_row_covers_all_five_columns() -> None:
    n = len(FACTORS["operating_mode_matrix"]["columns"])
    for row in FACTORS["operating_mode_matrix"]["rows"]:
        assert len(row["modes"]) == n


def test_matrix_uses_only_the_five_defined_phases() -> None:
    allowed = {"at_berth", "anchored", "manoeuvring", "slow_transit", "normal_cruising"}
    for row in FACTORS["operating_mode_matrix"]["rows"]:
        assert set(row["modes"]) <= allowed


def test_stationary_vessels_are_never_cruising() -> None:
    for row in FACTORS["operating_mode_matrix"]["rows"]:
        if row.get("sog_max") is not None and row["sog_max"] <= 3.0:
            assert set(row["modes"]) <= {"at_berth", "anchored"}

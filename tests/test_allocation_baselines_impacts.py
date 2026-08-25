"""§5 allocation, §6 baselines and §7 impacts under the paper's country map."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from emissions_allocation import baselines, impacts
from emissions_allocation.allocation import (
    ALLOCATION_OPTIONS,
    allocate,
    domestic_test,
    international_emissions_year,
    summarise_options,
    vessel_key_table,
)
from emissions_allocation.config import ConfigError, load_config
from emissions_allocation.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELIN_SUPP = PROJECT_ROOT / "data" / "external" / "paper" / "erlabec02supp2.xls"
VESSEL_A = "9516454"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def gcb(cfg):
    return baselines.build_baselines(cfg)


@pytest.fixture(scope="module")
def synthetic_emissions(cfg):
    return pd.DataFrame([
         {"imo": VESSEL_A, "year": year, "scenario_id": s["scenario_id"],
          "power_estimate": s["power_estimate"], "smoothing_window": s["smoothing_window"],
         "co2_tonnes": 100_000.0, "international_hour_share": 1.0,
         "unallocated_hours": 0}
        for year in (2023, 2024) for s in cfg.scenarios()
    ])


def test_gcb_units_and_coverage(gcb) -> None:
    row = gcb[(gcb["country"] == "China") & (gcb["year"] == 2024)].iloc[0]
    assert row["mtco2"] / row["mtc"] == pytest.approx(3.664)
    assert set(gcb["year"]) == set(range(2017, 2025))


def test_missing_country_raises_rather_than_returning_zero(gcb) -> None:
    with pytest.raises(ConfigError, match="no Global Carbon Budget baseline"):
        baselines.national_baseline(gcb, "Atlantis", 2024)


def test_supplementary_table_is_the_alignment_source() -> None:
    frame = pd.read_excel(SELIN_SUPP, sheet_name="All")
    names = set(frame["Name_ref"].dropna())
    assert len(frame) == 199
    assert "Hong Kong" not in names
    assert "Chinese Taipei" in names
    assert frame.loc[frame["Name_ref"] == "Chinese Taipei", "national_ref"].iloc[0] == "Taiwan"


def test_hong_kong_fixed_to_china_and_taiwan_retained(cfg) -> None:
    assert cfg.resolve_territory("Hong Kong") == "China"
    assert cfg.resolve_territory("Taiwan") == "Taiwan"


def test_mapping_targets_are_published_paper_countries(cfg) -> None:
    paper_countries = set(pd.read_excel(SELIN_SUPP, sheet_name="All")["Name_ref"].dropna())
    paper_countries.add("Taiwan")  # Published as Chinese Taipei; national_ref is Taiwan.
    assert set(cfg.territory_alignment["merge_into"].values()) <= paper_countries


def test_vessel_keys_use_the_fixed_paper_alignment(cfg) -> None:
    table = vessel_key_table(cfg)
    flag = table[(table["imo"] == VESSEL_A) & (table["option"] == "flag")].iloc[0]
    assert flag["country"] == "HKG"
    assert flag["gcb_name"] == "China"
    assert summarise_options(cfg).set_index("imo").loc[VESSEL_A, "is_degenerate"]


def test_allocation_and_impacts_have_no_hong_kong_axis(cfg, gcb, synthetic_emissions) -> None:
    with Database() as db:
        allocation = allocate(db, cfg, synthetic_emissions)
        result = impacts.compute_impacts(db, allocation, gcb)
    assert "hk_treatment" not in allocation.columns
    assert "hk_treatment" not in result.columns
    assert set(allocation["option"]) == set(ALLOCATION_OPTIONS)
    assert result["baseline_mt"].notna().all()


def test_allocation_covers_every_scenario(cfg, synthetic_emissions) -> None:
    with Database() as db:
        allocation = allocate(db, cfg, synthetic_emissions)
    flag_2024 = allocation[(allocation["option"] == "flag") & (allocation["year"] == 2024)]
    assert len(flag_2024) == len(cfg.scenarios())


def test_allocation_rejects_total_emissions(cfg, synthetic_emissions) -> None:
    with Database() as db, pytest.raises(ValueError, match="international-emissions"):
        allocate(db, cfg, synthetic_emissions.drop(columns=["international_hour_share"]))


def test_domestic_test_uses_all_active_signals_and_vessel_disputes(cfg) -> None:
    """High-seas signals remain in Selin's denominator and disputes are vessel-wide."""
    timestamps = pd.date_range("2024-01-01", periods=10, freq="h")
    vessel_hour = pd.DataFrame({
        "imo": [VESSEL_A] * len(timestamps),
        "ts": timestamps,
        "is_inactive": [False] * len(timestamps),
    })
    eez_hour = pd.DataFrame({
        "imo": [VESSEL_A] * len(timestamps),
        "ts": timestamps,
        "eez_iso3": ["AAA"] * 4 + ["BBB"] * 2 + [None] * 4,
        "is_disputed": [False] * 4 + [True] * 2 + [False] * 4,
    })
    with Database(spatial=False) as db:
        db.register_frame("vessel_hour", vessel_hour)
        db.register_frame("eez_hour", eez_hour)
        result = domestic_test(db, cfg).iloc[0]

    assert result["dominant_eez_iso3"] == "AAA"
    assert result["dominant_eez_hours"] == 4
    assert result["active_hours_total"] == 10
    assert result["hours_in_any_eez"] == 6
    assert result["hours_disputed"] == 2
    assert result["dominant_eez_share"] == pytest.approx(0.4)
    assert not bool(result["is_domestic"])
    assert bool(result["is_international"])


def test_voyage_allocation_includes_destination_call_and_splits_boundaries(cfg) -> None:
    """Destination-port hours retain the preceding voyage's country label."""
    timestamps = pd.date_range("2024-01-01", periods=11, freq="h")
    hourly = pd.DataFrame({
        "imo": VESSEL_A,
        "ts": timestamps,
        "scenario_id": "eexi_w3",
        "power_estimate": "eexi",
        "smoothing_window": 3,
        "gap_treatment": "linear_coverage",
        "co2_tonnes": 10.0,
    })
    legs = pd.DataFrame([
        {
            "imo": VESSEL_A, "depart_ts": timestamps[2],
            "arrive_ts": timestamps[4], "label_end_ts": timestamps[6],
            "is_international": True,
        },
        {
            "imo": VESSEL_A, "depart_ts": timestamps[6],
            "arrive_ts": timestamps[8], "label_end_ts": timestamps[10],
            "is_international": False,
        },
    ])
    coverage = pd.DataFrame([{
        "imo": VESSEL_A, "year": 2024, "coverage_raw": 1.0,
        "coverage_active": 1.0, "inactive_hours": 0,
    }])

    with Database(spatial=False) as db:
        result = international_emissions_year(db, hourly, legs, coverage, cfg)

    row = result.iloc[0]
    assert row["labelled_hours"] == 8
    assert row["international_hours_direct"] == 4
    assert row["unallocated_hours"] == 3
    assert row["international_hour_share"] == pytest.approx(0.5)
    assert row["international_co2_share"] == pytest.approx(0.5)
    # Four directly international hours plus half of the three boundary hours.
    assert row["co2_tonnes"] == pytest.approx(55.0)


def test_boundary_emissions_use_co2_not_hour_share(cfg) -> None:
    timestamps = pd.date_range("2024-01-01", periods=5, freq="h")
    hourly = pd.DataFrame({
        "imo": VESSEL_A, "ts": timestamps, "scenario_id": "A_w3",
        "power_estimate": "A", "smoothing_window": 3,
        "gap_treatment": "linear", "co2_tonnes": [1.0, 1.0, 9.0, 9.0, 10.0],
    })
    legs = pd.DataFrame([
        {"imo": VESSEL_A, "depart_ts": timestamps[0], "label_end_ts": timestamps[2], "is_international": True},
        {"imo": VESSEL_A, "depart_ts": timestamps[2], "label_end_ts": timestamps[4], "is_international": False},
    ])
    coverage = pd.DataFrame([{"imo": VESSEL_A, "year": 2024, "coverage_raw": 1.0, "coverage_active": 1.0, "inactive_hours": 0}])
    with Database(spatial=False) as db:
        row = international_emissions_year(db, hourly, legs, coverage, cfg).iloc[0]
    assert row["international_hour_share"] == pytest.approx(0.5)
    assert row["international_co2_share"] == pytest.approx(0.1)
    assert row["co2_tonnes"] == pytest.approx(3.0)

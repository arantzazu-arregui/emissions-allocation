"""THETIS-MRV export parsing and comparison (§8.2).

Built against the published export schema. There is no real export in the repo yet,
so these use a synthetic file in that shape -- which means they prove the parser
handles the documented columns and units, not that the portal still emits them. The
parser fails loudly with the actual headings if it does not.
"""

from __future__ import annotations

import pandas as pd
import pytest

from emissions_allocation.validate import parse_thetis_export

VESSEL_A = "9516454"

# Column headings as THETIS-MRV publishes them.
THETIS_COLUMNS = {
    "IMO Number": [VESSEL_A, VESSEL_A, "9999999"],
    "Name": ["COSCO ITALY", "COSCO ITALY", "OTHER SHIP"],
    "Reporting Period": [2023, 2024, 2024],
    "Total CO₂ emissions [m tonnes]": [21500.4, 24880.1, 5000.0],
    "Total fuel consumption [m tonnes]": [6800.0, 7900.0, 1600.0],
}


@pytest.fixture
def export(tmp_path):
    path = tmp_path / "thetis_export.xlsx"
    pd.DataFrame(THETIS_COLUMNS).to_excel(path, index=False)
    return path


def test_parses_the_published_schema(export) -> None:
    out = parse_thetis_export(export, VESSEL_A)
    assert list(out.year) == [2023, 2024]
    assert out.reported_co2_t.tolist() == [21500.4, 24880.1]


def test_filters_to_the_requested_hull(export) -> None:
    """An unfiltered portal export contains the whole fleet."""
    assert len(parse_thetis_export(export, VESSEL_A)) == 2
    assert len(parse_thetis_export(export, "9999999")) == 1


def test_m_tonnes_means_metric_not_million(export) -> None:
    """THETIS labels the column "m tonnes" for METRIC tonnes. Reading it as
    millions would overstate a ship's annual CO2 by a factor of 1e6 and still look
    like a plausible number."""
    out = parse_thetis_export(export, VESSEL_A)
    # A 13,200 TEU container ship's EU-scope CO2 is tens of thousands of tonnes.
    assert out.reported_co2_t.max() < 1e6
    assert out.reported_co2_t.max() > 1e3


def test_csv_export_also_parses(tmp_path) -> None:
    path = tmp_path / "thetis_export.csv"
    pd.DataFrame(THETIS_COLUMNS).to_csv(path, index=False)
    assert len(parse_thetis_export(path, VESSEL_A)) == 2


def test_missing_columns_report_what_was_found(tmp_path) -> None:
    """A schema change must name the actual headings, not fail obscurely."""
    path = tmp_path / "wrong.csv"
    pd.DataFrame({"ship": ["x"], "emissions": [1.0]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="Columns present"):
        parse_thetis_export(path, VESSEL_A)


def test_unknown_imo_returns_empty_rather_than_raising(export) -> None:
    assert parse_thetis_export(export, "1234567").empty


# ---------------------------------------------------------------------------
# MRV scope reconstruction
# ---------------------------------------------------------------------------


def test_uk_leaves_eea_scope_after_the_transition() -> None:
    """The UK was in EU MRV scope through the Brexit transition, to end-2020."""
    from emissions_allocation.validate import eea_countries

    assert "GBR" in eea_countries(2019)
    assert "GBR" in eea_countries(2020)
    assert "GBR" not in eea_countries(2021)
    for year in (2019, 2021):
        assert {"DEU", "NLD", "NOR", "ISL"} <= eea_countries(year)


def test_anchorage_stops_are_not_ports_of_call() -> None:
    """MRV counts a voyage from the last PORT OF CALL, excluding anchorage stops.

    GFW records Suez Canal transits as port visits. Treating them as ports of call
    breaks the Asia-Europe voyage at Suez and counts only the short final hop,
    undercounting scope roughly fourfold.
    """
    from emissions_allocation.validate import mrv_scope_hours

    hours = pd.DataFrame({"ts": pd.date_range("2019-01-01", periods=240, freq="h")})
    calls = pd.DataFrame([
        # A cargo call in Asia, then a Suez ANCHORAGE, then a cargo call in Rotterdam.
        {"start_ts": pd.Timestamp("2019-01-01"), "end_ts": pd.Timestamp("2019-01-02"),
         "port_iso3": "CHN", "at_dock": True},
        {"start_ts": pd.Timestamp("2019-01-05"), "end_ts": pd.Timestamp("2019-01-06"),
         "port_iso3": "EGY", "at_dock": False},
        {"start_ts": pd.Timestamp("2019-01-09"), "end_ts": pd.Timestamp("2019-01-10"),
         "port_iso3": "NLD", "at_dock": True},
    ])
    scope = mrv_scope_hours(hours, calls, 2019)
    # The whole China -> Rotterdam voyage counts, not just the Suez -> Rotterdam hop.
    assert scope.sum() > 24 * 6
    assert scope[hours.ts == pd.Timestamp("2019-01-03")].all()


def test_scope_excludes_voyages_that_never_touch_the_eea() -> None:
    from emissions_allocation.validate import mrv_scope_hours

    hours = pd.DataFrame({"ts": pd.date_range("2019-01-01", periods=240, freq="h")})
    calls = pd.DataFrame([
        {"start_ts": pd.Timestamp("2019-01-01"), "end_ts": pd.Timestamp("2019-01-02"),
         "port_iso3": "CHN", "at_dock": True},
        {"start_ts": pd.Timestamp("2019-01-06"), "end_ts": pd.Timestamp("2019-01-07"),
         "port_iso3": "KOR", "at_dock": True},
    ])
    assert not mrv_scope_hours(hours, calls, 2019).any()


def test_dock_share_is_reported_honestly() -> None:
    """at_dock is a sparse proxy -- 17% for vessel A -- and the check says so."""
    from emissions_allocation.validate import dock_call_share

    calls = pd.DataFrame({"at_dock": [True] * 68 + [False] * 321})
    assert dock_call_share(calls) == pytest.approx(0.175, abs=0.005)


def test_sparse_reconstruction_reports_pending_not_a_ratio(tmp_path) -> None:
    """A scope that cannot be rebuilt must not emit a number that looks like a verdict."""
    from emissions_allocation.config import load_config
    from emissions_allocation.validate import PENDING, compare_thetis_mrv

    cfg = load_config()
    calls = pd.DataFrame({"at_dock": [False] * 100, "start_ts": pd.NaT,
                          "end_ts": pd.NaT, "port_iso3": "CHN"})
    check = compare_thetis_mrv(
        cfg, pd.DataFrame({"year": [2018], "smoothing_window": [3],
                           "power_estimate": ["A"], "co2_tonnes": [1.0]}),
        cfg.vessel(VESSEL_A), pd.DataFrame({"ts": [], "smoothing_window": []}), calls,
    )
    assert check.status == PENDING
    assert "at_dock" in check.detail

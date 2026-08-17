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

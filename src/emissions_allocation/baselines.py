"""§7 -- Global Carbon Budget baselines and unit conversion.

Establishes the national baseline against which allocated emissions are measured.

Two things the file layout makes easy to get wrong. The sheet reports million tonnes
of **carbon**, not CO2 -- forgetting the 3.664 factor understates every baseline by
a factor of 3.7, which would make every dE% figure wrong in the same direction and so
look plausible. And the header sits at row index 11, not row 0; the ``Regions`` sheet
has no header row at all.

National columns already exclude bunker fuels -- only the World total includes them --
so the denominator is clean and adding shipping emissions does not double-count.

Country assignment follows Selin et al.'s supplementary Table 1.  The table has no
Hong Kong row, so the fixed territory map assigns Hong Kong-flagged emissions to China.
"""

from __future__ import annotations

import logging
from pathlib import Path
import pandas as pd

from emissions_allocation.config import Config, ConfigError

log = logging.getLogger(__name__)

# Sheet 'Territorial Emissions', header at row index 11 (0-based). Wide layout:
# rows are years 1850-2024, columns are 232 countries. Units are MtC.
GCB_SHEET = "Territorial Emissions"
GCB_HEADER_ROW = 11
GCB_REGIONS_SHEET = "Regions"

# Carried for the Section 7 cross-check rather than as an input.
SHIPPING_COLUMN = "International Shipping"
WORLD_COLUMN = "World"


def gcb_path(cfg: Config) -> Path:
    path = cfg.path("external") / "gcb" / "National_Fossil_Carbon_Emissions_2025_v0.3.xlsx"
    if not path.exists():
        raise ConfigError(f"Global Carbon Budget workbook not found at {path}")
    return path


def load_gcb(cfg: Config) -> pd.DataFrame:
    """Read Territorial Emissions and convert MtC to Mt CO2.

    Returns:
        Long form: ``country, year, mtc, mtco2``, restricted to the study period.
    """
    factor = cfg.factors["conversions"]["mtc_to_mtco2"]["value"]

    wide = pd.read_excel(gcb_path(cfg), sheet_name=GCB_SHEET, header=GCB_HEADER_ROW)
    wide = wide.rename(columns={wide.columns[0]: "year"})

    long = wide.melt(id_vars="year", var_name="country", value_name="mtc").dropna(
        subset=["mtc"]
    )
    long = long[long["year"].between(cfg.start_date.year, cfg.end_date.year)]
    long["mtco2"] = long["mtc"] * factor
    long["year"] = long["year"].astype(int)
    return long.reset_index(drop=True)


def build_baselines(cfg: Config) -> pd.DataFrame:
    """Return GCB baselines for the study period.

    Territory alignment happens when allocation keys are resolved, not by mutating
    the published GCB country totals.
    """
    return load_gcb(cfg)


def national_baseline(baselines: pd.DataFrame, country: str, year: int) -> float:
    """``B_c`` in Mt CO2 for one country and year.

    Raises:
        ConfigError: If the country is absent. Never returns zero for a missing
            country -- a zero denominator would make dE% infinite, and a silently
            dropped country would vanish from the ranking without trace.
    """
    match = baselines[
        (baselines["country"] == country)
        & (baselines["year"] == year)
    ]
    if match.empty:
        raise ConfigError(
            f"no Global Carbon Budget baseline for {country!r} in {year}.\n"
            "  The GCB keys baselines by country NAME. Check the `gcb_name` on this "
            "vessel's allocation key in config/vessel_specs.yaml against the "
            "workbook's column headings."
        )
    return float(match.iloc[0]["mtco2"])


def shipping_cross_check(cfg: Config, year: int) -> dict[str, float]:
    """Section 7 cross-check using the GCB International Shipping figure.

    An independent estimate of the global total to sanity-check any fleet-scale
    result against. 170.15 MtC for 2024, i.e. 623 Mt CO2. Not an input.
    """
    gcb = load_gcb(cfg)
    row = gcb[(gcb["country"] == SHIPPING_COLUMN) & (gcb["year"] == year)]
    if row.empty:
        raise ConfigError(f"no {SHIPPING_COLUMN!r} column for {year}")
    return {"mtc": float(row.iloc[0]["mtc"]), "mtco2": float(row.iloc[0]["mtco2"])}


def load_regions(cfg: Config) -> dict[str, list[str]]:
    """Groupings from the ``Regions`` sheet -- KP Annex B, OECD, EU27 and continents.

    These are the aggregations Selin et al. report. Note this sheet has **no header
    row**, unlike Territorial Emissions whose header is at index 11.
    """
    raw = pd.read_excel(gcb_path(cfg), sheet_name=GCB_REGIONS_SHEET, header=None)
    return {
        str(row[0]).strip(): [c.strip() for c in str(row[1]).split(",")]
        for _, row in raw.iterrows()
        if pd.notna(row[0]) and pd.notna(row[1])
    }

"""§6 -- Global Carbon Budget baselines, unit conversion, Hong Kong treatments.

Establishes the national baseline against which allocated emissions are measured.

Two things the file layout makes easy to get wrong. The sheet reports million tonnes
of **carbon**, not CO2 -- forgetting the 3.664 factor understates every baseline by
a factor of 3.7, which would make every dE% figure wrong in the same direction and so
look plausible. And the header sits at row index 11, not row 0; the ``Regions`` sheet
has no header row at all.

National columns already exclude bunker fuels -- only the World total includes them --
so the denominator is clean and adding shipping emissions does not double-count.

Hong Kong is computed **both** ways (§6.4). It is not a separate UNFCCC party, and
Selin et al.'s supplementary Table 1 confirms the paper's own treatment: 199 countries
with no Hong Kong row, no Taiwan and no Macao, aligned to the UNFCCC party list. So
``folded_into_china`` is the replication-faithful choice. Both are carried because the
baselines differ by a factor of 369 and, for a Hong Kong-flagged ship with Chinese
owners, that choice decides whether a flag-versus-owner divergence exists at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from emissions_allocation.config import Config, ConfigError

log = logging.getLogger(__name__)

# Sheet 'Territorial Emissions', header at row index 11 (0-based). Wide layout:
# rows are years 1850-2024, columns are 232 countries. Units are MtC.
GCB_SHEET = "Territorial Emissions"
GCB_HEADER_ROW = 11
GCB_REGIONS_SHEET = "Regions"

# Carried for the §6.2 cross-check rather than as an input.
SHIPPING_COLUMN = "International Shipping"
WORLD_COLUMN = "World"

HONG_KONG = "Hong Kong"
CHINA = "China"


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


def apply_hk_treatment(baselines: pd.DataFrame, treatment: str) -> pd.DataFrame:
    """Keep Hong Kong separate, or fold it into China (§6.4).

    Folding sums the two baselines rather than discarding Hong Kong's, so the
    denominator stays a true territorial total under either treatment.
    """
    if treatment == "separate":
        return baselines.assign(hk_treatment=treatment)

    if treatment != "folded_into_china":
        raise ConfigError(
            f"unknown Hong Kong treatment {treatment!r}. "
            "Known: 'separate', 'folded_into_china'."
        )

    folded = baselines.copy()
    folded["country"] = folded["country"].replace({HONG_KONG: CHINA})
    folded = (
        folded.groupby(["country", "year"], as_index=False)[["mtc", "mtco2"]].sum()
    )
    return folded.assign(hk_treatment=treatment)


def build_baselines(cfg: Config) -> pd.DataFrame:
    """Baselines under every configured Hong Kong treatment.

    Returns:
        ``country, year, mtc, mtco2, hk_treatment``.
    """
    raw = load_gcb(cfg)
    return pd.concat(
        [apply_hk_treatment(raw, t) for t in cfg.run["hk_treatments"]],
        ignore_index=True,
    )


def national_baseline(
    baselines: pd.DataFrame, country: str, year: int, treatment: str
) -> float:
    """``B_c`` in Mt CO2 for one country and year.

    Raises:
        ConfigError: If the country is absent. Never returns zero for a missing
            country -- a zero denominator would make dE% infinite, and a silently
            dropped country would vanish from the ranking without trace.
    """
    match = baselines[
        (baselines["country"] == country)
        & (baselines["year"] == year)
        & (baselines["hk_treatment"] == treatment)
    ]
    if match.empty:
        raise ConfigError(
            f"no Global Carbon Budget baseline for {country!r} in {year} under the "
            f"{treatment!r} treatment.\n"
            "  The GCB keys baselines by country NAME. Check the `gcb_name` on this "
            "vessel's allocation key in config/vessel_specs.yaml against the "
            "workbook's column headings."
        )
    return float(match.iloc[0]["mtco2"])


def shipping_cross_check(cfg: Config, year: int) -> dict[str, float]:
    """§6.2 -- the GCB's own International Shipping figure.

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

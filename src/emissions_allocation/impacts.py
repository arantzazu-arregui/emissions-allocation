"""§7 -- absolute and relative additions to national carbon budgets.

Expresses allocated emissions as ``dE`` and ``dE%`` against each baseline, ranks them,
and reports concentration shares.

Rankings and concentration shares are **structurally meaningless at n = 2** -- with
one or two countries per option, every country ranks first or second and the top-20
share is always 1.0. The code path exists, is exercised, and is labelled as such,
because the fleet-scale version is where it becomes informative.

The interpretable outputs at this scale are:

* annual and total CO2 under each power/speed estimate;
* allocation-rule contrasts using the paper-aligned national baselines;
* the spread across scenarios as an explicit uncertainty band.
"""

from __future__ import annotations

import logging

import pandas as pd

from emissions_allocation.db import Database

log = logging.getLogger(__name__)

TOP_N = 20


def compute_impacts(
    db: Database, allocation: pd.DataFrame, baseline: pd.DataFrame
) -> pd.DataFrame:
    """§7.1 -- ``dE``, ``dE%`` and rank, per country per option per scenario."""
    db.register_frame("allocation", allocation)
    db.register_frame("baseline", baseline)
    return db.sql("70_impacts").df()


def region_membership(cfg) -> pd.DataFrame:
    """Country -> region rows from the GCB ``Regions`` sheet (§6.3).

    Many-to-many by nature: Greece is EU27, OECD and KP Annex B at once. Names are
    resolved through the territory map first, so a dependent territory joins under
    the party that carries its baseline.
    """
    from emissions_allocation.baselines import load_regions

    rows = [
        {"region": region, "country": country}
        for region, members in load_regions(cfg).items()
        for country in members
    ]
    return pd.DataFrame(rows).drop_duplicates()


def impacts_by_region(db: Database, cfg, allocation: pd.DataFrame,
                      baseline: pd.DataFrame) -> pd.DataFrame:
    """§6.3 / §7 -- roll impacts up to KP Annex B, OECD, EU27 and the continents.

    These are the aggregations Selin et al. report. A country belongs to several
    groups at once, so totals must not be summed across groups.
    """
    db.register_frame("allocation", allocation)
    db.register_frame("baseline", baseline)
    db.register_frame("region_member", region_membership(cfg))
    return db.sql("71_impacts_by_region").df()


def concentration_share(impacts: pd.DataFrame, top_n: int = TOP_N) -> pd.DataFrame:
    """Share of allocated emissions falling to the top *n* countries.

    Always 1.0 at n = 2, since there are fewer than 20 countries to rank. Reported
    anyway so the fleet-scale path is exercised, and labelled ``is_meaningful`` so a
    reader is not invited to interpret it.
    """
    grouped = impacts.groupby(
        ["option", "scenario_id", "year"], as_index=False
    ).apply(
        lambda g: pd.Series({
            "n_countries": g["country"].nunique(),
            "total_mt": g["delta_e_mt"].sum(),
            "top_n_mt": g.nlargest(top_n, "delta_e_mt")["delta_e_mt"].sum(),
        }),
        include_groups=False,
    )
    grouped["share_top_n"] = grouped["top_n_mt"] / grouped["total_mt"].replace(0, pd.NA)
    grouped["is_meaningful"] = grouped["n_countries"] > top_n
    return grouped


def scenario_spread(impacts: pd.DataFrame) -> pd.DataFrame:
    """§8.1 -- the uncertainty band across the scenario cross join.

    The spread between power/speed estimates is a reported output, not an error to
    be resolved: no free source supplies installed power or design speed, so there
    is no basis for preferring one estimate.
    """
    return impacts.groupby(
        ["option", "country", "year"], as_index=False
    ).agg(
        delta_e_mt_min=("delta_e_mt", "min"),
        delta_e_mt_max=("delta_e_mt", "max"),
        delta_e_mt_median=("delta_e_mt", "median"),
        delta_e_pct_min=("delta_e_pct", "min"),
        delta_e_pct_max=("delta_e_pct", "max"),
        n_scenarios=("scenario_id", "nunique"),
    ).assign(
        spread_ratio=lambda d: d["delta_e_mt_max"] / d["delta_e_mt_min"].replace(0, pd.NA)
    )


def rank_countries(impacts: pd.DataFrame) -> pd.DataFrame:
    """Countries ordered by allocated emissions, per option and scenario."""
    return impacts.sort_values(
        ["option", "scenario_id", "year", "rank_in_option"]
    )



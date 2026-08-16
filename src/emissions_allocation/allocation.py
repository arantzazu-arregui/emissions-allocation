"""§5 -- allocation keys and the domestic/international test.

Attributes annual CO2 to countries under each allocation rule: flag, owner, manager
and operator. The fifth option, bunker fuel, is not computable at this scale -- it
rests on national marine-bunker sales statistics, and allocating one ship's emissions
to a bunkering country would require knowing where it took fuel, which no public
dataset records. Out of scope by construction, not by omission.

At n = 2 the allocation reduces to assigning each vessel's total to one country per
option, but the SQL is written as the general fleet aggregation so scaling needs no
change.

**The contrast is the result.** Vessel A's four options converge on a single country
once Hong Kong folds into China, and 3-to-1 otherwise. A vessel B on an open registry
would diverge by construction. Reporting them side by side is what makes the
comparison interpretable at this scale: it shows that the choice of allocation rule
redistributes responsibility for some ships and not others, and that the ships it
moves are systematically those on open registries.
"""

from __future__ import annotations

import logging

import pandas as pd

from emissions_allocation.config import Config, Vessel
from emissions_allocation.db import Database

log = logging.getLogger(__name__)

# The four computable options. Bunker is deliberately absent -- see the module
# docstring and METHODOLOGY's "Scope and honest limits".
ALLOCATION_OPTIONS = ("flag", "owner", "manager", "operator")

# Selin et al.: domestic if more than 95% of hours lie in a single country's EEZ.
DOMESTIC_THRESHOLD = 0.95


def vessel_key_table(cfg: Config, hk_treatment: str) -> pd.DataFrame:
    """One row per (imo, option) with the country that rule selects.

    ``gcb_name`` is the Global Carbon Budget's country name, which is what the
    baseline join uses -- the GCB keys its columns by name, not ISO3.

    Hong Kong is the one key whose GCB name depends on the treatment: under
    ``folded_into_china`` a Hong Kong flag resolves to China's baseline, which is
    what makes vessel A's allocation fully degenerate.
    """
    rows = []
    for vessel in cfg:
        for option in ALLOCATION_OPTIONS:
            key = vessel.allocation_keys.get(option) or {}
            gcb_name = key.get("gcb_name")
            if hk_treatment == "folded_into_china" and key.get("gcb_name_folded"):
                gcb_name = key["gcb_name_folded"]
            rows.append({
                "imo": vessel.imo,
                "option": option,
                "country": key.get("country"),
                "gcb_name": gcb_name,
                "hk_treatment": hk_treatment,
                "is_proxy": "PROXY" in str(key.get("method", "")),
            })
    return pd.DataFrame(rows)


def allocate(db: Database, cfg: Config, emissions_year: pd.DataFrame) -> pd.DataFrame:
    """§5.3 -- attribute ship-year CO2 to countries under each rule.

    Args:
        db: Database with the SQL registered.
        cfg: Loaded configuration.
        emissions_year: ``imo, year, scenario_id, power_estimate,
            smoothing_window, co2_tonnes`` from §4.

    Returns:
        ``option, country, gcb_name, year, scenario_id, ..., co2_tonnes, co2_mt``
        for every configured Hong Kong treatment.
    """
    db.register_frame("emissions_year", emissions_year)

    frames = []
    for treatment in cfg.run["hk_treatments"]:
        db.register_frame("vessel_key", vessel_key_table(cfg, treatment))
        frame = db.sql("50_allocation").df()
        frame["hk_treatment"] = treatment
        frames.append(frame)

    return pd.concat(frames, ignore_index=True)


def domestic_test(db: Database, cfg: Config) -> pd.DataFrame:
    """§5.4 -- domestic if more than 95% of hours lie in a single country's EEZ.

    Expects ``eez_hour`` and ``vessel_hour`` to be registered.

    Trivially satisfied for vessel A, which calls at ports in seventeen countries.
    Implemented because the fleet-scale version needs it, and because a template
    that omits the filter would quietly include domestic craft when scaled.
    """
    return db.sql("51_domestic_test", domestic_threshold=DOMESTIC_THRESHOLD).df()


def register_eez(db: Database, cfg: Config) -> int:
    """Register the EEZ v12 polygons and check the layer is the one expected.

    The archive holds two GeoPackages -- ``eez_v12.gpkg`` with 285 polygons and
    ``eez_boundaries_v12.gpkg`` with 2,349 linestrings. Loading the boundaries
    would make every point-in-polygon test return nothing with no error anywhere,
    so the feature count and geometry type are both asserted.
    """
    db.register_spatial_layer(
        "eez_polygons", cfg.spatial_layer("eez"), inner=cfg.spatial_inner("eez")
    )
    count, geom_type = db.query(
        "SELECT count(*), any_value(ST_GeometryType(geom)) FROM eez_polygons"
    ).fetchone()

    if "POLYGON" not in str(geom_type).upper():
        raise ValueError(
            f"EEZ layer geometry is {geom_type}, not polygons. The archive holds "
            "eez_boundaries_v12.gpkg (linestrings) alongside eez_v12.gpkg; check "
            "spatial.layers.eez in config/pilot.yaml."
        )
    if count != 285:
        log.warning("EEZ layer has %d polygons; v12 is documented as 285", count)
    return count


def summarise_options(cfg: Config, hk_treatment: str) -> pd.DataFrame:
    """Side-by-side view of every vessel's four allocation keys.

    This table is the qualitative result at n = 2: whether the rules agree or
    diverge, and for which hulls.
    """
    table = vessel_key_table(cfg, hk_treatment)
    wide = table.pivot(index="imo", columns="option", values="country")

    # Degeneracy is judged on the GCB name, not the ISO3 code, because the GCB name
    # is what decides which national budget the emissions actually land on.
    #
    # This distinction is the whole Hong Kong question. Under `folded_into_china` a
    # HKG-flagged hull keeps its ISO3 -- the flag really is Hong Kong -- but its
    # emissions join to China's baseline, so all four options resolve to one budget
    # and the comparison the paper is built on produces nothing. Judging on ISO3
    # would report a flag-versus-owner split that has no effect on any result.
    by_budget = table.pivot(index="imo", columns="option", values="gcb_name")
    wide["n_distinct_countries"] = by_budget[list(ALLOCATION_OPTIONS)].nunique(axis=1)
    wide["is_degenerate"] = wide["n_distinct_countries"] == 1
    wide["hk_treatment"] = hk_treatment
    return wide.reset_index()

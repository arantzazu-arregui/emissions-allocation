"""§3 -- fuel assignment, emission factors and specific fuel consumption.

A vessel-hour is assigned distillate fuel (MDO/MGO) when any of three conditions
holds, and residual fuel (HFO) otherwise: the main engine is high-speed; the position
falls inside an ECA polygon; or the hour belongs to a voyage leg between two EU ports.

The third condition is read from ``voyage_leg.is_eu_eu`` -- a genuine improvement on
the EEZ proxy that gridded-only data would have forced.

The IMO 2020 sulphur cap is immaterial here: the Fourth GHG Study assigns low-sulphur
HFO the same carbon content and emission factor as HFO, so the switch affects SOx, not
CO2. Whether the vessel carries a scrubber does not change the result, and no
fuel-switch date branch appears anywhere in this module.

The point-in-polygon test and the leg join are SQL (``21_eca_join.sql``,
``30_fuel_assignment.sql``); this module owns the lookups and the engine-class rule.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from emissions_allocation.config import Config, ConfigError, Vessel
from emissions_allocation.db import Database

log = logging.getLogger(__name__)

# IMO Table 19 engine classes. Only HSD triggers §3.1 condition 1.
HIGH_SPEED_ENGINE = "HSD"

# Table 19 names the boiler row "Steam Turbines (and boilers)".
BOILER_ENGINE = "boiler"
AUXILIARY_ENGINE = "auxiliary_engine"


def _imo_fuel_label(value: object) -> str | None:
    """Normalise one IHS main-fuel field to an IMO Table 9 label.

    Parameters
    ----------
    value : object
        A value from IHS ``FuelType1First`` or ``FuelType2Second``. ``NA`` has
        the report's deliberately broad meaning: unknown, not applicable, or
        fuel type not known.

    Returns
    -------
    str or None
        One of the labels used by Table 9, or ``None`` for a missing/unknown
        value.
    """
    if value is None or pd.isna(value):
        return None
    label = str(value).strip().casefold().replace("_", " ")
    aliases = {
        "na": None, "n/a": None, "unknown": None, "not applicable": None,
        "residual fuel": "residual", "residual": "residual",
        "distilled fuel": "distillate", "distillate fuel": "distillate",
        "distillate": "distillate", "mdo": "distillate", "mgo": "distillate",
        "gas boil-off": "gas_boil_off", "lng": "lng", "nuclear": "nuclear",
        "coal": "coal", "methanol": "methanol",
    }
    return aliases.get(label, label)


def allocate_imo_main_fuel(
    fuel_1: object, fuel_2: object, propulsion_type: object, vessel_type: object,
) -> str | None:
    """Allocate a vessel's main fuel using Fourth IMO GHG Study Table 9.

    Parameters
    ----------
    fuel_1, fuel_2 : object
        IHS ``FuelType1First`` (lightest) and ``FuelType2Second`` (densest)
        fields. ``NA`` is treated as missing, as stipulated by the Study.
    propulsion_type : object
        IHS propulsion-type description. The LNG exception applies when it
        identifies a steam turbine.
    vessel_type : object
        IHS vessel type. The LNG exception applies to liquefied gas tankers.

    Returns
    -------
    str or None
        ``HFO``, ``MDO``, ``LNG``, ``Nuclear``, ``Coal``, ``Methanol``, or
        ``None`` when Table 9 does not resolve the vessel.

    Notes
    -----
    This is the vessel-level fallback in printed pp. 46--47 of the Fourth IMO
    GHG Study 2020. It is not the project's primary vessel-hour ECA/EU rule.
    """
    first, second = _imo_fuel_label(fuel_1), _imo_fuel_label(fuel_2)
    fuels = {first, second}
    propulsion = "" if propulsion_type is None else str(propulsion_type).casefold()
    ship_type = "" if vessel_type is None else str(vessel_type).casefold()
    is_lng_steam_tanker = "steam" in propulsion and "liquefied gas tanker" in ship_type

    if is_lng_steam_tanker and "residual" in fuels:
        return "LNG"
    if first == "methanol" and second == "distillate":
        return "Methanol"
    if "residual" in fuels:
        return "HFO"
    if first == "gas_boil_off" and second == "distillate":
        return "LNG"
    if first == "lng" and second == "distillate":
        return "LNG"
    if "lng" in fuels and None in fuels:
        return "LNG"
    if second == "gas_boil_off":
        return "LNG"
    if first == "nuclear" and second in {"distillate", None}:
        return "Nuclear"
    if first == "coal" and second == "distillate":
        return "MDO"
    if first == "coal" and second is None:
        return "Coal"
    if first == "methanol":
        return "Methanol"
    if first == second == "distillate" or ("distillate" in fuels and None in fuels):
        return "MDO"
    return None


def allocate_and_infill_imo_main_fuel(
    vessels: pd.DataFrame,
    *,
    fuel_1_column: str = "fuel_type_1_first",
    fuel_2_column: str = "fuel_type_2_second",
    propulsion_column: str = "propulsion_type",
    vessel_type_column: str = "vessel_type",
    size_bin_column: str = "size_bin",
) -> pd.DataFrame:
    """Apply IMO Table 9 then infill unresolved vessels with group modal fuel.

    Parameters
    ----------
    vessels : pandas.DataFrame
        Fleet records containing IHS fuel fields plus vessel type and pre-defined
        size bin. The source does not license borrowing a mode across size bins.
    fuel_1_column, fuel_2_column, propulsion_column, vessel_type_column : str
        Input column names for the corresponding IHS fields.
    size_bin_column : str
        Input column defining the vessel-size category.

    Returns
    -------
    pandas.DataFrame
        A copy with ``main_fuel`` and ``main_fuel_assignment_method``. Rows stay
        null when no successfully allocated peer exists in their type-by-size group.

    Notes
    -----
    Implements printed pp. 46--47 of the Fourth IMO GHG Study 2020. A tied modal
    fuel is rejected because silently choosing one would make imputation arbitrary.
    """
    required = {fuel_1_column, fuel_2_column, propulsion_column, vessel_type_column, size_bin_column}
    missing = required.difference(vessels.columns)
    if missing:
        raise ConfigError(f"IMO fuel fallback missing columns: {sorted(missing)}")

    result = vessels.copy()
    result["main_fuel"] = [
        allocate_imo_main_fuel(first, second, propulsion, ship_type)
        for first, second, propulsion, ship_type in zip(
            result[fuel_1_column], result[fuel_2_column], result[propulsion_column],
            result[vessel_type_column], strict=True,
        )
    ]
    result["main_fuel_assignment_method"] = result["main_fuel"].notna().map(
        {True: "table_9", False: pd.NA}
    )

    group_columns = [vessel_type_column, size_bin_column]
    resolved = result.dropna(subset=["main_fuel"])
    counts = resolved.groupby(group_columns + ["main_fuel"], dropna=False).size()
    if counts.empty:
        return result
    maxima = counts.groupby(level=group_columns).transform("max")
    winners = counts[counts == maxima]
    tied = winners.groupby(level=group_columns).size()
    if (tied > 1).any():
        groups = list(tied[tied > 1].index)
        raise ConfigError(f"IMO fuel fallback has tied modal fuels for groups: {groups}")
    modes = winners.reset_index(name="_fuel_count")[
        group_columns + ["main_fuel"]
    ].rename(columns={"main_fuel": "_group_main_fuel"})
    result = result.merge(modes, on=group_columns, how="left")
    unresolved = result["main_fuel"].isna()
    result.loc[unresolved, "main_fuel"] = result.loc[unresolved, "_group_main_fuel"]
    result.loc[unresolved & result["main_fuel"].notna(), "main_fuel_assignment_method"] = "type_size_mode"
    return result.drop(columns="_group_main_fuel")


def is_high_speed(vessel: Vessel) -> bool:
    """§3.1 condition 1. False for both pilot hulls -- large ships are SSD."""
    return vessel.require_spec("engine_type") == HIGH_SPEED_ENGINE


def emission_factor(factors: dict[str, Any], fuel: str) -> float:
    """``EF_f`` in g CO2 per g fuel, from IMO Table 21."""
    fuels = (factors.get("emission_factors") or {}).get("fuels") or {}
    if fuel not in fuels:
        raise ConfigError(
            f"no emission factor for fuel {fuel!r} in config/emission_factors.yaml. "
            f"Known: {sorted(fuels)}"
        )
    return float(fuels[fuel]["ef_f"])


def sfc_base(factors: dict[str, Any], engine: str, fuel: str) -> float:
    """Base specific fuel consumption in g/kWh, from IMO Table 19 (2001+ column).

    Only the 2001+ column is carried: both pilot hulls are post-2001 builds. A
    pre-2001 hull would need the other two columns transcribed first, and this
    raises rather than silently using the wrong vintage.
    """
    engines = (factors.get("sfc_base") or {}).get("engines") or {}
    if engine not in engines:
        raise ConfigError(
            f"no SFC row for engine {engine!r} in config/emission_factors.yaml. "
            f"Known: {sorted(engines)}"
        )
    row = engines[engine]
    if fuel not in row:
        raise ConfigError(
            f"no SFC value for engine {engine!r} on fuel {fuel!r}. "
            f"Known fuels for this engine: {sorted(row)}"
        )
    return float(row[fuel])


def assert_build_year_in_range(vessel: Vessel, factors: dict[str, Any]) -> None:
    """Guard the single-column Table 19 assumption.

    ``config/emission_factors.yaml`` carries only the 2001+ SFC column. Applying it
    to a pre-2001 hull would understate fuel consumption by 5-15% with nothing
    failing, so the assumption is checked rather than trusted.
    """
    column = (factors.get("sfc_base") or {}).get("year_built_column")
    year = vessel.require_spec("year_built")
    if column == "2001+" and year < 2001:
        raise ConfigError(
            f"vessel {vessel.imo} was built in {year}, but config/emission_factors.yaml "
            f"carries only the Table 19 '{column}' column.\n"
            "  Transcribe the 'Before 1983' and '1984-2000' columns before modelling "
            "a pre-2001 hull -- the 2001+ values would understate its consumption."
        )


def assign_fuel(db: Database, cfg: Config, vessel: Vessel):
    """Assign a fuel to every vessel-hour (§3.1).

    Expects ``vessel_hour``, ``voyage_leg`` and ``eca_polygons`` to be registered.
    Materialises ``eca_hour`` then ``fuel_assignment``.

    Returns:
        The ``fuel_assignment`` relation as a DataFrame.
    """
    rules = cfg.factors["fuel_assignment"]

    db.table_from("eca_hour", "21_eca_join")
    db.table_from(
        "fuel_assignment",
        "30_fuel_assignment",
        main_engine_is_high_speed=is_high_speed(vessel),
        distillate_fuel=rules["distillate_fuel"],
        residual_fuel=rules["residual_fuel"],
    )
    return db.query("SELECT * FROM fuel_assignment").df()


def register_eca(db: Database, cfg: Config) -> int:
    """Register the ECA polygons and confirm the expected six are present.

    The Mediterranean SOx ECA entered into force in May 2025 and is absent from this
    shapefile. That is correct for a period ending in 2024, but its absence would be
    invisible -- a vessel in the Mediterranean would simply never match -- so the
    polygon count is asserted rather than assumed.
    """
    db.register_spatial_layer(
        "eca_polygons",
        cfg.spatial_layer("eca_sox_pm"),
        inner=cfg.spatial_inner("eca_sox_pm"),
    )
    count = db.query("SELECT count(*) FROM eca_polygons").fetchone()[0]

    expected = len(cfg.factors["fuel_assignment"]["eca"]["polygons"])
    if count != expected:
        raise ConfigError(
            f"ECA layer has {count} polygons, expected {expected}.\n"
            "  config/emission_factors.yaml lists the areas this study assumes. If "
            "the shapefile has been updated -- the Mediterranean SOx ECA came into "
            "force in May 2025 -- update that list and re-check the study period."
        )
    return count

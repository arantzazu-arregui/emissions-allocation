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

from emissions_allocation.config import Config, ConfigError, Vessel
from emissions_allocation.db import Database

log = logging.getLogger(__name__)

# IMO Table 19 engine classes. Only HSD triggers §3.1 condition 1.
HIGH_SPEED_ENGINE = "HSD"

# Table 19 names the boiler row "Steam Turbines (and boilers)".
BOILER_ENGINE = "boiler"
AUXILIARY_ENGINE = "auxiliary_engine"


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

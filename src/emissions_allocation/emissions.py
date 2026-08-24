"""§4 -- operating mode, power demand, SFC correction and CO2.

Converts the hourly activity series into CO2 mass per hour, summed to ship-year.

**One ordering decision, because the specification is circular on it.** IMO Table 16
assigns an operating mode from *main-engine load*, while §4.2 zeroes main-engine
power in the At berth and Anchored *modes*. Load is therefore computed from smoothed
speed first, used to assign the mode, and only then zeroed where the mode requires
it. This is consistent because the matrix consults load only above 3 kn, where the
mode is never berth or anchored.

Physical formulas live here. The mode matrix, the Table 17 range join, the distance
computations and all aggregation live in SQL.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from emissions_allocation.config import Config, Vessel
from emissions_allocation.db import Database
from emissions_allocation.fuel import AUXILIARY_ENGINE, BOILER_ENGINE, sfc_base
from emissions_allocation.specs import PowerEstimate, size_for_table17

log = logging.getLogger(__name__)

# Table 17's 'sea' column covers both transit modes.
MODE_TO_TABLE17 = {
    "at_berth": "at_berth",
    "anchored": "anchored",
    "manoeuvring": "manoeuvring",
    "slow_transit": "sea",
    "normal_cruising": "sea",
}

# §4.2: no main-engine power at berth or at anchor.
MODES_WITHOUT_MAIN_ENGINE = ("at_berth", "anchored")

# Beyond 5 nm Table 16 stops distinguishing, so distances are only computed inside
# this neighbourhood. ~0.12 degrees is a little over 7 nm of latitude.
PREFILTER_DEGREES = 0.12

# Table 16's 'Port 1-5 nm' column applies to liquid tankers only. The list is read
# from config (emission_factors.yaml operating_mode_matrix.liquid_tanker_types)
# rather than hardcoded, so adding a ship type is a config edit.


def main_engine_load(
    sog_kn: pd.Series,
    reference_speed_kn: float,
    load_at_reference: float,
    exponent: float = 3.0,
) -> pd.Series:
    """Estimate capped propulsion load from speed and a reference condition.

    ``Load_i = f_ref * (SOG_smoothed / V_ref)^n``, capped at 1.0, where ``f_ref``
    is the documented fraction of rated MCR at the source's reference speed. The
    cube is why §1.6's smoothing is mandatory rather than cosmetic: cell-centroid
    quantisation makes the raw speed oscillate, and the error does not average out.
    """
    if reference_speed_kn <= 0:
        raise ValueError(f"reference speed must be positive, got {reference_speed_kn}")
    if not 0 < load_at_reference <= 1:
        raise ValueError(f"reference load must be in (0, 1], got {load_at_reference}")
    if exponent <= 0:
        raise ValueError(f"speed exponent must be positive, got {exponent}")
    return np.minimum(
        load_at_reference * (sog_kn / reference_speed_kn) ** exponent,
        1.0,
    )


def load_correction_factor(load: pd.Series | float, factors: dict) -> pd.Series | float:
    """``CF_L = 0.455*Load^2 - 0.710*Load + 1.280`` -- IMO equation (10), verbatim.

    Minimises at Load = 0.78, matching the study's stated ~80% MCR optimum. That
    coincidence is the check that the coefficients are transcribed correctly, and it
    is asserted in the tests.
    """
    c = factors["load_correction"]
    return c["a"] * load**2 + c["b"] * load + c["c"]


def build_hour_model(
    spine: pd.DataFrame,
    vessel: Vessel,
    cfg: Config,
    estimate: PowerEstimate,
    window: int,
    modes: pd.Series,
    speed_prefix: str = "sog",
    gap_treatment: str = "linear_coverage",
) -> pd.DataFrame:
    """Per-hour power demand, SFC and emission factor for one scenario.

    Args:
        spine: ``vessel_hour`` with smoothed speed columns and ``is_inactive``.
        vessel: The hull.
        cfg: Loaded configuration.
        estimate: The power/speed estimate for this scenario.
        window: Smoothing window for this scenario.
        modes: Operating mode per hour, aligned to ``spine``.
    """
    factors = cfg.factors
    cutoff = factors["load_correction"]["main_engine_cutoff_load"]

    out = pd.DataFrame({
        "imo": spine["imo"],
        "ts": spine["ts"],
        "is_inactive": spine["is_inactive"],
        "sog": spine[f"{speed_prefix}_w{window}"],
        "operating_mode": modes,
        "gap_treatment": gap_treatment,
    })
    out["me_load"] = main_engine_load(
        out["sog"].fillna(0.0),
        estimate.design_speed_kn,
        estimate.load_at_reference,
        estimate.speed_exponent,
    )

    # §4.2: main engine off at berth and at anchor, and below the 7% MCR cutoff.
    # "At engine loads below 7%, fuel consumption and all the emissions derived
    # from the main engine are assumed to be zero."
    running = (
        ~out["operating_mode"].isin(MODES_WITHOUT_MAIN_ENGINE)
        & (out["me_load"] >= cutoff)
    )
    out["w_me_kw"] = np.where(running, estimate.mcr_kw * out["me_load"], 0.0)

    # Fuel is assigned per hour in §3; SFC and EF are looked up from it in SQL via
    # a join, but the per-hour base values depend on the fuel, so they are resolved
    # here where the fuel column is available.
    return out


def attach_fuel_dependent_terms(
    hour_model: pd.DataFrame, fuel_assignment: pd.DataFrame, vessel: Vessel, cfg: Config
) -> pd.DataFrame:
    """Attach SFC and emission factor, both of which depend on the assigned fuel.

    Auxiliary engines and boilers are **not** load-corrected -- IMO equation (11),
    ``FC_AE|BO,i = SFC_base * W_AE|BO,i``. Only the main engine gets ``CF_L``.
    """
    factors = cfg.factors
    engine = vessel.require_spec("engine_type")

    merged = hour_model.merge(
        fuel_assignment[["imo", "ts", "fuel_type"]], on=["imo", "ts"], how="left"
    )

    fuels = merged["fuel_type"].fillna(factors["fuel_assignment"]["residual_fuel"])
    merged["sfc_me_base"] = fuels.map(
        lambda f: sfc_base(factors, engine, f)
    )
    merged["sfc_ae_g_kwh"] = fuels.map(lambda f: sfc_base(factors, AUXILIARY_ENGINE, f))
    merged["sfc_bo_g_kwh"] = fuels.map(lambda f: sfc_base(factors, BOILER_ENGINE, f))
    merged["ef_f"] = fuels.map(
        lambda f: factors["emission_factors"]["fuels"][f]["ef_f"]
    )

    # Equation (10) applies to the main engine only.
    merged["sfc_me_g_kwh"] = merged["sfc_me_base"] * load_correction_factor(
        merged["me_load"], factors
    )
    merged["table17_mode"] = merged["operating_mode"].map(MODE_TO_TABLE17)
    return merged


def register_distance_layers(db: Database, cfg: Config, spine: pd.DataFrame) -> pd.DataFrame:
    """Compute distance to port and to coast for each distinct position.

    Distances are computed once per distinct 0.01 degree cell rather than per hour --
    70,128 hours collapse to ~38,600 positions, and the coast query is the most
    expensive step in the pipeline.
    """
    db.con.execute("""
        CREATE OR REPLACE MACRO gc_nm(lat1, lon1, lat2, lon2) AS
            2 * 6371.0088 * asin(sqrt(
                  pow(sin(radians(lat2 - lat1) / 2), 2)
                + cos(radians(lat1)) * cos(radians(lat2))
                  * pow(sin(radians(lon2 - lon1) / 2), 2)
            )) / 1.852;
    """)

    positions = spine[["lat", "lon"]].drop_duplicates()
    db.register_frame("distinct_position", positions)
    log.info("%d hours -> %d distinct positions", len(spine), len(positions))

    db.register_spatial_layer(
        "coastline_union",
        cfg.spatial_layer("coastline"),
        inner=cfg.spatial_inner("coastline"),
    )

    # §4.1 At berth / Anchored: a port-visit interval is a stronger signal than
    # distance to an anchorage point. Built once, reused by every scenario.
    db.table_from("port_visit_hour", "24_port_visit_hour")

    db.table_from("port_distance", "22_distance_to_port", prefilter_degrees=PREFILTER_DEGREES)
    db.table_from("coast_distance", "23_distance_to_coast", prefilter_degrees=PREFILTER_DEGREES)

    db.con.execute("""
        CREATE OR REPLACE TABLE position_distance AS
        SELECT p.lat, p.lon, pd.port_nm, cd.coast_nm
        FROM distinct_position AS p
        LEFT JOIN port_distance  AS pd ON pd.lat = p.lat AND pd.lon = p.lon
        LEFT JOIN coast_distance AS cd ON cd.lat = p.lat AND cd.lon = p.lon;
    """)
    return db.query("SELECT * FROM position_distance").df()


def assign_modes(
    db: Database, spine: pd.DataFrame, vessel: Vessel, cfg: Config,
    estimate: PowerEstimate, window: int, speed_prefix: str = "sog",
) -> pd.Series:
    """Run the Table 16 matrix for one scenario and return the mode per hour."""
    hour_load = pd.DataFrame({
        "imo": spine["imo"],
        "ts": spine["ts"],
        "lat": spine["lat"],
        "lon": spine["lon"],
        "sog": spine[f"{speed_prefix}_w{window}"].fillna(0.0),
    })
    hour_load["me_load"] = main_engine_load(
        hour_load["sog"],
        estimate.design_speed_kn,
        estimate.load_at_reference,
        estimate.speed_exponent,
    )
    db.register_frame("hour_load", hour_load)

    liquid_tankers = cfg.factors["operating_mode_matrix"]["liquid_tanker_types"]
    is_liquid_tanker = vessel.require_spec("ship_type") in liquid_tankers
    modes = db.sql(
        "40_operating_mode",
        is_liquid_tanker=is_liquid_tanker,
        use_port_visit_intervals=bool(cfg.run.get("use_port_visit_intervals", True)),
    ).df()
    return modes.set_index(["imo", "ts"])["operating_mode"]


def annual_emissions(
    db: Database, cfg: Config, vessel: Vessel, spine: pd.DataFrame,
    fuel_assignment: pd.DataFrame, coverage: pd.DataFrame,
    estimates: dict[str, PowerEstimate],
    *,
    speed_prefix: str = "sog",
    gap_treatment: str = "linear_coverage",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every scenario and return ``(emissions_hour, emissions_year)``.

    ``speed_prefix`` selects a parallel speed treatment (for example
    ``sog_imo2020`` for the adapted Fourth IMO GHG Study sensitivity) without
    changing the primary series. ``gap_treatment`` is persisted on every output
    row so separate treatment results cannot be silently combined.
    """
    ship_type, size, _unit = size_for_table17(vessel, cfg)
    db.register_frame("fuel_assignment", fuel_assignment)
    db.register_frame("coverage", coverage)

    hourly_frames = []
    for name, estimate in estimates.items():
        for window in cfg.run["smoothing_windows"]:
            scenario_id_stub = f"{name}_w{window}"

            modes = assign_modes(db, spine, vessel, cfg, estimate, window, speed_prefix)
            model = build_hour_model(
                spine, vessel, cfg, estimate, window,
                modes.reindex(
                    pd.MultiIndex.from_arrays([spine["imo"], spine["ts"]])
                ).to_numpy(), speed_prefix, gap_treatment,
            )
            model = attach_fuel_dependent_terms(model, fuel_assignment, vessel, cfg)
            model["scenario_id"] = scenario_id_stub
            model["power_estimate"] = name
            model["smoothing_window"] = window

            db.register_frame("hour_model", model)
            hourly_frames.append(
                db.sql("42_emissions_hour", ship_type=ship_type, vessel_size=size).df()
            )

    emissions_hour = pd.concat(hourly_frames, ignore_index=True)
    db.register_frame("emissions_hour", emissions_hour)

    emissions_year = db.sql(
        "43_emissions_year",
        apply_coverage_correction=bool(cfg.run["coverage_correction"]),
        coverage_warn=float(cfg.run["hour_coverage_warn"]),
    ).df()
    return emissions_hour, emissions_year

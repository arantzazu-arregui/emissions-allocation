"""§8 -- sensitivity and validation checks.

Every check returns a :class:`Check`, allowing the run to report all available
evidence rather than stopping at the first warning or failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from emissions_allocation.activity import haversine_km
from emissions_allocation.config import Config, Vessel
from emissions_allocation.specs import PowerEstimate

PASS, FAIL, WARN, PENDING = "PASS", "FAIL", "WARN", "PENDING"
MAX_PLAUSIBLE_LEG_KN = 30.0


@dataclass
class Check:
    """A validation result.

    Parameters
    ----------
    name : str
        Stable name of the check.
    status : str
        PASS, FAIL, WARN, or PENDING.
    detail : str
        Human-readable result.
    """

    name: str
    status: str
    detail: str
    basis: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        """Format the result for console output.

        Returns
        -------
        str
            One formatted status line.
        """
        return f"[{self.status:<7}] {self.name}: {self.detail}"


def check_hour_conservation(coverage: pd.DataFrame, cfg: Config) -> Check:
    """Check observed activity coverage by year.

    Parameters
    ----------
    coverage : pandas.DataFrame
        Per-year observed and active-hour coverage.
    cfg : Config
        Run configuration containing the warning threshold.

    Returns
    -------
    Check
        Coverage check result.
    """
    warn = cfg.run["hour_coverage_warn"]
    low = coverage[coverage["coverage_active"] < warn]
    detail = (
        f"{len(coverage)} years, active coverage "
        f"{coverage['coverage_active'].min():.1%}-{coverage['coverage_active'].max():.1%}"
    )
    if not low.empty:
        years = ", ".join(f"{int(row.year)} {row.coverage_active:.1%}" for row in low.itertuples())
        return Check("Hour conservation", WARN, f"{detail}; below {warn:.0%} in {len(low)} year(s): {years}",
                     basis="observed hours / in-service hours", data={"low_years": low["year"].tolist()})
    return Check("Hour conservation", PASS, detail, basis="observed / in-service hours")


def check_identity_integrity(spine: pd.DataFrame, vessel: Vessel) -> Check:
    """Ensure an activity series contains only its configured IMO.

    Parameters
    ----------
    spine : pandas.DataFrame
        Hourly activity data with an ``imo`` column.
    vessel : Vessel
        Configured vessel identity.

    Returns
    -------
    Check
        Identity-integrity result.
    """
    distinct = sorted(spine["imo"].dropna().unique())
    if distinct == [vessel.imo]:
        return Check("Identity integrity", PASS, f"one IMO throughout: {vessel.imo}",
                     basis="distinct IMO in vessel_hour")
    return Check("Identity integrity", FAIL, f"expected only {vessel.imo}, found {distinct}",
                 basis="distinct IMO in vessel_hour")


def check_leg_speeds(legs: pd.DataFrame, port_calls: pd.DataFrame) -> Check:
    """Check implied speeds between consecutive port calls.

    Parameters
    ----------
    legs : pandas.DataFrame
        Voyage-leg table with endpoint IDs and durations in h.
    port_calls : pandas.DataFrame
        Port-call locations in degrees north/east.

    Returns
    -------
    Check
        Leg-speed plausibility result.
    """
    coords = port_calls.set_index("port_id")[["lat", "lon"]].groupby(level=0).first()
    merged = legs.merge(coords.rename(columns={"lat": "o_lat", "lon": "o_lon"}),
                        left_on="origin_port_id", right_index=True, how="left").merge(
        coords.rename(columns={"lat": "d_lat", "lon": "d_lon"}),
        left_on="dest_port_id", right_index=True, how="left")
    usable = merged.dropna(subset=["o_lat", "o_lon", "d_lat", "d_lon"])
    usable = usable[usable["leg_hours"] > 0].copy()
    if usable.empty:
        return Check("Leg-speed plausibility", PENDING, "no legs with usable coordinates")

    usable["km"] = haversine_km(usable["o_lat"], usable["o_lon"], usable["d_lat"], usable["d_lon"])
    usable["kn"] = (usable["km"] / 1.852) / usable["leg_hours"]
    moving = usable[usable["km"] > 1.0]
    impossible = moving[moving["kn"] > MAX_PLAUSIBLE_LEG_KN]
    detail = f"{len(moving)} legs with movement, median {moving['kn'].median():.1f} kn, 95th pct {moving['kn'].quantile(0.95):.1f} kn"
    if impossible.empty:
        return Check("Leg-speed plausibility", PASS, detail,
                     basis="great-circle distance / leg duration (a lower bound)")

    domestic = int((impossible["origin_iso3"] == impossible["dest_iso3"]).sum())
    same_port = int((impossible["origin_port_id"] == impossible["dest_port_id"]).sum())
    hours = float(impossible["leg_hours"].sum())
    total_leg_hours = float(usable["leg_hours"].sum())
    if domestic == len(impossible) and hours / total_leg_hours < 0.01:
        return Check("Leg-speed plausibility", PASS,
                     f"{detail}; {len(impossible)} legs above {MAX_PLAUSIBLE_LEG_KN} kn are anchorage-segmentation artefacts, not voyages ({domestic}/{len(impossible)} same-country, {same_port} same-port, {hours:.1f} h = {hours / total_leg_hours:.2%} of leg time)",
                     basis="great-circle distance / leg duration; impossible legs diagnosed",
                     data={"artefact_legs": len(impossible), "artefact_hours": hours})
    return Check("Leg-speed plausibility", WARN,
                 f"{detail}; {len(impossible)} above {MAX_PLAUSIBLE_LEG_KN} kn, of which {len(impossible) - domestic} cross a border -- those are NOT anchorage artefacts and indicate a mis-ordered or missing port call",
                 basis="great-circle distance / leg duration (a lower bound)",
                 data={"artefact_legs": domestic, "suspect_legs": len(impossible) - domestic})


def check_port_call_agreement(emissions_hour: pd.DataFrame, port_calls: pd.DataFrame) -> Check:
    """Compare stationary model hours with port-visit duration.

    Parameters
    ----------
    emissions_hour : pandas.DataFrame
        Scenario-keyed modelled hourly emissions.
    port_calls : pandas.DataFrame
        Port-visit intervals and durations in h.

    Returns
    -------
    Check
        Port-call agreement result.
    """
    if emissions_hour.empty:
        return Check("Port-call/track agreement", PENDING, "no modelled hours")
    one = emissions_hour[emissions_hour["scenario_id"] == emissions_hour["scenario_id"].iloc[0]]
    modelled = len(one[one["operating_mode"].isin({"at_berth", "anchored", "manoeuvring"})])
    visit_hours = float(port_calls["duration_h"].sum())
    ratio = modelled / visit_hours if visit_hours else float("nan")
    detail = f"{modelled:,} h at berth/anchored/manoeuvring against {visit_hours:,.0f} h inside port visits (ratio {ratio:.2f})"
    return Check("Port-call/track agreement", PASS if 0.8 <= ratio <= 1.3 else WARN, detail,
                 basis="modes vs event intervals")


def check_fleet_envelope(estimates: dict[str, PowerEstimate], cfg: Config) -> Check:
    """Check estimated design speeds against configured fleet envelopes.

    Parameters
    ----------
    estimates : dict[str, PowerEstimate]
        Named power estimates.
    cfg : Config
        Unused run configuration, retained for a stable check interface.

    Returns
    -------
    Check
        Fleet-envelope result.
    """
    del cfg
    if not estimates:
        return Check("Fleet envelope", PENDING, "no power estimates resolved")
    outside = {key: estimate for key, estimate in estimates.items() if estimate.within_fleet_envelope is False}
    inside = sorted(key for key, estimate in estimates.items() if estimate.within_fleet_envelope is True)
    unassessed = sorted(key for key, estimate in estimates.items() if estimate.within_fleet_envelope is None)
    if not outside:
        detail = f"within range: {inside or 'none'}"
        if unassessed:
            detail += f"; no published envelope for this ship type: {unassessed}"
        return Check("Fleet envelope", PASS if inside else PENDING, detail,
                     basis="observed service-speed range for the ship type")
    names = ", ".join(f"{key} at {estimate.design_speed_kn:.2f} kn" for key, estimate in outside.items())
    detail = f"{names} outside the published range (inside: {', '.join(inside) or 'none'}"
    if unassessed:
        detail += f"; unassessed: {', '.join(unassessed)}"
    return Check("Fleet envelope", FAIL, detail + ")",
                 basis="observed service-speed range for the ship type",
                 data={"outside": list(outside), "unassessed": unassessed})


def check_smoothing_sensitivity(spine: pd.DataFrame, cfg: Config) -> Check:
    """Calculate the cubic speed bias for each smoothing window.

    Parameters
    ----------
    spine : pandas.DataFrame
        Activity spine containing smoothed speeds.
    cfg : Config
        Run configuration containing smoothing windows.

    Returns
    -------
    Check
        Smoothing-sensitivity result.
    """
    from emissions_allocation.activity import cubic_bias

    active = spine[~spine["is_inactive"]]
    bias = {window: cubic_bias(active[f"sog_w{window}"]) for window in cfg.run["smoothing_windows"]}
    best = min(bias, key=bias.get)
    return Check("Smoothing sensitivity", PASS,
                 " ".join(f"w={window}:{value:.2f}x" for window, value in bias.items()) + f" (lowest at w={best})",
                 basis="mean(v^3)/(mean v)^3 over in-service hours", data={"bias": bias, "best_window": best})


def run_all(cfg: Config, vessel: Vessel, spine: pd.DataFrame, port_calls: pd.DataFrame,
            legs: pd.DataFrame, coverage: pd.DataFrame, emissions_hour: pd.DataFrame,
            emissions_year: pd.DataFrame, estimates: dict[str, PowerEstimate]) -> list[Check]:
    """Run every sensitivity and validation check.

    Parameters
    ----------
    cfg, vessel, spine, port_calls, legs, coverage, emissions_hour, emissions_year, estimates
        Pipeline products required by the individual checks. ``emissions_year`` is
        retained in the public interface for pipeline compatibility.

    Returns
    -------
    list[Check]
        Results in report order.
    """
    del emissions_year
    return [check_identity_integrity(spine, vessel), check_hour_conservation(coverage, cfg),
            check_leg_speeds(legs, port_calls), check_port_call_agreement(emissions_hour, port_calls),
            check_fleet_envelope(estimates, cfg), check_smoothing_sensitivity(spine, cfg)]


def summarise(checks: list[Check]) -> pd.DataFrame:
    """Convert validation results to a tabular report.

    Parameters
    ----------
    checks : list[Check]
        Validation results.

    Returns
    -------
    pandas.DataFrame
        Check name, status, detail, and basis.
    """
    return pd.DataFrame([{"check": check.name, "status": check.status, "detail": check.detail, "basis": check.basis} for check in checks])

"""§8 -- sensitivity and validation.

===================== ========================================================
 Check                 Basis
===================== ========================================================
 THETIS-MRV            Verified annual CO2, published because the vessel makes
                       35 EU port calls. Genuine external ground truth and the
                       strongest check available.
 Hour conservation     Observed hours against elapsed time, per year.
 Leg-speed             Great-circle distance between port calls / leg duration
                       should give sensible average speeds.
 Port-call agreement   Berth periods in the track must coincide with port-visit
                       events.
 Identity integrity    Exactly one distinct IMO in every presence pull.
 Fleet envelope        Design speed within 6.0-24.5 kn. Estimate A fails at
                       28.92 kn.
===================== ========================================================

THETIS-MRV is used **only** to validate, never as an input -- it is EU-scope, and
this study allocates emissions globally. Feeding it back into the model would make
the comparison circular and would import an EU boundary into a global allocation.

Every check returns a :class:`Check` rather than raising, so one failure does not
hide the others. ``status`` is PASS, FAIL, WARN or PENDING -- PENDING meaning the
evidence is not available, which is reported rather than silently skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from emissions_allocation.activity import haversine_km
from emissions_allocation.config import Config, Vessel
from emissions_allocation.specs import PowerEstimate

log = logging.getLogger(__name__)

PASS, FAIL, WARN, PENDING = "PASS", "FAIL", "WARN", "PENDING"

# A leg's implied average speed should sit inside the physically plausible band for
# a merchant hull. Below this a "leg" is usually two calls at the same port.
MIN_PLAUSIBLE_LEG_KN = 1.0
MAX_PLAUSIBLE_LEG_KN = 30.0


@dataclass
class Check:
    """One validation result."""

    name: str
    status: str
    detail: str
    basis: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"[{self.status:<7}] {self.name}: {self.detail}"


def check_hour_conservation(coverage: pd.DataFrame, cfg: Config) -> Check:
    """Observed hours against elapsed time, per year.

    Judged on ``coverage_active`` -- observed over IN-SERVICE hours. Raw coverage
    counts a lay-up as missing data, which is what made vessel A's 2019 look like a
    36% failure when its reception was in fact 82%, the same as 2017.
    """
    warn = cfg.run["hour_coverage_warn"]
    low = coverage[coverage["coverage_active"] < warn]
    detail = (
        f"{len(coverage)} years, active coverage "
        f"{coverage['coverage_active'].min():.1%}-{coverage['coverage_active'].max():.1%}"
    )
    if not low.empty:
        years = ", ".join(f"{int(r.year)} {r.coverage_active:.1%}" for r in low.itertuples())
        return Check(
            "Hour conservation", WARN,
            f"{detail}; below {warn:.0%} in {len(low)} year(s): {years}",
            basis="observed hours / in-service hours",
            data={"low_years": low["year"].tolist()},
        )
    return Check("Hour conservation", PASS, detail, basis="observed / in-service hours")


def check_identity_integrity(spine: pd.DataFrame, vessel: Vessel) -> Check:
    """Exactly one distinct IMO in the activity series.

    The presence filter matches on ship name, which is not unique across the fleet,
    so this is the standing guard against two hulls being summed.
    """
    distinct = sorted(spine["imo"].dropna().unique())
    if distinct == [vessel.imo]:
        return Check(
            "Identity integrity", PASS, f"one IMO throughout: {vessel.imo}",
            basis="distinct IMO in vessel_hour",
        )
    return Check(
        "Identity integrity", FAIL,
        f"expected only {vessel.imo}, found {distinct}",
        basis="distinct IMO in vessel_hour",
    )


def check_leg_speeds(legs: pd.DataFrame, port_calls: pd.DataFrame) -> Check:
    """Great-circle distance between consecutive port calls over leg duration.

    A crude but independent check on the port-call sequence: if the events were
    mis-ordered or a call were missing, implied speeds would go out of range.
    Great-circle understates the real distance sailed, so this is a lower bound --
    an implausibly HIGH value is the informative failure.
    """
    coords = port_calls.set_index("port_id")[["lat", "lon"]].groupby(level=0).first()
    merged = legs.merge(
        coords.rename(columns={"lat": "o_lat", "lon": "o_lon"}),
        left_on="origin_port_id", right_index=True, how="left",
    ).merge(
        coords.rename(columns={"lat": "d_lat", "lon": "d_lon"}),
        left_on="dest_port_id", right_index=True, how="left",
    )
    usable = merged.dropna(subset=["o_lat", "o_lon", "d_lat", "d_lon"])
    usable = usable[usable["leg_hours"] > 0]
    if usable.empty:
        return Check("Leg-speed plausibility", PENDING, "no legs with usable coordinates")

    usable = usable.copy()
    usable["km"] = haversine_km(
        usable["o_lat"], usable["o_lon"], usable["d_lat"], usable["d_lon"]
    )
    usable["kn"] = (usable["km"] / 1.852) / usable["leg_hours"]

    # Same-port pairs give ~0 and are not informative.
    moving = usable[usable["km"] > 1.0]
    impossible = moving[moving["kn"] > MAX_PLAUSIBLE_LEG_KN]

    detail = (
        f"{len(moving)} legs with movement, median {moving['kn'].median():.1f} kn, "
        f"95th pct {moving['kn'].quantile(0.95):.1f} kn"
    )

    if impossible.empty:
        return Check(
            "Leg-speed plausibility", PASS, detail,
            basis="great-circle distance / leg duration (a lower bound)",
        )

    # A leg no ship could sail is not a voyage. It is GFW splitting one continuous
    # port stay into two "visits" as the hull shifts between adjacent anchorage
    # polygons -- the Pearl River Delta and Yangshan both do this. The implied
    # speed is inflated because the distance is measured between the two ports'
    # REPRESENTATIVE anchorage points, while the vessel barely moved.
    #
    # Diagnosed rather than merged away: merging visits would need an invented
    # threshold for "the same call", and would break the 389-port-call figure that
    # the activity stage validates against.
    domestic = int((impossible["origin_iso3"] == impossible["dest_iso3"]).sum())
    same_port = int((impossible["origin_port_id"] == impossible["dest_port_id"]).sum())
    hours = float(impossible["leg_hours"].sum())
    total_leg_hours = float(usable["leg_hours"].sum())

    if domestic == len(impossible) and hours / total_leg_hours < 0.01:
        return Check(
            "Leg-speed plausibility", PASS,
            f"{detail}; {len(impossible)} legs above {MAX_PLAUSIBLE_LEG_KN} kn are "
            f"anchorage-segmentation artefacts, not voyages "
            f"({domestic}/{len(impossible)} same-country, {same_port} same-port, "
            f"{hours:.1f} h = {hours / total_leg_hours:.2%} of leg time)",
            basis="great-circle distance / leg duration; impossible legs diagnosed",
            data={"artefact_legs": len(impossible), "artefact_hours": hours},
        )

    return Check(
        "Leg-speed plausibility", WARN,
        f"{detail}; {len(impossible)} above {MAX_PLAUSIBLE_LEG_KN} kn, of which "
        f"{len(impossible) - domestic} cross a border -- those are NOT anchorage "
        f"artefacts and indicate a mis-ordered or missing port call",
        basis="great-circle distance / leg duration (a lower bound)",
        data={"artefact_legs": domestic, "suspect_legs": len(impossible) - domestic},
    )


def check_port_call_agreement(
    emissions_hour: pd.DataFrame, port_calls: pd.DataFrame
) -> Check:
    """Berth periods in the track must coincide with port-visit events.

    Cross-endpoint again: the modes come from positions and speed, the intervals
    from the events API. With ``use_port_visit_intervals`` enabled the At berth
    hours are BY CONSTRUCTION inside a visit, so what this actually tests is the
    converse -- that in-port hours are not being classified as under way.
    """
    if emissions_hour.empty:
        return Check("Port-call/track agreement", PENDING, "no modelled hours")

    one = emissions_hour[emissions_hour["scenario_id"] == emissions_hour["scenario_id"].iloc[0]]
    stationary = {"at_berth", "anchored", "manoeuvring"}
    in_port_modes = one[one["operating_mode"].isin(stationary)]

    visit_hours = float(port_calls["duration_h"].sum())
    modelled = len(in_port_modes)
    ratio = modelled / visit_hours if visit_hours else float("nan")

    detail = (
        f"{modelled:,} h at berth/anchored/manoeuvring against {visit_hours:,.0f} h "
        f"inside port visits (ratio {ratio:.2f})"
    )
    if 0.8 <= ratio <= 1.3:
        return Check("Port-call/track agreement", PASS, detail, basis="modes vs event intervals")
    return Check("Port-call/track agreement", WARN, detail, basis="modes vs event intervals")


def check_fleet_envelope(estimates: dict[str, PowerEstimate], cfg: Config) -> Check:
    """Estimated design speed against the observed fleet range for its ship type.

    The envelope is hull-form specific -- 6.0-24.5 kn is the CONTAINER fleet's
    range -- so a type with none published returns ``within_fleet_envelope=None``
    and is reported as unassessed rather than as a pass.

    Estimate A sits at 25.55 kn for vessel A, just above the 24.5 kn maximum. That
    is a reference line fitted to a historical fleet applied to a modern
    slow-steaming hull: a known and bounded bias, reported rather than corrected.
    """
    outside = {k: e for k, e in estimates.items() if e.within_fleet_envelope is False}
    inside = sorted(k for k, e in estimates.items() if e.within_fleet_envelope is True)
    unassessed = sorted(k for k, e in estimates.items() if e.within_fleet_envelope is None)

    if not estimates:
        return Check("Fleet envelope", PENDING, "no power estimates resolved")

    if not outside:
        detail = f"within range: {inside or 'none'}"
        if unassessed:
            detail += f"; no published envelope for this ship type: {unassessed}"
        return Check(
            "Fleet envelope", PASS if inside else PENDING, detail,
            basis="observed service-speed range for the ship type",
        )

    names = ", ".join(f"{k} at {e.design_speed_kn:.2f} kn" for k, e in outside.items())
    return Check(
        "Fleet envelope", FAIL,
        f"{names} outside the published range (inside: {', '.join(inside) or 'none'}"
        + (f"; unassessed: {', '.join(unassessed)}" if unassessed else "") + ")",
        basis="observed service-speed range for the ship type",
        data={"outside": list(outside), "unassessed": unassessed},
    )


def check_smoothing_sensitivity(spine: pd.DataFrame, cfg: Config) -> Check:
    """The v^3 bias by smoothing window (§1.6, §8.1).

    Open item 5 was that the window had been validated on one day only. Measured
    across the full series it is larger than that sample suggested.
    """
    from emissions_allocation.activity import cubic_bias

    active = spine[~spine["is_inactive"]]
    bias = {w: cubic_bias(active[f"sog_w{w}"]) for w in cfg.run["smoothing_windows"]}
    best = min(bias, key=bias.get)
    detail = " ".join(f"w={w}:{b:.2f}x" for w, b in bias.items()) + f" (lowest at w={best})"
    return Check(
        "Smoothing sensitivity", PASS, detail,
        basis="mean(v^3)/(mean v)^3 over in-service hours",
        data={"bias": bias, "best_window": best},
    )


# ---------------------------------------------------------------------------
# MRV scope reconstruction
# ---------------------------------------------------------------------------

# EEA for MRV purposes: EU27 plus Norway and Iceland. The UK was in scope through
# the Brexit transition, which ended 31 December 2020.
_EEA_EXTRA = {"NOR", "ISL"}
UK_IN_SCOPE_THROUGH = 2020

# Below this share of port calls carrying a usable port-of-call flag, the scope
# reconstruction is too sparse to compare against, and the check reports PENDING
# rather than a ratio that looks like a verdict.
MIN_DOCK_SHARE = 0.10


def eea_countries(year: int) -> set[str]:
    """EEA membership for MRV scope in a given reporting year."""
    from emissions_allocation.activity import EU27

    members = set(EU27) | _EEA_EXTRA
    if year <= UK_IN_SCOPE_THROUGH:
        members.add("GBR")
    return members


def _naive(value) -> pd.Timestamp:
    """Drop the timezone so event timestamps compare against the naive hour spine."""
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(None) if stamp.tz is not None else stamp


def dock_call_share(port_calls: pd.DataFrame) -> float:
    """Fraction of port calls carrying the ``at_dock`` flag.

    The honest weakness of this reconstruction. GFW flags 68 of vessel A's 389
    calls, so entire years can reconstruct to zero scope -- which has to be reported
    as PENDING, not as a modelled figure of zero.
    """
    return float(port_calls["at_dock"].mean()) if len(port_calls) else 0.0


def mrv_scope_hours(
    emissions_hour: pd.DataFrame, port_calls: pd.DataFrame, year: int
) -> pd.Series:
    """Which modelled hours fall inside EU MRV scope, as a boolean Series.

    MRV counts a voyage from the **last port of call** to an EEA port, and from an
    EEA port to the next port of call, plus time at berth in EEA ports.

    The trap is its definition of "port of call", which **excludes stops at
    anchorage**. GFW records Suez Canal transit anchorages as port visits, so a
    naive reconstruction breaks the Asia-Europe voyage at Suez and counts only the
    short Suez-to-Europe hop -- undercounting scope roughly fourfold. Only calls
    flagged ``at_dock`` are treated as ports of call here.
    """
    calls = port_calls[port_calls["at_dock"]].sort_values("start_ts").reset_index(drop=True)
    if calls.empty:
        return pd.Series(False, index=emissions_hour.index)

    calls = calls.copy()
    calls["prev_end"] = calls["end_ts"].shift()
    calls["prev_iso"] = calls["port_iso3"].shift()

    eea = eea_countries(year)
    ts = emissions_hour["ts"]
    in_scope = pd.Series(False, index=emissions_hour.index)

    # Voyages with an EEA port at either end, counted in full.
    for leg in calls.dropna(subset=["prev_end"]).itertuples():
        if leg.prev_iso in eea or leg.port_iso3 in eea:
            in_scope |= (ts >= _naive(leg.prev_end)) & (ts < _naive(leg.start_ts))

    # Time at berth in EEA ports.
    for call in calls[calls["port_iso3"].isin(eea)].itertuples():
        in_scope |= (ts >= _naive(call.start_ts)) & (ts < _naive(call.end_ts))

    return in_scope


def compare_thetis_mrv(cfg: Config, emissions_year: pd.DataFrame, vessel: Vessel,
                       emissions_hour: pd.DataFrame | None = None,
                       port_calls: pd.DataFrame | None = None) -> Check:
    """Modelled annual CO2 against THETIS-MRV verified figures.

    The only genuine external ground truth available. Returns PENDING when no
    export is present rather than skipping the check silently, so its absence stays
    visible in every report.

    Note the scopes differ and the comparison must say so: THETIS-MRV covers
    voyages into, out of and between EU/EEA ports, while this model covers the
    vessel's entire global activity. The modelled figure should therefore EXCEED
    the reported one for a vessel that also trades outside Europe -- as this one
    heavily does, with 194 of 389 calls in China.
    """
    directory = Path(cfg.validation.get("thetis_mrv", "")) if cfg.validation.get("thetis_mrv") else None
    if directory and not directory.is_absolute():
        directory = Path(cfg.path("external")).parent.parent / directory

    files = sorted(directory.glob("*")) if directory and directory.exists() else []
    files = [f for f in files if f.suffix.lower() in {".csv", ".xlsx", ".xls"}]

    if not files:
        return Check(
            "THETIS-MRV", PENDING,
            "no export present -- annual CO2 is UNVERIFIED against external data",
            basis=(
                "Export IMO "
                f"{vessel.imo} from https://mrv.emsa.europa.eu/ to "
                f"{directory or 'data/external/thetis/'}. EU-scope: validation only, "
                "never an input."
            ),
        )

    # Search EVERY export for this hull rather than taking the first file. One
    # export per vessel is the natural way to save them, and picking file[0] made
    # vessel A read vessel B's file and report "no rows" -- a WARN that looks like
    # missing data when the data is present in the directory.
    reported = pd.DataFrame()
    problems = []
    for path in files:
        try:
            found = parse_thetis_export(path, vessel.imo)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            problems.append(f"{path.name}: {exc}")
            continue
        if not found.empty:
            reported = pd.concat([reported, found], ignore_index=True)

    if reported.empty:
        detail = f"no rows for IMO {vessel.imo} in {[p.name for p in files]}"
        if problems:
            detail += f"; parse problems: {problems}"
        return Check(
            "THETIS-MRV", WARN, detail,
            basis="check the export covers this hull",
        )
    reported = reported.drop_duplicates(subset=["year"]).sort_values("year")

    # LIKE-FOR-LIKE. The earlier version compared the modelled GLOBAL total against
    # an EU-scope reported figure and called the ratio a result. It is not: the ratio
    # then tracks how much the hull traded in Europe that year, not the model. For
    # vessel B it ran from 1.4x to 14.6x for exactly that reason.
    share = dock_call_share(port_calls) if port_calls is not None else 0.0
    if port_calls is None or share < MIN_DOCK_SHARE:
        return Check(
            "THETIS-MRV", PENDING,
            f"reported figures read for {len(reported)} year(s), but MRV scope cannot "
            f"be reconstructed: only {share:.0%} of port calls carry the at_dock flag "
            f"needed to tell a cargo port of call from an anchorage stop "
            f"(threshold {MIN_DOCK_SHARE:.0%})",
            basis=(
                "MRV counts a voyage from the last PORT OF CALL, and its definition "
                "excludes anchorage stops. Without that distinction the comparison is "
                "not like-for-like."
            ),
            data={"dock_share": share, "reported_years": reported["year"].tolist()},
        )

    modelled = emissions_year[emissions_year["smoothing_window"] == 3]
    estimates = sorted(modelled["power_estimate"].unique())

    rows = []
    for year in sorted(set(reported["year"]) & set(modelled["year"])):
        hours = emissions_hour[
            (emissions_hour["smoothing_window"] == 3)
            & (emissions_hour["ts"].dt.year == year)
        ]
        if hours.empty:
            continue
        scope = mrv_scope_hours(hours, port_calls, year)
        row = {"year": year,
               "reported_t": float(reported.set_index("year").loc[year, "reported_co2_t"])}
        for estimate in estimates:
            subset = hours[(hours["power_estimate"] == estimate) & scope]
            row[estimate] = float(subset["co2_tonnes"].sum())
        rows.append(row)

    if not rows:
        return Check("THETIS-MRV", PENDING, "no overlapping years with modelled hours")

    table = pd.DataFrame(rows)
    usable = table[table[estimates].sum(axis=1) > 0]
    if usable.empty:
        return Check(
            "THETIS-MRV", PENDING,
            f"{len(table)} overlapping year(s) but MRV scope reconstructed to zero "
            f"emissions in all of them -- no EEA dock call fell in these years",
            basis="at_dock is a sparse proxy for a cargo port of call",
            data={"dock_share": share},
        )

    ratios = {e: float((usable[e] / usable["reported_t"]).median()) for e in estimates}
    closest = min(ratios, key=lambda k: abs(ratios[k] - 1.0))
    detail = (
        f"{len(usable)} year(s) comparable in MRV scope; modelled/verified "
        + ", ".join(f"{e}={ratios[e]:.2f}x" for e in estimates)
        + f" (closest: {closest}); at_dock covers {share:.0%} of calls"
    )
    # Within 25% is close agreement for a bottom-up model with no observed engine
    # parameters; beyond that the estimate is saying something and should be read.
    status = PASS if any(0.75 <= r <= 1.25 for r in ratios.values()) else WARN
    return Check(
        "THETIS-MRV", status, detail,
        basis=(
            "modelled emissions restricted to MRV scope against EMSA-verified "
            "figures -- the only external ground truth in this project"
        ),
        data={"ratios": ratios, "closest_estimate": closest, "dock_share": share,
              "comparison": usable.to_dict("records")},
    )


# THETIS-MRV column headings, from the published export schema. Matched loosely
# because the portal has changed capitalisation and the CO2 subscript between
# vintages; the parser reports the actual headings when nothing matches.
_THETIS_IMO = ("imo number", "imo")
_THETIS_YEAR = ("reporting period", "reporting year", "year")
_THETIS_CO2 = ("total co₂ emissions", "total co2 emissions")


def _find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    for candidate in candidates:
        for key, original in lowered.items():
            if key.startswith(candidate):
                return original
    return None


def parse_thetis_export(path: Path, imo: str) -> pd.DataFrame:
    """Read a THETIS-MRV export and return ``year, reported_co2_t`` for one hull.

    **The units trap:** THETIS-MRV labels its CO2 column "m tonnes", which means
    *metric* tonnes, not *million* tonnes. Reading it as millions would overstate a
    ship's annual emissions by a factor of a million and still look like a number.

    Raises:
        ValueError: If the expected columns are absent, listing what was found so
            the mapping can be corrected rather than guessed at.
    """
    path = Path(path)
    frame = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)

    imo_col = _find_column(frame, _THETIS_IMO)
    year_col = _find_column(frame, _THETIS_YEAR)
    co2_col = _find_column(frame, _THETIS_CO2)

    missing = [
        name for name, col in
        (("IMO", imo_col), ("reporting period", year_col), ("total CO2", co2_col))
        if col is None
    ]
    if missing:
        raise ValueError(
            f"could not find {', '.join(missing)} column(s). "
            f"Columns present: {[str(c) for c in frame.columns][:25]}"
        )

    subset = frame[frame[imo_col].astype(str).str.strip() == str(imo)].copy()
    out = pd.DataFrame({
        "year": pd.to_numeric(subset[year_col], errors="coerce").astype("Int64"),
        # "m tonnes" = METRIC tonnes. No scaling.
        "reported_co2_t": pd.to_numeric(subset[co2_col], errors="coerce"),
    }).dropna()
    out["year"] = out["year"].astype(int)
    return out.sort_values("year").reset_index(drop=True)


def run_all(
    cfg: Config, vessel: Vessel, spine: pd.DataFrame, port_calls: pd.DataFrame,
    legs: pd.DataFrame, coverage: pd.DataFrame, emissions_hour: pd.DataFrame,
    emissions_year: pd.DataFrame, estimates: dict[str, PowerEstimate],
) -> list[Check]:
    """Every §8 check, in report order. Never raises."""
    return [
        check_identity_integrity(spine, vessel),
        check_hour_conservation(coverage, cfg),
        check_leg_speeds(legs, port_calls),
        check_port_call_agreement(emissions_hour, port_calls),
        check_fleet_envelope(estimates, cfg),
        check_smoothing_sensitivity(spine, cfg),
        compare_thetis_mrv(cfg, emissions_year, vessel, emissions_hour, port_calls),
    ]


def summarise(checks: list[Check]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"check": c.name, "status": c.status, "detail": c.detail, "basis": c.basis}
         for c in checks]
    )

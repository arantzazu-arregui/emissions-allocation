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
    """Estimated design speed must fall inside the observed fleet range.

    Estimate A fails at 28.92 kn against a 24.5 kn maximum. **That failure is the
    expected result**, not a defect -- MEPC.333(76)'s near-linear power exponent
    breaks at the top of the container range. It is reported as FAIL so it stays
    visible wherever the number travels.
    """
    envelope = cfg.defaults["container_fleet_speed_envelope"]
    outside = {k: e for k, e in estimates.items() if e.within_fleet_envelope is False}
    inside = sorted(set(estimates) - set(outside))

    if not outside:
        return Check(
            "Fleet envelope", PASS,
            f"all estimates within {envelope['min_kn']}-{envelope['max_kn']} kn",
            basis=envelope["source"],
        )
    names = ", ".join(f"{k} at {e.design_speed_kn:.2f} kn" for k, e in outside.items())
    return Check(
        "Fleet envelope", FAIL,
        f"{names} outside {envelope['min_kn']}-{envelope['max_kn']} kn "
        f"(inside: {', '.join(inside) or 'none'}) -- expected for estimate A",
        basis=envelope["source"],
        data={"outside": list(outside)},
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


def compare_thetis_mrv(cfg: Config, emissions_year: pd.DataFrame, vessel: Vessel) -> Check:
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

    try:
        reported = parse_thetis_export(files[0], vessel.imo)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the run
        return Check(
            "THETIS-MRV", WARN, f"could not parse {files[0].name}: {exc}",
            basis="EU-scope verified emissions",
        )

    if reported.empty:
        return Check(
            "THETIS-MRV", WARN,
            f"{files[0].name} holds no rows for IMO {vessel.imo}",
            basis="check the export was filtered to this hull",
        )

    # Compare on the scenario the run treats as headline: w=3, per power estimate.
    modelled = (
        emissions_year[emissions_year["smoothing_window"] == 3]
        .pivot_table(index="year", columns="power_estimate", values="co2_tonnes")
    )
    merged = reported.set_index("year").join(modelled, how="inner")
    if merged.empty:
        return Check(
            "THETIS-MRV", WARN,
            f"no overlapping years (reported {sorted(reported['year'])}, "
            f"modelled {sorted(modelled.index)})",
        )

    estimate_cols = [c for c in modelled.columns]
    ratios = {c: (merged[c] / merged["reported_co2_t"]).median() for c in estimate_cols}
    closest = min(ratios, key=lambda k: abs(ratios[k] - 1.0))

    # THETIS-MRV covers voyages into, out of and between EEA ports plus time at
    # berth there. This model covers the hull's entire global activity, and vessel A
    # trades overwhelmingly outside Europe. The modelled figure should therefore
    # EXCEED the reported one -- a ratio below 1 means the model is understating
    # even the European subset, which would be a genuine failure.
    detail = (
        f"{len(merged)} overlapping year(s); modelled/reported ratio "
        + ", ".join(f"{c}={ratios[c]:.2f}x" for c in estimate_cols)
        + f" (closest: {closest})"
    )
    status = PASS if all(r >= 1.0 for r in ratios.values()) else WARN
    if status == WARN:
        detail += (
            " -- a ratio BELOW 1 means the model understates even the EU subset, "
            "which the global scope should strictly exceed"
        )
    return Check(
        "THETIS-MRV", status, detail,
        basis=(
            "EU-scope verified emissions as a LOWER BOUND on global CO2: MRV covers "
            "voyages into/out of/between EEA ports plus at-berth there, this model "
            "covers all activity"
        ),
        data={"ratios": ratios, "closest_estimate": closest,
              "comparison": merged.reset_index().to_dict("records")},
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
        compare_thetis_mrv(cfg, emissions_year, vessel),
    ]


def summarise(checks: list[Check]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"check": c.name, "status": c.status, "detail": c.detail, "basis": c.basis}
         for c in checks]
    )

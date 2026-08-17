"""§1 -- ship activity: presence, port visits, speed derivation and smoothing.

Produces a continuous, ordered, hourly position-and-speed series across the study
period, plus the sequence of port calls that defines the vessel's voyages.

Two things here are not cosmetic.

**The three presence assertions** run on every pull. A wrong ship name returns HTTP
200 with zero rows and no error, so :func:`emissions_allocation.gfw.assert_presence`
is the only thing standing between a typo and a silently empty emissions series.

**Smoothing is mandatory.** GFW credits each vessel-hour to a single 0.01 degree
cell. A ship crossing ~22 cells per hour lands unpredictably within them, so
consecutive-centroid speeds oscillate -- 3.36 to 21.63 kn observed while the vessel
cruised steadily at ~15 kn. Because propulsion power scales as v^3 the error does not
average out: ``mean(v^3)`` = 2,654 against ``(mean v)^3`` = 1,588, a 1.67x
overestimate, falling to 1.19x with a 3-hour centred average. The window is carried
as a sensitivity axis rather than fixed.

Ordering note. §1.5 derives speed from consecutive positions and §1.7 fills gaps by
nearest-neighbour position and linear speed, but the specification does not say which
happens first. Doing it the other way round manufactures zeros: nearest-neighbour fill
holds a position constant across a gap, so a naive derivation reads that as a
stationary vessel followed by one impossible jump. The order used here is spine ->
position fill -> derive speed on observed pairs using true elapsed time -> linearly
interpolate speed across the filled hours.

Outputs: ``vessel_hour``, ``port_call``, ``voyage_leg``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from emissions_allocation.config import Config, Vessel
from emissions_allocation.gfw import GFWClient, assert_presence, year_bounds

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
KM_PER_NAUTICAL_MILE = 1.852

# §3.1 condition 3 and §1 voyage_leg.is_eu_eu.
EU27 = frozenset({
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU",
    "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT",
    "ROU", "SVK", "SVN", "ESP", "SWE",
})


# ---------------------------------------------------------------------------
# 1.2 / 1.3 -- presence
# ---------------------------------------------------------------------------


def load_presence(
    client: GFWClient, cfg: Config, vessel: Vessel, *, years: Sequence[int] | None = None
) -> pd.DataFrame:
    """Pull hourly presence for the study period, one request per calendar year.

    Every year passes through the three mandatory assertions before it is kept.
    Records carry identity inline, so no identity join is needed -- but ``imo`` is
    not a filterable field, so the filter is on ``shipname`` and the IMO restriction
    is applied here (§1.3).

    Returns:
        One row per vessel-hour: ``imo, ts, lat, lon, hours``.
    """
    frames: list[pd.DataFrame] = []
    for year in years or cfg.years:
        records = client.presence_year(vessel.shipnames, year)
        start, end = year_bounds(year)
        kept = assert_presence(
            records, vessel.imo, start, end,
            coverage_floor=cfg.run["hour_coverage_floor"],
            coverage_warn=cfg.run["hour_coverage_warn"],
            context=f"{year} world extent",
        )
        frame = pd.DataFrame(kept)
        frame["year"] = year
        frames.append(frame)

    raw = pd.concat(frames, ignore_index=True)

    out = pd.DataFrame({
        "imo": raw["imo"].astype(str),
        # HOURLY timestamps arrive as "2024-01-15 13:00" -- space separated, no
        # timezone, no seconds.
        "ts": pd.to_datetime(raw["date"], format="%Y-%m-%d %H:%M"),
        "lat": raw["lat"].astype(float),
        # Longitude carries float32 artefacts (-118.44000244140625) while latitude
        # is clean to 2 dp. Round both so cell centroids compare equal.
        "lon": raw["lon"].astype(float).round(5),
        "hours": raw["hours"].astype(float),
    })
    return _resolve_hour_grain(out.sort_values("ts").reset_index(drop=True))


def _resolve_hour_grain(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse any hour credited to more than one cell.

    At HOURLY resolution GFW normally emits one record per vessel-hour, and the
    captured 2024 pull is exactly 1:1 (8,782 records, 8,782 hours). A vessel can in
    principle straddle two 0.01 degree cells within one hour, which would break the
    unique-position-per-hour assumption that speed derivation rests on.

    Where that happens the position is collapsed to the hours-weighted centroid and
    the affected hours are logged, so the ambiguity is visible rather than silently
    resolved by whichever row sorted first.
    """
    duplicated = frame.duplicated(subset=["imo", "ts"], keep=False)
    if not duplicated.any():
        return frame

    n_hours = frame.loc[duplicated, "ts"].nunique()
    log.warning(
        "%d vessel-hours are credited to more than one cell; collapsing to the "
        "hours-weighted centroid", n_hours,
    )
    weighted = frame.assign(
        _lat=frame["lat"] * frame["hours"], _lon=frame["lon"] * frame["hours"]
    )
    grouped = weighted.groupby(["imo", "ts"], as_index=False).agg(
        _lat=("_lat", "sum"), _lon=("_lon", "sum"), hours=("hours", "sum")
    )
    grouped["lat"] = grouped["_lat"] / grouped["hours"]
    grouped["lon"] = grouped["_lon"] / grouped["hours"]
    return (
        grouped[["imo", "ts", "lat", "lon", "hours"]]
        .sort_values("ts")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# 1.5 -- derived speed over ground
# ---------------------------------------------------------------------------


def haversine_km(
    lat1: np.ndarray | float, lon1: np.ndarray | float,
    lat2: np.ndarray | float, lon2: np.ndarray | float,
) -> np.ndarray:
    """Great-circle distance in kilometres.

    ``a = sin^2(dphi/2) + cos(phi1) cos(phi2) sin^2(dlambda/2)``,
    ``d = 2R asin(sqrt(a))`` with ``R = 6371.0088 km``.
    """
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = phi2 - phi1
    dlambda = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def derive_speed(frame: pd.DataFrame) -> pd.DataFrame:
    """Speed over ground between consecutive observed positions, in knots.

    ``SOG_i = d / (1.852 * dt)`` with ``dt`` the true elapsed hours, so a speed
    computed across a reception gap is the correct average over that gap rather than
    a spurious instantaneous value.

    ``SOG`` is the IMO's term and the AIS field name, but note the difference in
    provenance: in the Fourth GHG Study it is *transmitted* by the vessel, whereas
    here it is *derived* from consecutive cell centroids. §1.6 exists because of that.
    """
    out = frame.sort_values("ts").reset_index(drop=True).copy()
    dt_hours = out["ts"].diff().dt.total_seconds() / 3600.0
    distance = haversine_km(
        out["lat"].shift(), out["lon"].shift(), out["lat"], out["lon"]
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        out["sog_raw"] = distance / (KM_PER_NAUTICAL_MILE * dt_hours)
    out["gap_hours"] = dt_hours
    return out


# ---------------------------------------------------------------------------
# 1.7 -- hourly spine and gap filling
# ---------------------------------------------------------------------------


def build_spine(
    frame: pd.DataFrame, start: datetime, end: datetime, imo: str
) -> pd.DataFrame:
    """Reindex onto a complete hourly grid, filling gaps per §1.7.

    Position is filled by nearest neighbour in time and speed linearly, following
    the source paper. Every filled hour is flagged.

    Args:
        frame: Observed vessel-hours carrying ``sog_raw``.
        start: Period start.
        end: Period end, exclusive.
        imo: The hull's IMO, written onto filled rows.
    """
    index = pd.date_range(start, end - timedelta(hours=1), freq="h", name="ts")
    observed = frame.set_index("ts").sort_index()

    # Exact join first, purely to record which hours were actually observed. This
    # has to happen before any filling, or every hour would look observed.
    exact = observed.reindex(index)
    is_interpolated = exact["lat"].isna()

    # Nearest-neighbour position: whichever observed fix is closer in time. Done
    # with reindex rather than Series.interpolate(method="nearest"), which routes
    # through scipy and would add a dependency for this one call.
    nearest = observed[["lat", "lon"]].reindex(index, method="nearest")

    out = pd.DataFrame({
        "imo": imo,
        "ts": index,
        "lat": nearest["lat"].to_numpy(),
        "lon": nearest["lon"].to_numpy(),
        "hours": exact["hours"].fillna(0.0).to_numpy(),
        # Linear speed. Doing this after the position fill is deliberate -- deriving
        # speed from nearest-neighbour positions would read a gap as a stationary
        # vessel followed by one impossible jump.
        "sog_raw": exact["sog_raw"].interpolate(method="linear", limit_direction="both").to_numpy(),
        "is_interpolated": is_interpolated.to_numpy(),
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1.6 -- smoothing
# ---------------------------------------------------------------------------


def smooth_speed(sog: pd.Series, window: int) -> pd.Series:
    """Centred moving average of width ``w``.

    ``SOG_bar_i = (1/w) * sum SOG_{i+k}`` for ``k = -(w-1)/2 ... +(w-1)/2``.

    ``w = 1`` returns the series unchanged; it is carried as the unsmoothed baseline
    that makes the v^3 bias visible. ``min_periods=1`` keeps the series endpoints
    rather than truncating the first and last (w-1)/2 hours of the study period.
    """
    if window % 2 == 0:
        raise ValueError(f"smoothing window must be odd (centred average); got {window}")
    if window == 1:
        return sog.copy()
    return sog.rolling(window=window, center=True, min_periods=1).mean()


def add_smoothed_speeds(frame: pd.DataFrame, windows: Iterable[int]) -> pd.DataFrame:
    """Add one ``sog_w{n}`` column per configured smoothing window.

    Smoothing runs **within contiguous in-service segments**, never across an
    out-of-service boundary. A centred average that straddles a 282-day lay-up
    would blend the speed of the last voyage before it into the first voyage after,
    which is not a smoothing artefact to be tolerated but a fabricated value: those
    hours are not neighbours in any physical sense.

    Requires ``is_inactive``, so :func:`classify_gaps` must run first. Falls back to
    treating the whole series as one segment when the column is absent.
    """
    out = frame.copy()
    if "is_inactive" not in out.columns:
        for window in windows:
            out[f"sog_w{window}"] = smooth_speed(out["sog_raw"], window)
        return out

    # Each maximal run of in-service hours is smoothed independently.
    segment = (out["is_inactive"] != out["is_inactive"].shift()).cumsum()
    for window in windows:
        column = f"sog_w{window}"
        out[column] = (
            out.groupby(segment)["sog_raw"]
            .transform(lambda s: smooth_speed(s, window))
        )
        out.loc[out["is_inactive"], column] = np.nan
    return out


def cubic_bias(sog: pd.Series) -> float:
    """``mean(v^3) / (mean v)^3`` -- the quantity §1.6 exists to reduce.

    Reported per smoothing window so the bias reduction is an output rather than an
    assertion. Measured 1.67x unsmoothed and 1.19x at a 3-hour window.
    """
    moving = sog.dropna()
    moving = moving[moving > 0]
    if moving.empty:
        return float("nan")
    return float((moving**3).mean() / moving.mean() ** 3)


# ---------------------------------------------------------------------------
# 1.4 -- port visits
# ---------------------------------------------------------------------------


def load_port_visits(client: GFWClient, cfg: Config, vessel: Vessel) -> pd.DataFrame:
    """Pull port visits across every vesselId belonging to the hull, and union them.

    One hull has several vesselIds -- IMO 9516454 has two, one carrying a null IMO
    and a former name. Pulling only the current one silently drops history. Events
    are deduplicated on the event id after the union.

    Note the payload's casing: ``port_visit`` is snake_case inside an otherwise
    camelCase response, ``confidence`` and ``distanceFromShoreKm`` come back as
    strings, and ``anchorage.name`` is null about 20% of the time while
    ``topDestination`` is always populated.
    """
    start = cfg.start_date.isoformat()
    end = cfg.end_date.isoformat()

    events: list[dict[str, Any]] = []
    for vessel_id in client.vessel_ids_for_imo(vessel.imo):
        events.extend(client.port_visits(vessel_id, start, end))

    seen: set[str] = set()
    unique = []
    for event in events:
        if event.get("id") not in seen:
            seen.add(event["id"])
            unique.append(event)

    return _parse_port_visits(unique, vessel.imo)


def _parse_port_visits(events: Sequence[dict[str, Any]], imo: str) -> pd.DataFrame:
    rows = []
    for event in events:
        visit = event.get("port_visit") or {}
        start_anchorage = visit.get("startAnchorage") or {}
        end_anchorage = visit.get("endAnchorage") or {}
        regions = event.get("regions") or {}
        eez = regions.get("eez") or []

        rows.append({
            "imo": imo,
            "event_id": event.get("id"),
            "visit_id": visit.get("visitId"),
            "start_ts": pd.to_datetime(event.get("start")),
            "end_ts": pd.to_datetime(event.get("end")),
            "duration_h": float(visit.get("durationHrs") or 0.0),
            "port_id": start_anchorage.get("id"),
            # Port country is the anchorage flag, an ISO3 code.
            "port_iso3": start_anchorage.get("flag"),
            # `name` is null ~20% of the time; `topDestination` never is.
            "port_name": start_anchorage.get("topDestination"),
            "end_port_id": end_anchorage.get("id"),
            "end_port_iso3": end_anchorage.get("flag"),
            "lat": start_anchorage.get("lat"),
            "lon": start_anchorage.get("lon"),
            # A visit can begin at one anchorage and end at another; both are
            # places the vessel was, so both feed the §4.1 port-distance point set.
            "end_lat": end_anchorage.get("lat"),
            "end_lon": end_anchorage.get("lon"),
            "at_dock": bool(start_anchorage.get("atDock")),
            # Arrives as a string.
            "confidence": str(visit.get("confidence")) if visit.get("confidence") else None,
            # MRGIDs, which join cleanly to EEZ v12.
            "eez_mrgid": eez[0] if eez else None,
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values("start_ts").reset_index(drop=True)


def is_eu(iso3: str | None) -> bool:
    return bool(iso3) and iso3 in EU27


# ---------------------------------------------------------------------------
# 1.7 -- coverage
# ---------------------------------------------------------------------------


class PresenceGapError(AssertionError):
    """A presence gap contains port calls, so the presence pull is broken.

    Presence and port visits come from different endpoints reached by different
    query paths -- 4Wings filtered on ``shipname``, ``/v3/events`` keyed on
    ``vesselId``. When one says the vessel was absent and the other says it called
    at a port, the disagreement is a data failure, not a property of the vessel.
    """


def find_gaps(frame: pd.DataFrame, min_hours: int = 1) -> pd.DataFrame:
    """Contiguous runs of unobserved hours in the spine.

    Returns:
        ``start_ts, end_ts, hours`` per run, longest first.
    """
    missing = frame.loc[frame["is_interpolated"], "ts"].sort_values().reset_index(drop=True)
    if missing.empty:
        return pd.DataFrame(columns=["start_ts", "end_ts", "hours"])

    run_id = (missing.diff() != pd.Timedelta(hours=1)).cumsum()
    runs = missing.groupby(run_id).agg(["min", "max", "count"])
    runs.columns = ["start_ts", "end_ts", "hours"]
    runs = runs[runs["hours"] >= min_hours]
    return runs.sort_values("hours", ascending=False).reset_index(drop=True)


def classify_gaps(
    frame: pd.DataFrame, port_calls: pd.DataFrame, inactivity_gap_days: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate genuine inactivity from missed reception, and flag the spine.

    Gap *structure* is what distinguishes them, not gap size:

    * Many short scattered runs mean thin reception -- the vessel was trading and
      hours were missed. These are correctable by §4.5's ``E / coverage``.
    * One long contiguous run with **no port calls inside it** means the hull was
      out of service. Scaling those hours up would fabricate voyages: applied to
      vessel A's 2019, the raw 36.1% coverage would multiply emissions by 2.77x for
      a ship that was laid up for 282 days.

    Adds ``is_inactive`` to the spine. Inactive hours carry no emissions and are
    excluded from the coverage denominator, rather than being scaled up.

    Returns:
        ``(spine_with_is_inactive, inactivity_windows)``.

    Raises:
        PresenceGapError: If a long gap contains port calls.
    """
    out = frame.copy()
    out["is_inactive"] = False

    gaps = find_gaps(frame, min_hours=inactivity_gap_days * 24)
    if gaps.empty:
        return out, gaps

    assert_gaps_have_no_port_calls(gaps, port_calls)

    for gap in gaps.itertuples():
        window = (out["ts"] >= gap.start_ts) & (out["ts"] <= gap.end_ts)
        out.loc[window, "is_inactive"] = True
        log.warning(
            "IMO %s out of service %s -> %s (%d days): no presence hours and no "
            "port calls. Excluded from the coverage denominator; no coverage "
            "correction applied to these hours.",
            out["imo"].iloc[0] if len(out) else "?",
            gap.start_ts.date(), gap.end_ts.date(), gap.hours // 24,
        )
    return out, gaps


def assert_gaps_have_no_port_calls(
    gaps: pd.DataFrame, port_calls: pd.DataFrame
) -> None:
    """Cross-endpoint consistency check -- the strong form of assertion (c).

    A truncated presence response and a genuinely absent vessel look identical in
    an hours ratio. They do not look identical here: if the events endpoint places
    a port call inside a window where presence reports nothing, the presence pull
    lost data.

    Raises:
        PresenceGapError: If any gap contains a port call.
    """
    if port_calls.empty or gaps.empty:
        return

    starts = pd.to_datetime(port_calls["start_ts"], utc=True)
    failures = []
    for gap in gaps.itertuples():
        lo = pd.Timestamp(gap.start_ts, tz="UTC")
        hi = pd.Timestamp(gap.end_ts, tz="UTC")
        inside = port_calls[(starts >= lo) & (starts <= hi)]
        if not inside.empty:
            failures.append(
                f"    {lo.date()} -> {hi.date()} ({int(gap.hours)} h) contains "
                f"{len(inside)} port call(s): "
                f"{', '.join(inside['port_id'].dropna().astype(str).head(5))}"
            )

    if failures:
        raise PresenceGapError(
            "presence gaps contain port calls, so the presence pull lost data:\n"
            + "\n".join(failures)
            + "\n  Presence is filtered on shipname; port visits are keyed on "
            "vesselId. A port call inside a presence gap usually means the hull "
            "traded under a name that is not listed in config/vessel_specs.yaml "
            "shipnames. Check the rename history before proceeding."
        )


def coverage_by_year(frame: pd.DataFrame) -> pd.DataFrame:
    """Coverage per calendar year, separating inactivity from missed reception.

    Computed here rather than taken from the Insights endpoint, which only reaches
    back to 2020 and counts a different unit. The two agreed to within 0.08% for
    2024 (99.98% here against 99.897% there).

    Two coverage figures are reported per year, and they mean different things:

    ``coverage_raw``
        observed / elapsed. Transparency only.
    ``coverage_active``
        observed / (elapsed - inactive). **This is the §4.5 divisor.** Hours where
        the hull was out of service are removed from the denominator rather than
        scaled up, so a lay-up cannot fabricate voyages.

    For vessel A's 2019 the two differ sharply: 36.1% raw against 82.0% active,
    the latter matching 2017's reception quality once the 282-day absence is set
    aside.
    """
    work = frame.copy()
    work["year"] = work["ts"].dt.year
    if "is_inactive" not in work.columns:
        work["is_inactive"] = False

    by_year = work.groupby("year", as_index=False).agg(
        elapsed_hours=("ts", "count"),
        observed_hours=("is_interpolated", lambda s: int((~s).sum())),
        inactive_hours=("is_inactive", "sum"),
    )
    by_year["active_hours"] = by_year["elapsed_hours"] - by_year["inactive_hours"]
    by_year["interpolated_hours"] = (
        by_year["active_hours"] - by_year["observed_hours"]
    )
    by_year["coverage_raw"] = by_year["observed_hours"] / by_year["elapsed_hours"]
    by_year["coverage_active"] = (
        by_year["observed_hours"] / by_year["active_hours"].replace(0, pd.NA)
    ).astype(float)
    by_year["imo"] = work["imo"].iloc[0] if len(work) else None
    return by_year[[
        "imo", "year", "elapsed_hours", "inactive_hours", "active_hours",
        "observed_hours", "interpolated_hours", "coverage_raw", "coverage_active",
    ]]


def build_voyage_legs(*args, **kwargs):
    """Consecutive port pairs. Implemented in SQL -- see ``12_voyage_leg.sql``."""
    raise NotImplementedError(
        "voyage legs are built in SQL (LAG over port_call). Use "
        "Database.table_from('voyage_leg', '12_voyage_leg')."
    )

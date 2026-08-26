"""§3 -- ship activity: presence, port visits, speed derivation and smoothing.

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

Ordering note. §3.3 derives speed from consecutive positions and §3.4 fills gaps by
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
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from emissions_allocation.config import Config, Vessel
from emissions_allocation.gfw import GFWClient, assert_presence, year_bounds

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
KM_PER_NAUTICAL_MILE = 1.852

# §5.1 condition 3 and §3.2 voyage_leg.is_eu_eu.
EU27 = frozenset({
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU",
    "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT",
    "ROU", "SVK", "SVN", "ESP", "SWE",
})

# The Brexit transition ended on 31 December 2020. Fuel-leg construction uses
# this cutoff so "EU port" has one consistent meaning.
UK_IN_EU_THROUGH = 2020


def eu_countries(year: int) -> frozenset[str]:
    """EU members applicable to a calendar year.

    Parameters
    ----------
    year : int
        Calendar year of the port call or voyage leg.

    Returns
    -------
    frozenset[str]
        EU27 plus the United Kingdom through 2020.
    """
    return EU27 | ({"GBR"} if year <= UK_IN_EU_THROUGH else set())


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
    is applied here (§3.1).

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


def load_observed_presence(
    client: GFWClient, cfg: Config, vessel: Vessel
) -> pd.DataFrame:
    """Acquire GFW presence across its complete available archive for one vessel.

    Parameters
    ----------
    client : GFWClient
        Authenticated Global Fishing Watch API client.
    cfg : Config
        Pipeline configuration, including the GFW archive start and rolling lag.
    vessel : Vessel
        Configured vessel whose exact name history is queried.

    Returns
    -------
    pandas.DataFrame
        Observed hourly positions from 2012 through the API availability boundary.
        Empty calendar years are retained later as ``unobserved`` in the yearly
        activity table; they are not treated as API failures.

    Notes
    -----
    This is deliberately separate from :func:`load_presence`, which remains the
    strict, fixed-study-window input to the emissions model. GFW zero observations
    cannot establish that a ship was inactive.
    """
    frames: list[pd.DataFrame] = []
    start = cfg.gfw_observation_start_date
    end = cfg.gfw_observation_end_date
    cursor = start
    while cursor < end:
        next_year = date(cursor.year + 1, 1, 1)
        chunk_end = min(next_year, end)
        if cursor == date(cursor.year, 1, 1) and chunk_end == next_year:
            records = client.presence_year(vessel.shipnames, cursor.year)
        else:
            records = client.presence_range(
                vessel.shipnames, cursor.isoformat(), chunk_end.isoformat()
            )
        if records:
            kept = assert_presence(
                records, vessel.imo,
                datetime.combine(cursor, datetime.min.time()),
                datetime.combine(chunk_end, datetime.min.time()),
                coverage_floor=0.0,
                coverage_warn=0.0,
                context=f"{cursor} to {chunk_end} GFW archive",
            )
            frames.append(pd.DataFrame(kept))
        cursor = chunk_end

    if not frames:
        return pd.DataFrame(columns=["imo", "ts", "lat", "lon", "hours"])
    raw = pd.concat(frames, ignore_index=True)
    out = pd.DataFrame({
        "imo": raw["imo"].astype(str),
        "ts": pd.to_datetime(raw["date"], format="%Y-%m-%d %H:%M"),
        "lat": raw["lat"].astype(float),
        "lon": raw["lon"].astype(float).round(5),
        "hours": raw["hours"].astype(float),
    })
    return _resolve_hour_grain(out.sort_values("ts").reset_index(drop=True))


def observed_activity_by_year(
    presence: pd.DataFrame,
    vessel: Vessel,
    start_date: date,
    end_date: date,
    min_observed_hours: int,
    min_observed_days: int,
) -> pd.DataFrame:
    """Classify each calendar year as GFW-observed active or unobserved.

    Parameters
    ----------
    presence : pandas.DataFrame
        GFW hourly presence with ``ts`` and ``hours`` columns.
    vessel : Vessel
        Vessel represented by the observations.
    start_date, end_date : datetime.date
        Inclusive archive bounds for reporting years.
    min_observed_hours : int
        Minimum observed AIS-presence hours for ``observed_active``.
    min_observed_days : int
        Minimum distinct calendar days with observations for that label.

    Returns
    -------
    pandas.DataFrame
        One row per vessel-year, with observation counts and an ``activity_state``.

    Notes
    -----
    ``unobserved`` is not an IMO inactive status. It only records insufficient GFW
    evidence, so it prevents a fleet analysis from converting AIS non-detection
    into a claim that the ship was inactive.
    """
    years = pd.DataFrame({"year": range(start_date.year, end_date.year + 1)})
    if presence.empty:
        grouped = pd.DataFrame(columns=["year", "observed_hours", "observed_days"])
    else:
        observed = presence.assign(
            year=presence["ts"].dt.year,
            day=presence["ts"].dt.normalize(),
        )
        grouped = observed.groupby("year", as_index=False).agg(
            observed_hours=("hours", "sum"), observed_days=("day", "nunique")
        )
    out = years.merge(grouped, on="year", how="left").fillna(0)
    out["imo"] = vessel.imo
    is_observed = (
        (out["observed_hours"] >= min_observed_hours)
        & (out["observed_days"] >= min_observed_days)
    )
    out["activity_state"] = np.where(is_observed, "observed_active", "unobserved")
    return out[["imo", "year", "observed_hours", "observed_days", "activity_state"]]


def assert_study_years_observed_active(activity: pd.DataFrame, cfg: Config, vessel: Vessel) -> None:
    """Require the GFW-observed activity screen for every analysis year."""
    eligible = set(activity.loc[activity["activity_state"] == "observed_active", "year"])
    missing = sorted(set(cfg.years) - eligible)
    if missing:
        raise ValueError(
            f"IMO {vessel.imo} is unobserved by GFW in study year(s) {missing} under "
            "the configured observed-activity threshold. This is not evidence of "
            "IMO inactivity; obtain a registry status source or exclude the vessel."
        )


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
    here it is *derived* from consecutive cell centroids. §3.3 exists because of that.
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
    """Reindex onto a complete hourly grid, filling gaps per §3.4.

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


def _port_visit_mask(
    frame: pd.DataFrame, port_calls: pd.DataFrame | None
) -> pd.Series:
    """Identify spine hours inside a GFW port-visit interval.

    Parameters
    ----------
    frame : pandas.DataFrame
        Hourly vessel spine with a ``ts`` timestamp column.
    port_calls : pandas.DataFrame, optional
        GFW port visits with ``start_ts`` and ``end_ts``. When omitted, an existing
        ``in_port_visit`` column is used; otherwise every hour is treated as
        underway.

    Returns
    -------
    pandas.Series
        Boolean mask aligned to ``frame``.

    Notes
    -----
    Port-event times are UTC-aware whereas the hourly spine is deliberately naive,
    so event timestamps are normalised before comparison.
    """
    if port_calls is None:
        return frame.get("in_port_visit", pd.Series(False, index=frame.index)).astype(bool)
    required = {"start_ts", "end_ts"}
    missing = required - set(port_calls.columns)
    if missing:
        raise ValueError(f"port_calls is missing required columns: {sorted(missing)}")

    in_port = pd.Series(False, index=frame.index)
    for call in port_calls[["start_ts", "end_ts"]].itertuples(index=False):
        start, end = (pd.Timestamp(value) for value in call)
        if start.tz is not None:
            start = start.tz_localize(None)
        if end.tz is not None:
            end = end.tz_localize(None)
        in_port |= (frame["ts"] >= start) & (frame["ts"] <= end)
    return in_port


def add_smoothed_speeds(
    frame: pd.DataFrame,
    windows: Iterable[int],
    port_calls: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add one ``sog_w{n}`` column per configured smoothing window.

    Smoothing runs **within contiguous underway segments**, never across an
    out-of-service or port-visit boundary. A centred average that straddles a
    282-day lay-up or a berth/departure boundary would fabricate a speed for hours
    that are not physical neighbours. Port-visit speeds are left unsmoothed so the
    Table 16 berth/anchor thresholds retain their observed values.

    Requires ``is_inactive``, so :func:`classify_gaps` must run first. ``port_calls``
    is the parsed GFW event table used to delimit voyage segments. If either input is
    absent, the relevant boundary type is simply not available.
    """
    out = frame.copy()
    inactive = out.get("is_inactive", pd.Series(False, index=out.index)).astype(bool)
    in_port = _port_visit_mask(out, port_calls)
    out["in_port_visit"] = in_port
    underway = ~(inactive | in_port)
    segment = underway.ne(underway.shift()).cumsum()
    for window in windows:
        column = f"sog_w{window}"
        out[column] = out["sog_raw"]
        out.loc[underway, column] = (
            out.loc[underway].groupby(segment.loc[underway])["sog_raw"]
            .transform(lambda s: smooth_speed(s, window))
        )
        out.loc[inactive, column] = np.nan
    return out


def add_imo2020_port_phase_sensitivity(
    frame: pd.DataFrame,
    port_calls: pd.DataFrame,
    windows: Iterable[int],
    *,
    transition_hours: int = 6,
    min_gap_hours: float = 6.0,
    max_gap_hours: float = 72.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add an adapted Fourth IMO GHG Study SOG-infill sensitivity branch.

    Parameters
    ----------
    frame : pandas.DataFrame
        Complete hourly vessel spine with ``sog_raw``, ``is_interpolated`` and
        ``is_inactive`` columns. ``sog_raw`` remains the primary linear-infill
        series and is never modified.
    port_calls : pandas.DataFrame
        GFW port-visit events carrying ``start_ts`` and ``end_ts``. These events
        define port-to-port voyages because an absent GFW presence hour has no AIS
        activity report from which the Fourth Study's original SOG phase rule can
        be evaluated.
    windows : iterable of int
        Odd centred smoothing-window widths in hours.
    transition_hours : int, default=6
        Hours adjacent to each port-call boundary classified as transition.
    min_gap_hours, max_gap_hours : float, default=6, 72
        Bounds imposed on the median inter-port voyage duration when deciding
        whether a missing run is short enough for phase-mean infill.

    Returns
    -------
    tuple[pandas.DataFrame, pandas.DataFrame]
        A copied spine with ``imo2020_phase``, ``sog_imo2020_raw`` and one
        ``sog_imo2020_w<n>`` column per smoothing window, plus a one-row audit
        table containing the threshold and affected hours.

    Notes
    -----
    This is a sensitivity experiment, not the primary gap treatment. The Fourth
    IMO GHG Study applies its method where an activity record exists but SOG is
    missing. GFW presence gaps lack position and SOG together, and its
    centroid-derived SOG cannot reliably reproduce the study's 90th-percentile
    voyage-phase classifier. Port events therefore provide the phase proxy here.

    Only short, non-inactive reception gaps are replaced with the mean *observed*
    SOG for their port-event phase. Long gaps retain the primary linear-infill
    value and remain covered by the existing active-coverage correction; replacing
    a whole voyage would create unsupportable spatial activity.
    """
    if transition_hours < 0:
        raise ValueError("transition_hours must be non-negative")
    if min_gap_hours <= 0 or max_gap_hours < min_gap_hours:
        raise ValueError("gap-hour bounds must satisfy 0 < min <= max")

    out = frame.copy()
    if "is_inactive" not in out.columns:
        out["is_inactive"] = False
    out["imo2020_phase"] = "voyage"
    out.loc[out["is_inactive"], "imo2020_phase"] = "inactive"

    # Port events are UTC-aware whereas the hourly spine is deliberately naive.
    # Convert both boundaries before comparing them to the spine timestamps.
    for call in port_calls.itertuples():
        start = pd.Timestamp(call.start_ts)
        end = pd.Timestamp(call.end_ts)
        if start.tz is not None:
            start = start.tz_localize(None)
        if end.tz is not None:
            end = end.tz_localize(None)
        in_port = (out["ts"] >= start) & (out["ts"] <= end) & ~out["is_inactive"]
        out.loc[in_port, "imo2020_phase"] = "port"
        before = (
            (out["ts"] >= start - pd.Timedelta(hours=transition_hours))
            & (out["ts"] < start)
            & ~out["is_inactive"]
            & (out["imo2020_phase"] != "port")
        )
        after = (
            (out["ts"] > end)
            & (out["ts"] <= end + pd.Timedelta(hours=transition_hours))
            & ~out["is_inactive"]
            & (out["imo2020_phase"] != "port")
        )
        out.loc[before | after, "imo2020_phase"] = "transition"

    calls = port_calls.copy()
    if calls.empty:
        inter_port_hours = pd.Series(dtype=float)
    else:
        for column in ("start_ts", "end_ts"):
            calls[column] = pd.to_datetime(calls[column], utc=True).dt.tz_localize(None)
        calls = calls.sort_values("start_ts")
        inter_port_hours = (
            calls["start_ts"].shift(-1) - calls["end_ts"]
        ).dt.total_seconds().div(3600)
        inter_port_hours = inter_port_hours[inter_port_hours > 0]
    median_hours = float(inter_port_hours.median()) if not inter_port_hours.empty else min_gap_hours
    threshold_hours = float(np.clip(median_hours, min_gap_hours, max_gap_hours))

    observed = (~out["is_interpolated"]) & (~out["is_inactive"]) & out["sog_raw"].notna()
    phase_means = out.loc[observed].groupby("imo2020_phase")["sog_raw"].mean()
    fallback_speed = float(out.loc[observed, "sog_raw"].mean()) if observed.any() else 0.0
    phase_means = phase_means.reindex(["port", "transition", "voyage"]).fillna(fallback_speed)

    out["sog_imo2020_raw"] = out["sog_raw"]
    gaps = find_gaps(out.loc[~out["is_inactive"]].copy())
    short_gaps = gaps[gaps["hours"] < threshold_hours]
    for gap in short_gaps.itertuples():
        in_gap = (out["ts"] >= gap.start_ts) & (out["ts"] <= gap.end_ts)
        phases = out.loc[in_gap, "imo2020_phase"]
        out.loc[in_gap, "sog_imo2020_raw"] = phases.map(phase_means).to_numpy()

    # Maintain the no-cross-lay-up or port-visit rule used by the primary branch.
    in_port = _port_visit_mask(out, port_calls)
    out["in_port_visit"] = in_port
    inactive = out["is_inactive"].astype(bool)
    underway = ~(inactive | in_port)
    segment = underway.ne(underway.shift()).cumsum()
    for window in windows:
        if window % 2 == 0:
            raise ValueError(f"smoothing window must be odd (centred); got {window}")
        column = f"sog_imo2020_w{window}"
        out[column] = out["sog_imo2020_raw"]
        out.loc[underway, column] = out.loc[underway].groupby(
            segment.loc[underway]
        )["sog_imo2020_raw"].transform(
            lambda speed: smooth_speed(speed, window)
        )
        out.loc[inactive, column] = np.nan

    audit = pd.DataFrame([{
        "imo": out["imo"].iloc[0] if len(out) else None,
        "method": "imo2020_port_phase",
        "transition_hours": transition_hours,
        "median_inter_port_hours": median_hours,
        "missing_gap_threshold_hours": threshold_hours,
        "short_gap_runs": len(short_gaps),
        "short_gap_hours": int(short_gaps["hours"].sum()),
        "long_gap_runs": int((gaps["hours"] >= threshold_hours).sum()),
        "long_gap_hours": int(gaps.loc[gaps["hours"] >= threshold_hours, "hours"].sum()),
        "port_mean_sog_kn": float(phase_means["port"]),
        "transition_mean_sog_kn": float(phase_means["transition"]),
        "voyage_mean_sog_kn": float(phase_means["voyage"]),
    }])
    return out, audit


def cubic_bias(sog: pd.Series) -> float:
    """``mean(v^3) / (mean v)^3`` -- the quantity §3.3 exists to reduce.

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
            # places the vessel was, so both feed the §5.2 port-distance point set.
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


def is_eu(iso3: str | None, year: int) -> bool:
    """Whether an ISO3 country was an EU member in a reporting year.

    Parameters
    ----------
    iso3 : str or None
        Port-country ISO3 code.
    year : int
        Calendar year controlling the United Kingdom's membership.

    Returns
    -------
    bool
        True for EU ports under the year-aware fuel-rule definition.
    """
    return bool(iso3) and iso3 in eu_countries(year)


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
      hours were missed. A coverage-scaled branch would correct them with §3.4's
      ``E / coverage``; the configured primary run instead models interpolated hours.
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


def coverage_by_year(frame: pd.DataFrame, db=None) -> pd.DataFrame:
    """Coverage per calendar year, separating inactivity from missed reception.

    The aggregation itself is SQL (``13_coverage.sql``) -- grouped counts are what
    the architecture split assigns to DuckDB. A connection is opened for the caller
    when none is supplied, so this stays a one-line call.

    Two coverage figures are returned and they mean different things:

    ``coverage_raw``
        observed / elapsed. Transparency only.
    ``coverage_active``
        observed / (elapsed - inactive). **This is the §3.4 divisor.** Out-of-service
        hours leave the denominator rather than being scaled up, so a lay-up cannot
        fabricate voyages.

    For vessel A's 2019 the two differ sharply: 36.1% raw against 82.0% active, the
    latter matching 2017's reception quality once the 282-day absence is set aside.
    """
    from emissions_allocation.db import Database

    work = frame.copy()
    if "is_inactive" not in work.columns:
        work["is_inactive"] = False

    owned = db is None
    database = Database(spatial=False) if owned else db
    try:
        database.register_frame("vessel_hour", work)
        out = database.sql("13_coverage").df()
    finally:
        if owned:
            database.close()
    return out


def build_voyage_legs(*args, **kwargs):
    """Consecutive port pairs. Implemented in SQL -- see ``12_voyage_leg.sql``."""
    raise NotImplementedError(
        "voyage legs are built in SQL (LAG over port_call). Use "
        "Database.table_from('voyage_leg', '12_voyage_leg')."
    )

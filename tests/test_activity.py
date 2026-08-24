"""§1 -- speed derivation, smoothing, the hourly spine and port-visit parsing.

Fixtures are the captured responses in ``data/sample/api/``, so the parsers are
tested against the payload GFW actually returns -- including its casing traps
(``shipName`` vs ``shipname``, snake_case ``port_visit`` inside a camelCase body,
``confidence`` and ``distanceFromShoreKm`` as strings).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from emissions_allocation.activity import (
    EU27,
    _parse_port_visits,
    add_imo2020_port_phase_sensitivity,
    add_smoothed_speeds,
    build_spine,
    coverage_by_year,
    cubic_bias,
    derive_speed,
    haversine_km,
    is_eu,
    observed_activity_by_year,
    smooth_speed,
)
from emissions_allocation.config import load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SAMPLES = PROJECT_ROOT / "data" / "sample" / "api"
VESSEL_A = "9516454"


def test_observed_activity_distinguishes_unobserved_from_inactive() -> None:
    """No GFW receipt is an unknown observation state, not vessel inactivity."""
    cfg = load_config()
    vessel = cfg.vessel(VESSEL_A)
    presence = pd.DataFrame({
        "ts": pd.to_datetime([
            "2017-01-01 00:00", "2017-01-02 00:00", "2017-01-03 00:00",
        ]),
        "hours": [24.0, 24.0, 24.0],
    })
    out = observed_activity_by_year(
        presence, vessel, date(2017, 1, 1), date(2018, 12, 31),
        min_observed_hours=24, min_observed_days=3,
    )
    assert out.set_index("year").loc[2017, "activity_state"] == "observed_active"
    assert out.set_index("year").loc[2018, "activity_state"] == "unobserved"


# ---------------------------------------------------------------------------
# 1.5 -- great-circle distance
# ---------------------------------------------------------------------------


def test_haversine_zero_distance() -> None:
    assert haversine_km(33.7, -118.2, 33.7, -118.2) == pytest.approx(0.0, abs=1e-9)


def test_haversine_one_degree_of_latitude() -> None:
    """One degree of latitude is ~111.19 km on a sphere of radius 6371.0088 km."""
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.195, abs=0.01)


def test_haversine_known_long_distance() -> None:
    """Los Angeles to Shanghai, roughly 10,400 km."""
    assert haversine_km(33.74, -118.27, 31.23, 121.47) == pytest.approx(10_400, rel=0.02)


def test_haversine_is_symmetric() -> None:
    forward = haversine_km(33.7, -118.2, 35.1, 128.8)
    backward = haversine_km(35.1, 128.8, 33.7, -118.2)
    assert forward == pytest.approx(backward)


def test_haversine_crosses_the_antimeridian() -> None:
    """0.02 degrees of longitude at the dateline, not 359.98."""
    assert haversine_km(0.0, 179.99, 0.0, -179.99) == pytest.approx(2.224, abs=0.01)


def test_haversine_vectorises() -> None:
    result = haversine_km(
        np.array([0.0, 0.0]), np.array([0.0, 0.0]),
        np.array([1.0, 2.0]), np.array([0.0, 0.0]),
    )
    assert result.shape == (2,)
    assert result[1] == pytest.approx(2 * result[0], rel=1e-6)


# ---------------------------------------------------------------------------
# 1.5 -- derived speed
# ---------------------------------------------------------------------------


def _track(positions: list[tuple[float, float]], start: str = "2024-01-15 00:00") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(positions), freq="h")
    return pd.DataFrame({
        "imo": VESSEL_A,
        "ts": index,
        "lat": [p[0] for p in positions],
        "lon": [p[1] for p in positions],
        "hours": 1.0,
    })


def test_speed_of_a_stationary_vessel_is_zero() -> None:
    out = derive_speed(_track([(33.7, -118.2)] * 4))
    assert out["sog_raw"].iloc[1:].eq(0.0).all()


def test_first_hour_has_no_speed() -> None:
    """There is no preceding position to difference against."""
    assert pd.isna(derive_speed(_track([(0.0, 0.0), (0.0, 0.1)]))["sog_raw"].iloc[0])


def test_speed_matches_a_hand_computed_leg() -> None:
    """0.25 degrees of latitude in one hour = 27.8 km/h = 15.0 kn."""
    out = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    assert out["sog_raw"].iloc[1] == pytest.approx(15.0, abs=0.05)


def test_speed_across_a_gap_uses_true_elapsed_time() -> None:
    """A speed computed over a reception gap must be the average over that gap.

    Dividing by a hard-coded 1 h instead of the real interval would report a
    24-hour crossing as a 24x overspeed.
    """
    frame = _track([(0.0, 0.0), (0.25, 0.0)])
    frame.loc[1, "ts"] = frame.loc[0, "ts"] + pd.Timedelta(hours=2)
    out = derive_speed(frame)
    assert out["sog_raw"].iloc[1] == pytest.approx(7.5, abs=0.05)
    assert out["gap_hours"].iloc[1] == 2.0


def test_cruising_speed_is_plausible() -> None:
    """A container ship at ~0.25 deg/h sits in the mid-teens, not the hundreds."""
    out = derive_speed(_track([(0.0, 0.0), (0.25, 0.0), (0.50, 0.0), (0.75, 0.0)]))
    assert out["sog_raw"].iloc[1:].between(6, 25).all()


# ---------------------------------------------------------------------------
# 1.6 -- smoothing
# ---------------------------------------------------------------------------


def test_window_of_one_is_the_identity() -> None:
    """w=1 is the unsmoothed baseline carried through the sensitivity analysis."""
    sog = pd.Series([3.36, 21.63, 4.1, 19.8, 15.0])
    pd.testing.assert_series_equal(smooth_speed(sog, 1), sog)


def test_even_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be odd"):
        smooth_speed(pd.Series([1.0, 2.0]), 2)


def test_smoothing_is_centred_not_trailing() -> None:
    """A trailing average would shift the whole series one step late."""
    sog = pd.Series([0.0, 3.0, 0.0])
    assert smooth_speed(sog, 3).iloc[1] == pytest.approx(1.0)


def test_smoothing_preserves_series_endpoints() -> None:
    """min_periods=1 keeps the first and last hours of the study period."""
    out = smooth_speed(pd.Series([10.0, 12.0, 14.0, 16.0, 18.0]), 3)
    assert out.notna().all()
    assert out.iloc[0] == pytest.approx(11.0)


def test_smoothing_damps_the_observed_oscillation() -> None:
    """3.36 to 21.63 kn while cruising steadily at ~15 kn, from §1.6."""
    oscillating = pd.Series([3.36, 21.63, 4.10, 19.80, 5.20, 21.00, 15.0])
    assert smooth_speed(oscillating, 3).std() < oscillating.std()


def test_smoothing_preserves_the_mean_in_the_interior() -> None:
    sog = pd.Series(np.linspace(10, 20, 101))
    assert smooth_speed(sog, 5).mean() == pytest.approx(sog.mean(), rel=0.01)


def test_smoothing_does_not_cross_a_port_visit_boundary() -> None:
    """Berth hours must not smear into departure/cruise speed or mode assignment."""
    spine = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=8, freq="h"),
        "sog_raw": [0.0, 0.0, 0.0, 0.0, 20.0, 20.0, 20.0, 20.0],
        "is_inactive": [False] * 8,
    })
    ports = pd.DataFrame({
        "start_ts": [pd.Timestamp("2024-01-01 00:00", tz="UTC")],
        "end_ts": [pd.Timestamp("2024-01-01 03:00", tz="UTC")],
    })
    out = add_smoothed_speeds(spine, [7], ports)

    assert out.loc[:3, "sog_w7"].eq(0.0).all()
    assert out.loc[4:, "sog_w7"].eq(20.0).all()
    assert out.loc[:3, "in_port_visit"].all()


# ---------------------------------------------------------------------------
# 1.6 -- the v^3 bias that makes smoothing mandatory
# ---------------------------------------------------------------------------


def test_cubic_bias_is_one_for_a_constant_speed() -> None:
    assert cubic_bias(pd.Series([15.0] * 10)) == pytest.approx(1.0)


def test_cubic_bias_exceeds_one_for_an_oscillating_series() -> None:
    """Power scales as v^3, so the error does not average out."""
    assert cubic_bias(pd.Series([3.36, 21.63, 4.10, 19.80, 5.20, 21.00])) > 1.3


def test_smoothing_reduces_the_cubic_bias() -> None:
    """The measured effect: 1.67x unsmoothed falling to 1.19x at a 3-hour window."""
    oscillating = pd.Series([3.36, 21.63, 4.10, 19.80, 5.20, 21.00, 4.50, 20.10] * 6)
    assert cubic_bias(smooth_speed(oscillating, 3)) < cubic_bias(oscillating)


def test_documented_bias_arithmetic() -> None:
    """mean(v^3) = 2,654 against (mean v)^3 = 1,588 is a 1.67x overestimate."""
    assert 2654 / 1588 == pytest.approx(1.67, abs=0.01)


# ---------------------------------------------------------------------------
# 1.7 -- the hourly spine
# ---------------------------------------------------------------------------


def test_spine_covers_every_hour_of_the_period() -> None:
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    assert len(out) == 24
    assert out["ts"].is_monotonic_increasing
    assert out["ts"].duplicated().sum() == 0


def test_spine_flags_filled_hours() -> None:
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    assert out["is_interpolated"].sum() == 22
    assert not out["is_interpolated"].iloc[0]
    assert not out["is_interpolated"].iloc[1]


def test_spine_leaves_no_missing_positions() -> None:
    """Downstream spatial joins need a position for every hour."""
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    assert out["lat"].notna().all()
    assert out["lon"].notna().all()


def test_filled_hours_contribute_no_observed_hours() -> None:
    """Otherwise coverage would compute as 100% by construction."""
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    assert out.loc[out["is_interpolated"], "hours"].eq(0.0).all()
    assert out["hours"].sum() == 2.0


def test_nearest_neighbour_fill_does_not_manufacture_zero_speeds() -> None:
    """The ordering trap this function exists to avoid.

    Nearest-neighbour holds position constant across a gap. Deriving speed from
    those filled positions would read a moving ship as stationary, then emit one
    impossible jump when the signal returns.
    """
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0), (0.50, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    filled = out.loc[out["is_interpolated"], "sog_raw"]
    assert filled.notna().all()
    assert (filled > 0).all()


def test_spine_is_a_uniform_one_hour_grid() -> None:
    """Uniform spacing is what lets the centred average be a simple rolling mean."""
    frame = derive_speed(_track([(0.0, 0.0), (0.25, 0.0)]))
    out = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    assert out["ts"].diff().dropna().eq(pd.Timedelta(hours=1)).all()


# ---------------------------------------------------------------------------
# 1.7 -- coverage
# ---------------------------------------------------------------------------


def test_coverage_of_a_fully_observed_day() -> None:
    frame = derive_speed(_track([(0.0, i / 100) for i in range(24)]))
    spine = build_spine(frame, datetime(2024, 1, 15), datetime(2024, 1, 16), VESSEL_A)
    out = coverage_by_year(spine)
    assert out.loc[0, "observed_hours"] == 24


def test_coverage_matches_the_measured_2024_figure() -> None:
    """8,782 observed of 8,784 elapsed = 99.98%."""
    assert 8782 / 8784 == pytest.approx(0.9998, abs=0.0001)


# ---------------------------------------------------------------------------
# 1.4 -- port visits
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def port_calls() -> pd.DataFrame:
    events = json.loads(
        (API_SAMPLES / "round4" / "A_port_visits_2017_2024.json").read_text(encoding="utf-8")
    )
    return _parse_port_visits(events, VESSEL_A)


def test_all_389_port_calls_parse(port_calls) -> None:
    assert len(port_calls) == 389


def test_every_port_call_has_a_country(port_calls) -> None:
    """Port country is the start anchorage flag, an ISO3 code."""
    assert port_calls["port_iso3"].notna().all()
    assert port_calls["port_iso3"].str.len().eq(3).all()


def test_every_port_call_is_confidence_4(port_calls) -> None:
    """Confidences 3 and 4 were requested; only 4 came back. Note it is a string."""
    assert set(port_calls["confidence"]) == {"4"}


def test_port_country_histogram_matches_the_capture(port_calls) -> None:
    counts = port_calls["port_iso3"].value_counts()
    assert counts["CHN"] == 194
    assert counts["USA"] == 39
    assert counts["TWN"] == 46


def test_eu_port_calls_unlock_thetis_validation(port_calls) -> None:
    """35 EU calls, so the vessel's verified annual CO2 is published (§8.2)."""
    eu = port_calls[port_calls["port_iso3"].isin(EU27)]
    assert len(eu) == 35
    assert set(eu["port_iso3"].value_counts().to_dict()) == {"NLD", "DEU", "BEL", "GRC", "POL"}


def test_port_calls_span_every_study_year(port_calls) -> None:
    """Criterion 4: continuous presence, so no truncation reads as a decline."""
    assert set(port_calls["start_ts"].dt.year) == set(range(2017, 2025))


def test_port_name_prefers_top_destination(port_calls) -> None:
    """``anchorage.name`` is null ~20% of the time; ``topDestination`` never is."""
    assert port_calls["port_name"].notna().all()


def test_durations_are_positive_and_finite(port_calls) -> None:
    assert (port_calls["duration_h"] > 0).all()
    assert port_calls["duration_h"].max() < 24 * 60


def test_median_stay_matches_the_documented_figure(port_calls) -> None:
    """§4.1 records a median stay of 30.1 hours."""
    assert port_calls["duration_h"].median() == pytest.approx(30.1, abs=1.5)


def test_time_in_port_is_a_large_share_of_the_period(port_calls) -> None:
    """24.9% of 70,128 hours, which is why auxiliary demand is not a correction term."""
    assert port_calls["duration_h"].sum() / 70_128 == pytest.approx(0.249, abs=0.02)


def test_port_calls_are_time_ordered(port_calls) -> None:
    assert port_calls["start_ts"].is_monotonic_increasing


def test_end_is_after_start(port_calls) -> None:
    assert (port_calls["end_ts"] > port_calls["start_ts"]).all()


def test_eez_mrgid_is_captured(port_calls) -> None:
    """MRGIDs join cleanly to EEZ v12 -- 8456 is the US EEZ."""
    assert port_calls["eez_mrgid"].notna().sum() > 300


def test_snake_case_port_visit_block_is_read() -> None:
    """``port_visit`` is the only snake_case key in a camelCase payload."""
    events = json.loads(
        (API_SAMPLES / "round4" / "A_port_visits_2017_2024.json").read_text(encoding="utf-8")
    )
    assert "port_visit" in events[0]
    assert "portVisit" not in events[0]


def test_eu_membership_helper() -> None:
    assert is_eu("NLD") and is_eu("DEU") and is_eu("POL")
    assert not is_eu("CHN")
    assert not is_eu("GBR")  # left the EU; not in EU27
    assert not is_eu(None)


# ---------------------------------------------------------------------------
# Gap classification -- inactivity vs missed reception
# ---------------------------------------------------------------------------


def _spine_with_gap(gap_start: int, gap_hours: int, total_hours: int = 24 * 40):
    """A spine of `total_hours` where one contiguous run is unobserved."""
    index = pd.date_range("2019-01-01", periods=total_hours, freq="h")
    frame = pd.DataFrame({
        "imo": VESSEL_A,
        "ts": index,
        "lat": 33.7,
        "lon": -118.2,
        "hours": 1.0,
        "sog_raw": 12.0,
        "is_interpolated": False,
    })
    frame.loc[gap_start:gap_start + gap_hours - 1, "is_interpolated"] = True
    frame.loc[gap_start:gap_start + gap_hours - 1, "hours"] = 0.0
    return frame


def test_find_gaps_locates_a_contiguous_run() -> None:
    from emissions_allocation.activity import find_gaps

    gaps = find_gaps(_spine_with_gap(100, 240))
    assert len(gaps) == 1
    assert gaps.loc[0, "hours"] == 240


def test_find_gaps_separates_scattered_runs() -> None:
    from emissions_allocation.activity import find_gaps

    frame = _spine_with_gap(100, 3)
    frame.loc[500:502, "is_interpolated"] = True
    assert len(find_gaps(frame)) == 2


def test_short_scattered_gaps_are_not_inactivity() -> None:
    """2017's pattern: 615 runs, none over 7 days. Correctable by §4.5."""
    from emissions_allocation.activity import classify_gaps

    spine, windows = classify_gaps(_spine_with_gap(100, 12), pd.DataFrame(), 7)
    assert windows.empty
    assert not spine["is_inactive"].any()


def test_long_gap_without_port_calls_is_inactivity() -> None:
    """The 2019 pattern: a contiguous absence the vessel was genuinely out for."""
    from emissions_allocation.activity import classify_gaps

    spine, windows = classify_gaps(_spine_with_gap(100, 24 * 10), pd.DataFrame(), 7)
    assert len(windows) == 1
    assert spine["is_inactive"].sum() == 24 * 10


def test_long_gap_containing_port_calls_raises() -> None:
    """The cross-endpoint check: presence says absent, events says it called at a port.

    That disagreement is a broken pull, not a property of the vessel -- most often a
    hull trading under a name missing from config.
    """
    from emissions_allocation.activity import PresenceGapError, classify_gaps

    spine = _spine_with_gap(100, 24 * 10)
    gap_ts = spine.loc[spine["is_interpolated"], "ts"]
    port_calls = pd.DataFrame({
        "start_ts": [gap_ts.iloc[50].tz_localize("UTC")],
        "port_id": ["usa-longbeach"],
    })
    with pytest.raises(PresenceGapError, match="contain port calls"):
        classify_gaps(spine, port_calls, 7)


def test_gap_error_names_the_likely_cause() -> None:
    from emissions_allocation.activity import PresenceGapError, classify_gaps

    spine = _spine_with_gap(100, 24 * 10)
    gap_ts = spine.loc[spine["is_interpolated"], "ts"]
    port_calls = pd.DataFrame({
        "start_ts": [gap_ts.iloc[50].tz_localize("UTC")],
        "port_id": ["usa-longbeach"],
    })
    with pytest.raises(PresenceGapError, match="rename history"):
        classify_gaps(spine, port_calls, 7)


def test_coverage_separates_raw_from_active() -> None:
    """§4.5's divisor must exclude out-of-service hours, not scale them up."""
    from emissions_allocation.activity import classify_gaps, coverage_by_year

    spine, _ = classify_gaps(_spine_with_gap(100, 24 * 10), pd.DataFrame(), 7)
    out = coverage_by_year(spine).iloc[0]
    assert out["inactive_hours"] == 240
    assert out["coverage_active"] > out["coverage_raw"]
    assert out["coverage_active"] == pytest.approx(1.0)


def test_2019_active_coverage_matches_reception_quality() -> None:
    """3,159 observed of 8,760 is 36.1% raw, but 82.0% of the 3,854 in-service hours
    -- the same reception quality as 2017, once the 282-day absence is set aside."""
    assert 3159 / 8760 == pytest.approx(0.361, abs=0.001)
    assert 3159 / (8760 - 4906) == pytest.approx(0.820, abs=0.002)


def test_correcting_2019_raw_would_fabricate_voyages() -> None:
    """Why the classification exists: 2.77x on a ship that was laid up."""
    assert 1 / (3159 / 8760) == pytest.approx(2.77, abs=0.01)


def test_smoothing_does_not_cross_an_inactivity_boundary() -> None:
    """A centred window straddling a lay-up would blend the last voyage before it
    into the first voyage after -- not a smoothing artefact but a fabricated value."""
    from emissions_allocation.activity import add_smoothed_speeds, classify_gaps

    spine = _spine_with_gap(100, 24 * 10)
    spine.loc[:99, "sog_raw"] = 0.0      # in port before the lay-up
    spine.loc[340:, "sog_raw"] = 20.0    # cruising after it
    spine, _ = classify_gaps(spine, pd.DataFrame(), 7)
    out = add_smoothed_speeds(spine, [7])

    assert out.loc[out["is_inactive"], "sog_w7"].isna().all()
    # The first in-service hour after the gap must not have inherited the zeros.
    assert out.loc[340, "sog_w7"] == pytest.approx(20.0)


def test_imo2020_port_phase_sensitivity_only_replaces_short_gaps() -> None:
    """The adapted Fourth IMO branch cannot manufacture a whole long voyage."""
    spine = _spine_with_gap(5, 2, total_hours=30)
    spine.loc[16:27, "is_interpolated"] = True
    spine.loc[16:27, "hours"] = 0.0
    spine.loc[spine["is_interpolated"], "sog_raw"] = 99.0
    spine.loc[~spine["is_interpolated"], "sog_raw"] = 15.0
    ports = pd.DataFrame({
        "start_ts": pd.to_datetime(["2019-01-01 00:00", "2019-01-01 12:00"], utc=True),
        "end_ts": pd.to_datetime(["2019-01-01 02:00", "2019-01-01 14:00"], utc=True),
    })

    out, audit = add_imo2020_port_phase_sensitivity(
        spine, ports, [1], transition_hours=0, min_gap_hours=6, max_gap_hours=72
    )

    assert audit.loc[0, "missing_gap_threshold_hours"] == pytest.approx(10.0)
    assert audit.loc[0, "short_gap_hours"] == 2
    assert audit.loc[0, "long_gap_hours"] == 12
    assert out.loc[5:6, "sog_imo2020_raw"].eq(15.0).all()
    assert out.loc[16:27, "sog_imo2020_raw"].eq(99.0).all()
    assert out.loc[spine["is_interpolated"], "sog_raw"].eq(99.0).all()


# ---------------------------------------------------------------------------
# §8.2 -- leg-speed plausibility and anchorage-segmentation artefacts
# ---------------------------------------------------------------------------


def _legs_and_ports(kn_target: float, origin_iso: str, dest_iso: str):
    """One leg of interest between ports 12 km apart, plus a realistic background
    of ordinary ocean voyages.

    The background matters: the artefact diagnosis requires the impossible legs to
    be a negligible share of total leg time, which is what distinguishes "GFW split
    one port stay" from "this dataset is broken". A fixture containing only the
    suspect leg would rightly fail that test.
    """
    hours = (12.0 / 1.852) / kn_target
    rows = [{
        "imo": VESSEL_A,
        "depart_ts": pd.Timestamp("2021-03-17 14:50", tz="UTC"),
        "arrive_ts": pd.Timestamp("2021-03-17 14:50", tz="UTC") + pd.Timedelta(hours=hours),
        "origin_port_id": "a", "dest_port_id": "b",
        "origin_iso3": origin_iso, "dest_iso3": dest_iso,
        "leg_hours": hours,
    }]
    # 40 ordinary transpacific legs at ~14 kn, as vessel A actually sails.
    for i in range(40):
        rows.append({
            "imo": VESSEL_A,
            "depart_ts": pd.Timestamp("2021-04-01", tz="UTC") + pd.Timedelta(days=20 * i),
            "arrive_ts": pd.Timestamp("2021-04-01", tz="UTC")
                         + pd.Timedelta(days=20 * i, hours=400),
            "origin_port_id": "c", "dest_port_id": "d",
            "origin_iso3": "CHN", "dest_iso3": "USA",
            "leg_hours": 400.0,
        })
    ports = pd.DataFrame([
        {"port_id": "a", "lat": 22.50, "lon": 113.90, "duration_h": 20.0},
        {"port_id": "b", "lat": 22.50, "lon": 114.02, "duration_h": 20.0},
        {"port_id": "c", "lat": 31.23, "lon": 121.47, "duration_h": 30.0},
        {"port_id": "d", "lat": 33.74, "lon": -118.27, "duration_h": 30.0},
    ])
    return pd.DataFrame(rows), ports


def test_impossible_domestic_leg_is_diagnosed_not_flagged() -> None:
    """A 39 kn leg between two adjacent Chinese anchorages is GFW splitting one
    port stay in two, not a voyage. Reporting it as a generic WARN would bury the
    diagnosis and make the check noise."""
    from emissions_allocation.config import load_config
    from emissions_allocation.validate import PASS, check_leg_speeds

    legs, ports = _legs_and_ports(39.5, "CHN", "CHN")
    check = check_leg_speeds(legs, ports)
    assert check.status == PASS
    assert "artefact" in check.detail


def test_impossible_cross_border_leg_still_warns() -> None:
    """A border-crossing leg no ship could sail is NOT an anchorage artefact --
    it means a port call is missing or mis-ordered, and must stay visible."""
    from emissions_allocation.validate import WARN, check_leg_speeds

    legs, ports = _legs_and_ports(39.5, "CHN", "USA")
    check = check_leg_speeds(legs, ports)
    assert check.status == WARN
    assert "cross a border" in check.detail


def test_plausible_legs_pass_cleanly() -> None:
    from emissions_allocation.validate import PASS, check_leg_speeds

    legs, ports = _legs_and_ports(14.0, "CHN", "USA")
    check = check_leg_speeds(legs, ports)
    assert check.status == PASS
    assert "artefact" not in check.detail

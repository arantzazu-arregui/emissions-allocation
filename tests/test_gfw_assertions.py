"""The three mandatory presence assertions, and the envelope parsing beneath them.

Ground rule 2. A wrong ship name returns HTTP 200 with zero rows and no error; these
tests are what stand between that and a silently empty emissions series.

Fixtures are the real captured responses in ``data/sample/api/``, not hand-written
mocks, so the tests fail if GFW's envelope shape ever moves.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from emissions_allocation.gfw import (
    GFWClient,
    PresenceAssertionError,
    assert_presence,
    extract_report_records,
    is_valid_imo,
    year_bounds,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SAMPLES = PROJECT_ROOT / "data" / "sample" / "api"

EXPECTED_IMO = "9516454"          # COSCO ITALY
Y2024_RECORDS = 8782
Y2024_ELAPSED_HOURS = 8784        # 2024 is a leap year


def _load(relative: str) -> dict:
    return json.loads((API_SAMPLES / relative).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Envelope parsing
# ---------------------------------------------------------------------------


def test_extract_records_from_a_real_report() -> None:
    body = _load("05_speed_c_separate_params.json")
    assert len(extract_report_records(body)) == 526


def test_zero_row_response_yields_no_records() -> None:
    """The silent failure: HTTP 200, dataset value literally ``null``.

    ``entries[0]`` is still a dict with the dataset key present, so a naive
    ``for r in entries[0][key]`` raises TypeError rather than reporting emptiness.
    """
    body = _load("round3/F2_lowercase.json")
    assert body["entries"][0]["public-global-presence:v4.0"] is None
    assert extract_report_records(body) == []


def test_total_field_is_not_a_record_count() -> None:
    """``total`` counts datasets. It is 1 even when zero rows came back."""
    empty = _load("round3/F2_lowercase.json")
    assert empty["total"] == 1
    assert extract_report_records(empty) == []

    populated = _load("05_speed_c_separate_params.json")
    assert populated["total"] == 1
    assert len(extract_report_records(populated)) == 526


def test_4wings_never_paginates() -> None:
    meta = _load("round4/C_year_meta.json")
    assert meta["n_records"] == Y2024_RECORDS
    assert meta["total_field"] == 1
    assert meta["limit"] is None and meta["offset"] is None and meta["nextOffset"] is None


def test_truncated_entries_placeholder_does_not_crash() -> None:
    """``round2/A1`` has ``entries`` as a string -- a dump-script artefact."""
    body = _load("round2/A1_world_narrow_filter.json")
    assert isinstance(body["entries"], str)
    assert extract_report_records(body) == []


# ---------------------------------------------------------------------------
# Assertion (a) -- non-empty
# ---------------------------------------------------------------------------


def test_empty_result_raises() -> None:
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="ZERO records"):
        assert_presence([], EXPECTED_IMO, start, end)


def test_empty_result_message_names_the_case_sensitivity_trap() -> None:
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError) as excinfo:
        assert_presence([], EXPECTED_IMO, start, end)
    message = str(excinfo.value)
    assert "CASE-SENSITIVE" in message
    assert "shipnames" in message


def test_lowercase_shipname_response_is_caught() -> None:
    """End to end: the body a misspelled name actually returns must raise."""
    records = extract_report_records(_load("round3/F2_lowercase.json"))
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError):
        assert_presence(records, EXPECTED_IMO, start, end)


# ---------------------------------------------------------------------------
# Assertion (b) -- exactly one distinct IMO, and it is the expected one
# ---------------------------------------------------------------------------


def _hour_records(imo: str, count: int) -> list[dict]:
    return [{"imo": imo, "hours": 1} for _ in range(count)]


def test_imo_validator_requires_seven_digits_and_checksum() -> None:
    assert is_valid_imo(EXPECTED_IMO)
    assert not is_valid_imo("95164540")
    assert not is_valid_imo("9516455")
    assert not is_valid_imo("95164A4")


def test_two_distinct_imos_raise() -> None:
    records = _hour_records(EXPECTED_IMO, 5000) + _hour_records("9277802", 4000)
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="distinct IMO"):
        assert_presence(records, EXPECTED_IMO, start, end)


def test_wrong_single_imo_raises() -> None:
    """One hull, but not the one asked for."""
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="distinct IMO"):
        assert_presence(_hour_records("9277802", 8782), EXPECTED_IMO, start, end)


def test_malformed_imo_raises_without_normalization() -> None:
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="malformed IMO"):
        assert_presence(_hour_records("95164540", 8782), EXPECTED_IMO, start, end)


def test_blank_imo_is_not_counted_as_a_distinct_hull() -> None:
    """Absent IMO is ``""`` in presence records, ``null`` in identity endpoints."""
    records = _hour_records(EXPECTED_IMO, 8782) + [{"imo": "", "hours": 1}]
    start, end = year_bounds(2024)
    kept = assert_presence(records, EXPECTED_IMO, start, end)
    assert len(kept) == 8782


def test_records_are_filtered_to_the_expected_imo() -> None:
    """Section 1.1: IMO is a post-filter because it cannot be a request filter."""
    records = _hour_records(EXPECTED_IMO, 8782) + [{"imo": "", "hours": 1}]
    start, end = year_bounds(2024)
    assert all(r["imo"] == EXPECTED_IMO for r in assert_presence(records, EXPECTED_IMO, start, end))


# ---------------------------------------------------------------------------
# Assertion (c) -- observed hours within tolerance of elapsed hours
# ---------------------------------------------------------------------------


def test_near_empty_year_raises_on_the_hard_floor() -> None:
    """A year returning almost nothing is a broken pull, not thin reception."""
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="hard floor"):
        assert_presence(_hour_records(EXPECTED_IMO, 400), EXPECTED_IMO, start, end)


def test_failure_message_does_not_invite_lowering_the_floor() -> None:
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError) as excinfo:
        assert_presence(_hour_records(EXPECTED_IMO, 400), EXPECTED_IMO, start, end)
    assert "do not lower the floor" in str(excinfo.value)


def test_genuinely_thin_year_passes_but_warns(caplog) -> None:
    """36.1% is vessel A's real 2019 coverage. An hours ratio cannot tell a
    truncated response from a laid-up vessel, so this must not hard-fail."""
    start, end = year_bounds(2019)
    with caplog.at_level("WARNING"):
        kept = assert_presence(_hour_records(EXPECTED_IMO, 3159), EXPECTED_IMO, start, end)
    assert len(kept) == 3159
    assert "below the 95% warning threshold" in caplog.text


@pytest.mark.parametrize(
    "year,observed",
    [(2017, 7185), (2018, 6233), (2019, 3159), (2020, 6388),
     (2021, 8163), (2022, 8498), (2023, 8743), (2024, 8782)],
)
def test_every_measured_year_survives_the_floor(year: int, observed: int) -> None:
    """All eight measured years are legitimate and must pass."""
    start, end = year_bounds(year)
    assert len(assert_presence(_hour_records(EXPECTED_IMO, observed), EXPECTED_IMO, start, end)) == observed


def test_more_hours_than_elapsed_raises() -> None:
    """Duplicate records mean the vessel-hour grain is broken."""
    start, end = year_bounds(2024)
    with pytest.raises(PresenceAssertionError, match="More hours than"):
        assert_presence(_hour_records(EXPECTED_IMO, 12000), EXPECTED_IMO, start, end)


def test_known_good_year_passes_all_three() -> None:
    """The measured 2024 figure: 8,782 of 8,784 hours = 99.98%."""
    start, end = year_bounds(2024)
    kept = assert_presence(_hour_records(EXPECTED_IMO, Y2024_RECORDS), EXPECTED_IMO, start, end)
    assert len(kept) == Y2024_RECORDS
    assert (end - start).total_seconds() / 3600 == Y2024_ELAPSED_HOURS


def test_year_bounds_handles_leap_years() -> None:
    assert (year_bounds(2024)[1] - year_bounds(2024)[0]).days == 366
    assert (year_bounds(2023)[1] - year_bounds(2023)[0]).days == 365


def test_captured_year_sample_carries_exactly_one_imo() -> None:
    """Identity integrity on the real captured payload (Section 8)."""
    records = json.loads(
        (API_SAMPLES / "round4" / "C_year_sample_records.json").read_text(encoding="utf-8")
    )
    assert sorted({r["imo"] for r in records}) == [EXPECTED_IMO]


# ---------------------------------------------------------------------------
# Filter composition
# ---------------------------------------------------------------------------


def test_conditions_compose_into_one_and_joined_string() -> None:
    """A second ``filters[n]`` parameter is silently dropped: 526 rows vs 24."""
    combined = GFWClient.build_filter('vessel_type in ("cargo")', 'speed in ("10-15")')
    assert combined == 'vessel_type in ("cargo") AND speed in ("10-15")'


def test_dropped_second_filter_is_visible_in_the_captured_responses() -> None:
    """The evidence: separate params returned 22x the rows of the conjunction."""
    separate = extract_report_records(_load("05_speed_c_separate_params.json"))
    conjunction = extract_report_records(_load("05_speed_b_combined_and.json"))
    assert len(separate) == 526
    assert len(conjunction) == 24


def test_shipname_filter_accepts_a_rename_history() -> None:
    assert (
        GFWClient.shipname_filter(["COSCO ITALY", "NE116"])
        == 'shipname in ("COSCO ITALY","NE116")'
    )


def test_shipname_filter_quotes_a_single_name() -> None:
    assert GFWClient.shipname_filter(["COSCO ITALY"]) == 'shipname in ("COSCO ITALY")'

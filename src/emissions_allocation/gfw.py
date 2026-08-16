"""Authenticated Global Fishing Watch API v3 client.

Everything in this module is written against captured raw responses in
``data/sample/api/`` rather than against the published documentation, because the
documentation does not describe several behaviours that matter. The important ones,
each established by a probe in ``scripts/exploratory/``:

**A wrong ship name returns HTTP 200 with zero rows and no error.** The body is
``{"entries": [{"public-global-presence:v4.0": null}]}`` -- a literal ``null``, not
an empty list. This is the most dangerous behaviour in the whole API, and it is why
:func:`assert_presence` exists and why every presence pull goes through it.

**``total`` in a 4Wings report is not a record count.** It counts datasets, and is
``1`` even for a zero-row response. 4Wings never paginates -- ``limit``, ``offset``
and ``nextOffset`` are ``null`` even on an 8,782-record response. ``/v3/events`` is
the opposite: ``total`` is real and ``nextOffset`` advances.

**HTTP status and the body's ``statusCode`` disagree.** HTTP 422 arrives carrying
``"statusCode": 503`` inside. Retry logic reads both. These reproduce across runs
33 minutes apart, so they are server behaviour, not transient faults.

**A second ``filters[n]`` parameter is silently dropped.** Sending ``filters[0]``
and ``filters[1]`` returned 526 rows where the intended conjunction returns 24 --
proven by byte-identical response files. Conditions are always composed into one
``AND``-joined string.

**You cannot filter presence by IMO, MMSI or vesselId.** Those columns do not exist
in the query scope; the only identity field that binds is ``shipname``, matched
exactly and case-sensitively. IMO filtering happens in this loader, not in the
request.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

PRESENCE_DATASET = "public-global-presence:latest"
PORT_VISITS_DATASET = "public-global-port-visits-events:latest"
IDENTITY_DATASET = "public-global-vessel-identity:latest"

# The world polygon accepted by 4Wings. Latitude is clipped to +/-85, not +/-90:
# that is the extent the probe in round 2 confirmed, returning 14,489 records and
# 991 vessels for a single day.
WORLD_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]],
}

# One concurrent 4Wings report is permitted; a second returns 429.
_MIN_SECONDS_BETWEEN_REPORTS = 1.0


class GFWError(RuntimeError):
    """An API call failed in a way retrying will not fix."""


class PresenceAssertionError(AssertionError):
    """A presence pull did not survive the three mandatory assertions.

    Never catch this to continue. It means the data is not what was asked for, and
    the API will not have said so.
    """


# ---------------------------------------------------------------------------
# Response envelope handling
# ---------------------------------------------------------------------------


def extract_report_records(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the record list out of a 4Wings report envelope.

    The envelope is ``{"entries": [{"<resolved-dataset>": [...]}]}``. The key is the
    *resolved* dataset version (``public-global-presence:v4.0``), never the
    ``:latest`` alias that was sent, so it is read positionally rather than by name.

    Returns ``[]`` for the zero-row case, where the dataset value is ``null``. The
    caller must not treat that as success -- see :func:`assert_presence`.
    """
    entries = body.get("entries")
    if not isinstance(entries, list) or not entries:
        return []

    records: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            # round2/A1 has `entries` as a placeholder string: a local artefact of
            # the dump script's size limit, not something the API returns.
            continue
        for _dataset_key, cells in entry.items():
            if cells:  # None (zero rows) and [] both fall through
                records.extend(cells)
    return records


def _read_error(response: requests.Response) -> tuple[int, str]:
    """Return ``(effective_status, message)``, reading both status sources.

    HTTP 422 frequently carries ``"statusCode": 503`` in the body. The body's code
    is the one that describes what actually happened, so it wins when they differ.
    404 returns plain text, not JSON.
    """
    try:
        body = response.json()
    except ValueError:
        return response.status_code, response.text[:400]

    if not isinstance(body, dict):
        return response.status_code, str(body)[:400]

    status = body.get("statusCode", response.status_code)

    # Two error shapes: {statusCode, error, messages[{title, detail}]} and, for 403
    # only, {message, error, statusCode}.
    if "messages" in body and isinstance(body["messages"], list):
        details = [m.get("detail", "") for m in body["messages"] if isinstance(m, dict)]
        seen: list[str] = []
        for d in details:  # the insights 422 duplicates one message verbatim
            if d not in seen:
                seen.append(d)
        message = "; ".join(seen)
    else:
        message = body.get("message") or body.get("error") or ""

    return int(status), message[:600]


# ---------------------------------------------------------------------------
# The three mandatory presence assertions
# ---------------------------------------------------------------------------


def assert_presence(
    records: Sequence[dict[str, Any]],
    expected_imo: str,
    period_start: datetime,
    period_end: datetime,
    coverage_floor: float = 0.10,
    coverage_warn: float = 0.95,
    context: str = "",
) -> list[dict[str, Any]]:
    """Apply the three mandatory assertions and return the IMO-filtered records.

    Every presence pull goes through this. In order:

    (a) **Non-empty.** A wrong or misspelled ship name returns HTTP 200 with a
        ``null`` dataset value. Nothing upstream will flag it.
    (b) **Exactly one distinct IMO, and it is the expected one.** ``shipname`` is
        not unique across the fleet, so a name that matches two hulls silently
        doubles the emissions.
    (c) **Observed hours plausible against elapsed hours.** Catches a partial pull
        that returned successfully.

    Note what (c) can and cannot do. It exists to catch a truncated response, but an
    hours ratio cannot distinguish "the API returned half the year" from "the vessel
    was laid up half the year" -- both look like 50%. Real coverage for vessel A
    ranges from 36% to 99.98% across the study period, so a strict floor rejects
    legitimate years. The floor here is therefore set low enough to catch a broken
    pull only, a separate warning threshold flags thin years without stopping, and
    the decisive structural test lives in
    :func:`emissions_allocation.activity.assert_gaps_have_no_port_calls`, which
    cross-checks presence against an independent endpoint.

    Args:
        records: Raw records from :func:`extract_report_records`.
        expected_imo: The IMO this pull was supposed to be about.
        period_start: Start of the requested window.
        period_end: End of the requested window, exclusive.
        coverage_floor: Below this, the pull is treated as broken and raises.
        coverage_warn: Below this, the year is logged as low-confidence.
        context: Free text added to failure messages.

    Returns:
        Records filtered to ``expected_imo``.

    Raises:
        PresenceAssertionError: If any assertion fails.
    """
    where = f" ({context})" if context else ""
    expected_imo = str(expected_imo)

    # (a) non-empty
    if not records:
        raise PresenceAssertionError(
            f"presence pull for IMO {expected_imo} returned ZERO records{where}.\n"
            "  HTTP 200 with no rows is how this API reports a ship name that did "
            "not match.\n"
            "  shipname matching is EXACT and CASE-SENSITIVE: 'cosco italy' and "
            "'COSCO' both return nothing.\n"
            "  Check config/vessel_specs.yaml shipnames against the vessel's rename "
            "history."
        )

    # (b) exactly one distinct IMO, and it is the expected one.
    # Absent IMO is "" in presence records (it is null in the identity endpoints).
    distinct = sorted({str(r.get("imo") or "") for r in records} - {""})
    if distinct != [expected_imo]:
        raise PresenceAssertionError(
            f"presence pull for IMO {expected_imo} returned {len(distinct)} distinct "
            f"IMO(s): {distinct}{where}.\n"
            "  The shipname filter matched more than one hull, or the wrong one. "
            "Emissions would be summed across ships.\n"
            "  Narrow config/vessel_specs.yaml shipnames, or add a post-filter step."
        )

    matching = [r for r in records if str(r.get("imo") or "") == expected_imo]

    # (c) observed hours vs elapsed hours
    observed = sum(float(r.get("hours") or 0) for r in matching)
    elapsed = (period_end - period_start).total_seconds() / 3600.0
    coverage = observed / elapsed if elapsed else 0.0
    if coverage < coverage_floor:
        raise PresenceAssertionError(
            f"presence pull for IMO {expected_imo} observed {observed:,.0f} hours of "
            f"{elapsed:,.0f} elapsed = {coverage:.2%}{where}, below the "
            f"{coverage_floor:.0%} hard floor.\n"
            "  A year returning almost nothing is a broken pull, not thin reception. "
            "Investigate before using it -- do not lower the floor to make it pass."
        )
    if coverage < coverage_warn:
        log.warning(
            "IMO %s%s: coverage %.2f%% is below the %.0f%% warning threshold. "
            "Classify the gaps before applying any coverage correction -- a "
            "contiguous absence must not be scaled up as if it were missed reception.",
            expected_imo, where, coverage * 100, coverage_warn * 100,
        )
    if coverage > 1.01:
        raise PresenceAssertionError(
            f"presence pull for IMO {expected_imo} observed {observed:,.0f} hours of "
            f"only {elapsed:,.0f} elapsed = {coverage:.2%}{where}.\n"
            "  More hours than the period contains means duplicate records: the "
            "vessel-hour grain is broken."
        )

    log.info(
        "presence IMO %s%s: %d records, %.0f h of %.0f elapsed (%.2f%%)",
        expected_imo, where, len(matching), observed, elapsed, coverage * 100,
    )
    return matching


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class GFWClient:
    """Sequential, cached, retrying GFW client.

    Calls are sequential by construction: only one concurrent 4Wings report is
    permitted and a second returns 429.

    Responses are cached to ``cache_dir`` keyed by request. An 8-year presence pull
    is ~6 minutes of wall time per vessel, so caching is what makes the pipeline
    resumable and makes reruns free.
    """

    token: str
    cache_dir: Path | None = None
    timeout: int = 600
    max_retries: int = 4
    session: requests.Session | None = None
    _last_report_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.token:
            raise GFWError(
                "no GFW token. Put GFW_TOKEN in .env (already gitignored) or set it "
                "in the environment. Free non-commercial tokens: "
                "https://globalfishingwatch.org/our-apis/"
            )
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_env(cls, cache_dir: Path | None = None, **kwargs: Any) -> "GFWClient":
        """Build from ``GFW_TOKEN``, reading ``.env`` if the variable is unset."""
        token = os.environ.get("GFW_TOKEN", "")
        if not token:
            env_file = Path(__file__).resolve().parents[2] / ".env"
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("GFW_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        return cls(token=token, cache_dir=cache_dir, **kwargs)

    # -- caching ------------------------------------------------------------

    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
        return self.cache_dir / f"{safe}.json"

    def _cached(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        if path and path.exists():
            log.info("cache hit: %s", path.name)
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _store(self, key: str, body: dict[str, Any]) -> None:
        path = self._cache_path(key)
        if path:
            path.write_text(json.dumps(body, indent=1), encoding="utf-8")

    # -- transport ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        if cache_key:
            hit = self._cached(cache_key)
            if hit is not None:
                return hit

        url = f"{BASE_URL}{path}"
        last_error = ""

        for attempt in range(1, self.max_retries + 1):
            # One concurrent report only.
            since = time.monotonic() - self._last_report_at
            if since < _MIN_SECONDS_BETWEEN_REPORTS:
                time.sleep(_MIN_SECONDS_BETWEEN_REPORTS - since)

            try:
                response = self.session.request(  # type: ignore[union-attr]
                    method, url, params=params, json=json_body, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = f"transport: {exc}"
                log.warning("attempt %d/%d failed: %s", attempt, self.max_retries, exc)
                time.sleep(min(2 ** attempt, 60))
                continue
            finally:
                self._last_report_at = time.monotonic()

            if response.ok:
                body = response.json()
                if cache_key:
                    self._store(cache_key, body)
                return body

            status, message = _read_error(response)
            last_error = f"HTTP {response.status_code} / body statusCode {status}: {message}"

            # 429: one concurrent report exceeded. Back off hard.
            if response.status_code == 429 or status == 429:
                wait = min(10 * attempt, 120)
                log.warning("429 rate limited, waiting %ds (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue

            # 503-in-body is reproducible server behaviour for a malformed filter,
            # not a transient outage. Retrying it wastes quota.
            if status == 503 and "does not exist" in message:
                raise GFWError(
                    f"{method} {path}: {last_error}\n"
                    "  This is a malformed filter, not an outage. Presence cannot be "
                    "filtered by vessel_id, imo, ssvid or mmsi -- those columns do not "
                    "exist in query scope. Only shipname, flag, vessel_type and speed "
                    "bind. See CLAUDE.md."
                )

            if 500 <= status < 600:
                time.sleep(min(2 ** attempt, 60))
                continue

            raise GFWError(f"{method} {path}: {last_error}")

        raise GFWError(f"{method} {path}: exhausted {self.max_retries} attempts. {last_error}")

    # -- presence -----------------------------------------------------------

    @staticmethod
    def build_filter(*conditions: str) -> str:
        """Compose conditions into ONE ``AND``-joined ``filters[0]`` string.

        A second ``filters[n]`` parameter is silently dropped -- 526 rows returned
        where 24 were intended. There is never more than one filter parameter.
        """
        return " AND ".join(c for c in conditions if c)

    @staticmethod
    def shipname_filter(shipnames: Iterable[str]) -> str:
        """``shipname in ("A","B")`` -- exact and case-sensitive.

        ``in (...)`` accepts a list, so a whole rename history fits one request.
        """
        names = ",".join(f'"{n}"' for n in shipnames)
        return f"shipname in ({names})"

    def presence_year(
        self,
        shipnames: Sequence[str],
        year: int,
        *,
        extra_conditions: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """Hourly presence for one calendar year at world extent.

        One request per year: a full year returns in ~44 s in a single response with
        no pagination. Records carry identity inline (``imo``, ``mmsi``, ``flag``,
        ``shipName``), so no identity join is needed.

        Returns raw records. Pass them through :func:`assert_presence` before use --
        this method deliberately does not, so the caller supplies the expected IMO.
        """
        params = {
            "spatial-resolution": "HIGH",
            "temporal-resolution": "HOURLY",
            "group-by": "VESSEL_ID",
            "datasets[0]": PRESENCE_DATASET,
            "date-range": f"{year}-01-01,{year + 1}-01-01",
            "format": "JSON",
            "filters[0]": self.build_filter(
                self.shipname_filter(shipnames), *extra_conditions
            ),
        }
        key = f"presence_{'-'.join(shipnames)}_{year}"
        body = self._request(
            "POST", "/4wings/report",
            params=params, json_body={"geojson": WORLD_POLYGON}, cache_key=key,
        )
        return extract_report_records(body)

    # -- events -------------------------------------------------------------

    def port_visits(
        self,
        vessel_id: str,
        start_date: str,
        end_date: str,
        *,
        confidences: Sequence[int] = (3, 4),
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """All port visits for one vesselId, following ``nextOffset``.

        Unlike 4Wings, this endpoint paginates properly: ``total`` is a real count
        and ``nextOffset`` advances and ends ``null``.

        Note the caller must union across every vesselId belonging to the hull --
        one hull has several, and IMO 9516454 has two.
        """
        events: list[dict[str, Any]] = []
        offset = 0
        total: int | None = None

        while True:
            params: dict[str, Any] = {
                "vessels[0]": vessel_id,
                "datasets[0]": PORT_VISITS_DATASET,
                "start-date": start_date,
                "end-date": end_date,
                "limit": page_size,
                "offset": offset,
            }
            for i, c in enumerate(confidences):
                params[f"confidences[{i}]"] = c

            key = f"portvisits_{vessel_id}_{start_date}_{end_date}_o{offset}"
            body = self._request("GET", "/events", params=params, cache_key=key)

            page = body.get("entries") or []
            events.extend(page)
            total = body.get("total", total)

            next_offset = body.get("nextOffset")
            if next_offset is None or not page:
                break
            offset = int(next_offset)

        if total is not None and len(events) != total:
            raise GFWError(
                f"port visits for {vessel_id}: collected {len(events)} events but the "
                f"API reported total={total}. Pagination did not complete."
            )
        return events

    # -- identity -----------------------------------------------------------

    def vessel_search(self, imo: str) -> dict[str, Any]:
        """Search identity by IMO.

        ``offset`` must be OMITTED -- this endpoint rejects it, while
        ``/vessel-groups`` requires it. Use this rather than ``/vessels/{id}``:
        the detail endpoint returns only one ``selfReportedInfo`` element and so
        loses former identities.
        """
        return self._request(
            "GET", "/vessels/search",
            params={"query": str(imo), "datasets[0]": IDENTITY_DATASET, "limit": 20},
            cache_key=f"vessel_search_{imo}",
        )

    def vessel_ids_for_imo(self, imo: str) -> list[str]:
        """Every ``vesselId`` GFW associates with this hull.

        One hull has several. IMO 9516454 returns two: the current identity and a
        former name carrying a null IMO. Port visits must be pulled for all of them
        and unioned.

        These are API parameters only. Never a join key -- IMO is the key.
        """
        body = self.vessel_search(imo)
        found: list[str] = []
        for entry in body.get("entries") or []:
            # Two spellings for the same value: `id` in selfReportedInfo,
            # `vesselId` in combinedSourcesInfo.
            for item in entry.get("selfReportedInfo") or []:
                vid = item.get("id")
                if vid and vid not in found:
                    found.append(vid)
            for item in entry.get("combinedSourcesInfo") or []:
                vid = item.get("vesselId")
                if vid and vid not in found:
                    found.append(vid)
        if not found:
            raise GFWError(f"no vesselId found for IMO {imo}")
        return found

    def registry_identity(self, imo: str) -> dict[str, Any]:
        """Flat registry summary for one IMO, for cross-checking config.

        Tonnage is ``tonnageGt`` and length is ``lengthM``; there is no ``gt``,
        ``dwt``, ``beam`` or ``yearBuilt`` anywhere in the identity response, which
        is why hull dimensions come from config rather than from the API.
        """
        body = self.vessel_search(imo)
        entries = body.get("entries") or []
        if not entries:
            raise GFWError(f"identity search for IMO {imo} returned no entries")

        registry = (entries[0].get("registryInfo") or [{}])[0]
        owners = entries[0].get("registryOwners") or []
        names = sorted(
            {
                item.get("shipname")
                for block in ("registryInfo", "selfReportedInfo")
                for item in (entries[0].get(block) or [])
                if item.get("shipname")
            }
        )
        return {
            "imo": registry.get("imo"),
            "ssvid": registry.get("ssvid"),
            "flag": registry.get("flag"),
            "shipname": registry.get("shipname"),
            "callsign": registry.get("callsign"),
            "tonnage_gt": registry.get("tonnageGt"),
            "length_m": registry.get("lengthM"),
            "transmission_from": registry.get("transmissionDateFrom"),
            "transmission_to": registry.get("transmissionDateTo"),
            "name_history": names,
            # registryOwners.flag echoes the SHIP's flag, not owner domicile --
            # it returns HKG for a Shanghai-registered owner. NOT usable for the
            # owner allocation. Surfaced here only so the contradiction is visible.
            "registry_owners": [
                {"name": o.get("name"), "flag_UNRELIABLE": o.get("flag")} for o in owners
            ],
        }

    # -- coverage -----------------------------------------------------------

    def insights_coverage(
        self, vessel_id: str, start_date: str, end_date: str
    ) -> dict[str, Any]:
        """AIS coverage from the Insights endpoint. Returns HTTP 201, not 200.

        ``startDate`` must be on or after 2020-01-01, so this cannot cover the
        2017-2019 part of the study period at all. Prefer computing coverage as
        observed hours / elapsed hours, which works for the whole period; the two
        agreed to within 0.08% for 2024 on different units.

        ``blocks`` and ``blocksWithPositions`` come back as strings.
        """
        if start_date < "2020-01-01":
            raise GFWError(
                f"insights coverage requires startDate >= 2020-01-01, got {start_date}. "
                "Compute coverage as observed/elapsed hours instead."
            )
        body = self._request(
            "POST", "/insights/vessels",
            json_body={
                "includes": ["COVERAGE"],
                "startDate": start_date,
                "endDate": end_date,
                "vessels": [{"vesselId": vessel_id, "datasetId": IDENTITY_DATASET}],
            },
            cache_key=f"coverage_{vessel_id}_{start_date}_{end_date}",
        )
        coverage = body.get("coverage") or {}
        return {
            "blocks": int(coverage["blocks"]) if coverage.get("blocks") else None,
            "blocks_with_positions": (
                int(coverage["blocksWithPositions"])
                if coverage.get("blocksWithPositions") else None
            ),
            "percentage": coverage.get("percentage"),
        }


def year_bounds(year: int) -> tuple[datetime, datetime]:
    """``(start, end_exclusive)`` for a calendar year, for the hour assertion."""
    return datetime(year, 1, 1), datetime(year, 1, 1) + timedelta(days=366 if _leap(year) else 365)


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

"""§0 -- candidate discovery and criteria filtering.

Chooses study vessels by reproducible criteria rather than by hand, so a researcher
extending this work can apply the same filter to any number of ships.

Candidate discovery cannot use the ship-name filter, because names are what we are
searching *for*. It uses ``flag`` and ``vessel_type`` instead -- the other two
presence filters that bind.

**This stage cannot be closed automatically, and that is a property of the data, not
of the code.** Steps 1-4 are API work and run here. Criterion 7 -- registered-owner
country != flag country, which is the entire point of vessel B -- requires Equasis,
which needs a logged-in account and publishes no API. :func:`shortlist` therefore
ranks candidates and hands over; the last step is a human reading company records.

No IMO number is ever invented or recalled. Every candidate below comes from a live
presence query.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from emissions_allocation.activity import EU27
from emissions_allocation.config import Config
from emissions_allocation.gfw import GFWClient

log = logging.getLogger(__name__)

# §0.1 criterion 7. Open registries where flag routinely differs from owner domicile
# -- the divergence vessel B exists to demonstrate.
OPEN_REGISTRY_FLAGS = ("PAN", "LBR", "MHL", "MLT", "BHS", "CYP")

# §0.1 criterion 2. The international merchant fleet.
MERCHANT_TYPES = ("cargo",)

MIN_PORT_COUNTRIES = 3      # criterion 5
MIN_EU_PORT_CALLS = 1       # criterion 6


@dataclass
class Candidate:
    """One vessel surviving API-side filtering, pending the Equasis check."""

    imo: str
    shipname: str
    flag: str
    years_present: int = 0
    name_shared_with: int = 0
    port_countries: int = 0
    eu_port_calls: int = 0
    port_calls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def name_is_distinct(self) -> bool:
        """Criterion 3.

        The presence filter matches on name, exactly and case-sensitively. A name
        shared by several hulls silently sums their emissions -- the single most
        expensive mistake available in this pipeline.
        """
        return self.name_shared_with == 0


def discover_candidates(
    client: GFWClient,
    cfg: Config,
    *,
    flags: Sequence[str] = OPEN_REGISTRY_FLAGS,
    vessel_types: Sequence[str] = MERCHANT_TYPES,
    sample_days: Sequence[str] | None = None,
) -> list[Candidate]:
    """§0.2 steps 1-3: build a candidate pool and apply criteria 1-4.

    One presence request per sample day, at world extent, filtered by flag and type.
    A single flag-and-type combination returned 991 distinct vessels for one day in
    testing, so this yields a large pool cheaply.

    Sampling one day per study year is a cheap proxy for criterion 4 (continuous
    presence): a hull that appears on a day in every year has not been scrapped,
    renamed out of the filter, or laid up for a whole year. It is a proxy, not a
    proof -- the selected vessel still needs a full presence pull.

    Args:
        client: Authenticated GFW client.
        cfg: Loaded configuration, for the study years.
        flags: Registries to search.
        vessel_types: GFW vessel types.
        sample_days: ``MM-DD`` per year. Defaults to one mid-year day.
    """
    days = sample_days or [f"{year}-06-15" for year in cfg.years]
    flag_filter = ",".join(f'"{f}"' for f in flags)
    type_filter = ",".join(f'"{t}"' for t in vessel_types)
    condition = f"flag in ({flag_filter}) AND vessel_type in ({type_filter})"

    seen: dict[str, dict[str, Any]] = {}
    name_counter: Counter[str] = Counter()

    for day in days:
        year = int(day[:4])
        log.info("presence sample %s", day)
        records = client.presence_day(condition, day)

        found_today: set[str] = set()
        for record in records:
            imo = str(record.get("imo") or "").strip()
            name = str(record.get("shipName") or "").strip()
            # Criterion 1: Selin et al. restrict the universe to IMO-registered ships.
            if not imo or not name:
                continue
            found_today.add(imo)
            entry = seen.setdefault(
                imo, {"name": name, "flag": record.get("flag"), "years": set()}
            )
            entry["years"].add(year)

        for imo in found_today:
            name_counter[seen[imo]["name"]] += 0  # ensure key exists
        log.info("  %d records, %d distinct IMOs", len(records), len(found_today))

    # Criterion 3 is about NAME COLLISIONS across hulls, so count distinct IMOs per name.
    per_name: dict[str, set[str]] = {}
    for imo, entry in seen.items():
        per_name.setdefault(entry["name"], set()).add(imo)

    candidates = [
        Candidate(
            imo=imo,
            shipname=entry["name"],
            flag=entry["flag"],
            years_present=len(entry["years"]),
            name_shared_with=len(per_name[entry["name"]]) - 1,
        )
        for imo, entry in seen.items()
    ]
    log.info("pool: %d distinct IMOs across %d sample days", len(candidates), len(days))
    return candidates


def apply_presence_criteria(
    candidates: Sequence[Candidate], n_years: int
) -> list[Candidate]:
    """Criteria 3 and 4: distinctive name, present in every sampled year."""
    return [
        c for c in candidates
        if c.name_is_distinct and c.years_present == n_years
    ]


def enrich_with_port_calls(
    client: GFWClient, cfg: Config, candidates: Sequence[Candidate], limit: int = 25
) -> list[Candidate]:
    """Criteria 5 and 6, via the events endpoint -- one call per candidate.

    Deliberately capped: this is the expensive step, and the shortlist only needs to
    be long enough that a few survive the Equasis check.
    """
    start, end = cfg.start_date.isoformat(), cfg.end_date.isoformat()
    enriched = []
    for candidate in list(candidates)[:limit]:
        try:
            vessel_ids = client.vessel_ids_for_imo(candidate.imo)
            events: list[dict] = []
            for vessel_id in vessel_ids:
                events.extend(client.port_visits(vessel_id, start, end))
        except Exception as exc:  # noqa: BLE001 - a bad candidate must not stop the scan
            candidate.notes.append(f"port visits failed: {type(exc).__name__}")
            enriched.append(candidate)
            continue

        countries = {
            (e.get("port_visit") or {}).get("startAnchorage", {}).get("flag")
            for e in events
        } - {None}
        candidate.port_calls = len(events)
        candidate.port_countries = len(countries)
        candidate.eu_port_calls = sum(
            1 for e in events
            if (e.get("port_visit") or {}).get("startAnchorage", {}).get("flag") in EU27
        )
        enriched.append(candidate)
        log.info(
            "  %s %s: %d calls, %d countries, %d EU",
            candidate.imo, candidate.shipname, candidate.port_calls,
            candidate.port_countries, candidate.eu_port_calls,
        )
    return enriched


def shortlist(candidates: Sequence[Candidate], out_path: Path | None = None) -> pd.DataFrame:
    """§0.2 steps 4 and 6: apply criteria 5-6, rank, and write the hand-off table.

    Ranking is by EU port calls then total calls so shortlisted vessels meet the
    configured international-port-call criteria.

    The CSV is the hand-off point. **Criterion 7 must be closed in Equasis by hand**
    -- registered-owner country must differ from flag country. Nothing here can
    establish that.
    """
    frame = pd.DataFrame([{
        "imo": c.imo,
        "shipname": c.shipname,
        "flag": c.flag,
        "years_present": c.years_present,
        "name_is_distinct": c.name_is_distinct,
        "port_calls": c.port_calls,
        "port_countries": c.port_countries,
        "eu_port_calls": c.eu_port_calls,
        "meets_criterion_5": c.port_countries >= MIN_PORT_COUNTRIES,
        "meets_criterion_6": c.eu_port_calls >= MIN_EU_PORT_CALLS,
        "notes": "; ".join(c.notes),
    } for c in candidates])

    if frame.empty:
        return frame

    frame["passes_api_criteria"] = frame.meets_criterion_5 & frame.meets_criterion_6
    frame = frame.sort_values(
        ["passes_api_criteria", "eu_port_calls", "port_calls"], ascending=False
    ).reset_index(drop=True)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(out_path, index=False)
        log.info("wrote %s", out_path)
    return frame

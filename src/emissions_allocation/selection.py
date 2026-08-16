"""§0 -- candidate discovery and criteria filtering.

Chooses study vessels by reproducible criteria rather than by hand, so a researcher
extending this work can apply the same filter to any number of ships.

Candidate discovery cannot use the ship-name filter, because names are what we are
searching *for*. It uses ``flag`` and ``vessel_type`` instead -- the other two
presence filters that bind.

**This stage cannot be closed automatically.** Steps 1-4 below are API work and run
here. Criterion 7 (registered-owner country != flag country) requires Equasis, which
needs a logged-in account and publishes no API. :func:`shortlist` therefore writes a
ranked CSV for manual completion rather than selecting a vessel.

Status: vessel A is fixed; vessel B has not been selected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from emissions_allocation.gfw import GFWClient

log = logging.getLogger(__name__)

# §0.2 step 1. A single flag-and-type combination returned 991 distinct vessels for
# one day in testing, so this yields a large candidate pool cheaply.
OPEN_REGISTRY_FLAGS = ("PAN", "LBR", "MHL", "MLT", "BHS", "CYP")
MERCHANT_TYPES = ("cargo",)

# §0.1 criteria 5 and 6.
MIN_PORT_COUNTRIES = 3
MIN_EU_PORT_CALLS = 1

EU27 = frozenset({
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA", "DEU",
    "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT",
    "ROU", "SVK", "SVN", "ESP", "SWE",
})


@dataclass(frozen=True)
class Candidate:
    """One vessel surviving API-side filtering, pending the Equasis check."""

    imo: str
    shipname: str
    flag: str
    name_is_distinct: bool
    port_countries: int = 0
    eu_port_calls: int = 0
    years_present: int = 0
    observed_hours: float = 0.0


def discover_candidates(
    client: GFWClient,
    sample_date: str,
    *,
    flags: Sequence[str] = OPEN_REGISTRY_FLAGS,
    vessel_types: Sequence[str] = MERCHANT_TYPES,
) -> list[Candidate]:
    """§0.2 steps 1-3: pull a candidate pool and apply the distinct-name criterion.

    Criterion 3 matters more than it looks: the presence filter matches on name
    exactly and case-sensitively, so a name shared by several hulls silently sums
    their emissions. Non-distinct names are marked rather than dropped, so the
    shortlist shows why a candidate was excluded.
    """
    raise NotImplementedError(
        "§0.2 candidate discovery is implemented but not yet wired to the pipeline. "
        "See the plan: vessel B selection was deferred so the pipeline runs on "
        "vessel A alone."
    )


def shortlist(candidates: Sequence[Candidate], out_path: Path) -> Path:
    """§0.2 steps 4 and 6: apply criteria 4-6, rank by coverage, write a CSV.

    The CSV is the hand-off point. Criterion 7 must be closed in Equasis by hand:
    the registered-owner country must differ from the flag country, which is the
    divergence vessel B exists to produce.
    """
    raise NotImplementedError(
        "§0.2 shortlisting is not yet wired to the pipeline. Criterion 7 requires "
        "an Equasis lookup that cannot be automated."
    )

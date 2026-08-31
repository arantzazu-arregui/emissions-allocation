"""National allocation of international shipping CO2 emissions.

A two-vessel replication of Selin et al. (2021), *Mitigation of CO2 emissions from
international shipping through national allocation* (Environ. Res. Lett. 16, 045009),
built entirely from public data.

The pipeline follows ``docs/METHODOLOGY.md`` section by section:

===== =============================================== =============================
 §     Methodology step                               Module
===== =============================================== =============================
 0     Prepare a run and optionally select a vessel   :mod:`selection`
 1     Ingest and preprocess AIS activity             :mod:`activity`
 2     Consolidate ship movement                      :mod:`activity` and SQL
 3     Acquire ship specifications                    :mod:`specs`
 4     Estimate hourly engine power demand             :mod:`emissions`
 5     Assign fuel and calculate CO2                  :mod:`fuel`, :mod:`emissions`
 6     Allocate international-voyage emissions        :mod:`allocation`
 7     Compute allocation impacts                     :mod:`baselines`, :mod:`impacts`
 8     Validate, inspect, and extend                  :mod:`validate`
===== =============================================== =============================

Python owns API access, parsing and the physical model; DuckDB owns spatial joins,
aggregation, allocation and reporting.

Everything is keyed on the IMO number. Scaling from two vessels to the full fleet is
a loop over the vessel list in ``config/pilot.yaml``, not an edit to model code.
"""

from emissions_allocation.config import (
    Config,
    ConfigError,
    MissingParameter,
    Parameter,
    Vessel,
    load_config,
)
from emissions_allocation.db import Database, load_sql
from emissions_allocation.gfw import (
    GFWClient,
    GFWError,
    PresenceAssertionError,
    assert_presence,
)

__all__ = [
    "Config",
    "ConfigError",
    "Database",
    "GFWClient",
    "GFWError",
    "MissingParameter",
    "Parameter",
    "PresenceAssertionError",
    "Vessel",
    "assert_presence",
    "load_config",
    "load_sql",
]

"""National allocation of international shipping CO2 emissions.

A two-vessel replication of Selin et al. (2021), *Mitigation of CO2 emissions from
international shipping through national allocation* (Environ. Res. Lett. 16, 045009),
built entirely from public data.

The pipeline follows ``docs/METHODOLOGY.md`` section by section:

===== ======================================== =========================
 §     Stage                                    Module
===== ======================================== =========================
 0     Select vessels                           :mod:`selection`
 1     Obtain ship activity data                :mod:`activity`
 2     Ship specifications and parameters       :mod:`specs`
 3     Fuel type and emission factors           :mod:`fuel`
 4     Calculate CO2 emissions                  :mod:`emissions`
 5     Allocate to countries                    :mod:`allocation`
 6     Compare with carbon budgets              :mod:`baselines`
 7     Compute allocation impacts               :mod:`impacts`
 8     Sensitivity and validation               :mod:`validate`
===== ======================================== =========================

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

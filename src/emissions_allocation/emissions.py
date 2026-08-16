"""§4 -- operating mode, power demand, SFC correction and CO2.

Converts the hourly activity series into CO2 mass per hour, summed to ship-year.

One ordering decision worth stating, because the specification is circular on it:
IMO Table 16 assigns an operating mode from *main-engine load*, while §4.2 zeroes
main-engine power in the At berth and Anchored *modes*. Load is therefore computed
from smoothed speed first, used to assign the mode, and only then zeroed where the
mode requires it. This is consistent because the matrix consults load only above
3 kn, where the mode is never berth or anchored.

Physical formulas live here. The mode matrix, the Table 17 range join and all
aggregation live in SQL.
"""

from __future__ import annotations


def main_engine_load(*args, **kwargs):
    """``Load_i = (SOG_smoothed / V)^3``, capped at 1.0 (§4.2)."""
    raise NotImplementedError("emissions.main_engine_load is not yet implemented.")


def load_correction_factor(*args, **kwargs):
    """``CF_L = 0.455*Load^2 - 0.710*Load + 1.280`` -- IMO equation (10)."""
    raise NotImplementedError("emissions.load_correction_factor is not yet implemented.")


def fuel_consumption_hour(*args, **kwargs):
    """Main engine, auxiliary and boiler fuel in grams for one hour (§4.4)."""
    raise NotImplementedError("emissions.fuel_consumption_hour is not yet implemented.")


def co2_hour(*args, **kwargs):
    """``E_CO2,i = FC_i * EF_f / 1e6`` in tonnes (§4.5)."""
    raise NotImplementedError("emissions.co2_hour is not yet implemented.")


def annual_emissions(*args, **kwargs):
    """Ship-year totals, with and without the coverage correction (§4.5)."""
    raise NotImplementedError("emissions.annual_emissions is not yet implemented.")

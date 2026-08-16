"""§3 -- fuel assignment, emission factors and specific fuel consumption.

A vessel-hour is assigned distillate fuel (MDO/MGO) when any of three conditions
holds, and residual fuel (HFO) otherwise: the main engine is high-speed; the position
falls inside an ECA polygon; or the hour belongs to a voyage leg between two EU ports.

The third condition is read from ``voyage_leg.is_eu_eu`` -- a genuine improvement on
the EEZ proxy that gridded-only data would have forced.

The IMO 2020 sulphur cap is immaterial here: the Fourth GHG Study assigns low-sulphur
HFO the same carbon content and emission factor as HFO, so the switch affects SOx,
not CO2. Whether the vessel carries a scrubber does not change the result.
"""

from __future__ import annotations


def assign_fuel(*args, **kwargs):
    """Distillate or residual per vessel-hour, by the three-condition rule (§3.1)."""
    raise NotImplementedError("fuel.assign_fuel is not yet implemented.")


def emission_factor(*args, **kwargs):
    """``EF_f`` in g CO2 per g fuel, from IMO Table 21."""
    raise NotImplementedError("fuel.emission_factor is not yet implemented.")


def sfc_base(*args, **kwargs):
    """Base specific fuel consumption in g/kWh, from IMO Table 19 (2001+ column)."""
    raise NotImplementedError("fuel.sfc_base is not yet implemented.")

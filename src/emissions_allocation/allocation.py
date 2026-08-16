"""§5 -- allocation keys and the domestic/international test.

Attributes annual CO2 to countries under each allocation rule: flag, owner, manager
and operator. The fifth option, bunker fuel, is not computable at this scale -- it
rests on national marine-bunker sales statistics, and allocating one ship's emissions
to a bunkering country would require knowing where it took fuel, which no public
dataset records. Out of scope by construction, not by omission.

At n=2 the allocation reduces to assigning each vessel's total to one country per
option, but the SQL is written as the general fleet aggregation so scaling needs no
change.
"""

from __future__ import annotations


def allocate(*args, **kwargs):
    """``E_c,option = sum over ships of E_ship where key_option(ship) = c`` (§5.3)."""
    raise NotImplementedError("allocation.allocate is not yet implemented.")


def domestic_test(*args, **kwargs):
    """Domestic if >95% of hours lie in a single country's EEZ (§5.4)."""
    raise NotImplementedError("allocation.domestic_test is not yet implemented.")

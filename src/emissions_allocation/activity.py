"""§1 -- ship activity: presence, port visits, speed derivation and smoothing.

Produces a continuous, ordered, hourly position-and-speed series across the study
period, plus the sequence of port calls that defines the vessel's voyages.

Two things here are not cosmetic.

**The three presence assertions** (:func:`emissions_allocation.gfw.assert_presence`)
run on every pull. A wrong ship name returns HTTP 200 with zero rows and no error.

**Smoothing is mandatory.** GFW credits each vessel-hour to a single 0.01 degree
cell. A ship crossing ~22 cells per hour lands unpredictably within them, so
consecutive-centroid speeds oscillate -- 3.36 to 21.63 kn observed while the vessel
cruised steadily at ~15 kn. Because propulsion power scales as v^3 the error does
not average out: ``mean(v^3)`` = 2,654 against ``(mean v)^3`` = 1,588, a 1.67x
overestimate, falling to 1.19x with a 3-hour centred average. The window is a
sensitivity parameter, not a fixed choice.

Outputs: ``vessel_hour``, ``port_call``, ``voyage_leg``.
"""

from __future__ import annotations


def load_presence(*args, **kwargs):
    """Pull hourly presence per calendar year and apply the three assertions."""
    raise NotImplementedError("activity.load_presence is not yet implemented.")


def load_port_visits(*args, **kwargs):
    """Pull port visits across every vesselId belonging to the hull, and union them."""
    raise NotImplementedError("activity.load_port_visits is not yet implemented.")


def derive_speed(*args, **kwargs):
    """Great-circle speed between consecutive hourly positions (§1.5)."""
    raise NotImplementedError("activity.derive_speed is not yet implemented.")


def smooth_speed(*args, **kwargs):
    """Centred moving average of width w over the derived speed series (§1.6)."""
    raise NotImplementedError("activity.smooth_speed is not yet implemented.")


def build_voyage_legs(*args, **kwargs):
    """Consecutive port pairs via LAG, with the EU-to-EU flag (§1 outputs)."""
    raise NotImplementedError("activity.build_voyage_legs is not yet implemented.")

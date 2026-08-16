"""§2 -- ship specifications: TEU inversion and the three power/speed estimates.

The central data constraint of this replication. Selin et al. used IHS World Register
of Shipping, a paid commercial register, for installed power and design speed. The
IMO Fourth GHG Study's own fallback regressions (equations 3 and 4) publish only
symbolic coefficients and are mutually circular -- speed requires power, power
requires speed. **No free source supplies these two parameters.**

Three independent estimates are therefore carried in parallel, with no primary, and
the spread between them is a reported output rather than an error to be resolved:

A. IMO EEXI curve fit (MEPC.333(76)). Fails the fleet-envelope check for this hull.
B. Admiralty coefficient calibrated on Charchalis (2014), Froude-number speed.
C. Sourced specification per hull -- OPEN ITEM 4, absent, raises on use.
"""

from __future__ import annotations


def teu_from_beam(*args, **kwargs):
    """Invert Cepowski & Chorab's beam relation: ``TEU = (B / 3.27)^(1/0.29)``."""
    raise NotImplementedError("specs.teu_from_beam is not yet implemented.")


def estimate_a_eexi(*args, **kwargs):
    """``V = A*DWT^B``, ``MCR = C*DWT^D`` -- MEPC.333(76) curve fit."""
    raise NotImplementedError("specs.estimate_a_eexi is not yet implemented.")


def estimate_b_admiralty(*args, **kwargs):
    """Froude-number speed and ``MCR = displacement^(2/3) * V^3 / C_adm``."""
    raise NotImplementedError("specs.estimate_b_admiralty is not yet implemented.")


def estimate_c_sourced(*args, **kwargs):
    """Sourced installed power and service speed. Raises: OPEN ITEM 4."""
    raise NotImplementedError("specs.estimate_c_sourced is not yet implemented.")


def check_fleet_envelope(*args, **kwargs):
    """Design speed must fall inside the observed modern container fleet range."""
    raise NotImplementedError("specs.check_fleet_envelope is not yet implemented.")

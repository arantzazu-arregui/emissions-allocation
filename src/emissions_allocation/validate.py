"""§8 -- validation checks.

===================== ========================================================
 Check                 Basis
===================== ========================================================
 THETIS-MRV            Verified annual CO2, published because the vessel makes
                       35 EU port calls. Genuine external ground truth and the
                       strongest check available. PENDING -- no export present.
 Hour conservation     Observed hours against elapsed time, per year. Measured
                       99.98% for 2024.
 Leg-speed             Great-circle distance between port calls / leg duration
                       should give sensible average speeds.
 Port-call agreement   Berth periods in the track must coincide with port-visit
                       events.
 Identity integrity    Exactly one distinct IMO in every presence pull.
 Fleet envelope        Design speed within 6.0-24.5 kn. Estimate A fails this
                       test at 28.92 kn.
===================== ========================================================

THETIS-MRV is used **only** to validate, never as an input -- it is EU-scope, and this
study allocates emissions globally.
"""

from __future__ import annotations


def check_hour_conservation(*args, **kwargs):
    """Observed hours against elapsed time, per year."""
    raise NotImplementedError("validate.check_hour_conservation is not yet implemented.")


def check_leg_speeds(*args, **kwargs):
    """Great-circle distance between consecutive port calls over leg duration."""
    raise NotImplementedError("validate.check_leg_speeds is not yet implemented.")


def check_port_call_agreement(*args, **kwargs):
    """Berth periods in the track must coincide with port-visit events."""
    raise NotImplementedError("validate.check_port_call_agreement is not yet implemented.")


def check_fleet_envelope(*args, **kwargs):
    """Estimated design speed must fall inside the observed fleet range."""
    raise NotImplementedError("validate.check_fleet_envelope is not yet implemented.")


def compare_thetis_mrv(*args, **kwargs):
    """Compare modelled annual CO2 against THETIS-MRV verified figures.

    Returns a PENDING result when no export is present, rather than silently
    skipping the check.
    """
    raise NotImplementedError("validate.compare_thetis_mrv is not yet implemented.")

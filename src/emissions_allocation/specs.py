"""§3 -- ship specifications and power/speed estimates.

The central data constraint of this replication. Selin et al. used IHS World Register
of Shipping, a paid commercial register, for installed power and design speed. The
IMO Fourth GHG Study's own fallback regressions (equations 3 and 4) publish only
symbolic coefficients and are mutually circular -- speed requires power, power
requires speed. **No free source supplies these two parameters.**

Independent estimates are therefore carried in parallel, with no primary, and
the spread between them is a reported output rather than an error to be resolved:

A. IMO EEXI curve fit (MEPC.333(76)). Returns 25.55 kn for vessel A after the
   resolution's containership caps are applied. It remains just above the observed
   modern container fleet envelope and is flagged as such.
B. Admiralty coefficient calibrated on Charchalis (2014), Froude-number speed. Both
   displacement conventions are carried because they bracket a real ~18% difference.
C. Sourced specification per hull -- OPEN ITEM 4, absent, raises on use.
D. EPA DWT-to-horsepower regression, paired with a separately documented reference
   speed. The pairing is Froude speed for containers and EEXI speed for the vehicle
   carrier; EPA itself estimates power only.

Every returned estimate carries its own provenance, so a notebook table can mark
each number as estimated without the caller tracking which is which.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from emissions_allocation.config import Config, ConfigError, MissingParameter, Vessel

NL = chr(10)

log = logging.getLogger(__name__)

GRAVITY = 9.80665           # m/s^2
KNOTS_PER_MS = 1 / 0.5144   # 0.5144 m/s per knot


@dataclass(frozen=True)
class PowerEstimate:
    """One (design speed, installed power) pair with its provenance.

    ``mcr_kw`` is *installed* power. MEPC.333(76) calls it ``P_ME`` while the Fourth
    GHG Study uses that symbol for instantaneous demand, so this project says ``MCR``
    throughout to keep the two apart.
    """

    label: str
    design_speed_kn: float
    mcr_kw: float
    load_at_reference: float
    reference_condition: str
    source: str
    method: str
    speed_exponent: float = 3.0
    estimated: bool = True
    within_fleet_envelope: bool | None = None
    variants: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        mark = " [estimated]" if self.estimated else ""
        warn = "" if self.within_fleet_envelope is not False else "  <- OUTSIDE FLEET ENVELOPE"
        return (
            f"{self.label}: {self.design_speed_kn:.2f} kn, "
            f"{self.mcr_kw:,.0f} kW, reference load {self.load_at_reference:.2f}"
            f"{mark}{warn}"
        )


def _reference_parameters(
    defaults: dict[str, Any], estimate: str, ship_type: str
) -> dict[str, Any]:
    """Return sourced reference-load parameters for one estimate and hull type.

    Parameters
    ----------
    defaults : dict[str, Any]
        Shared vessel-specification configuration.
    estimate : str
        Estimate label (``A`` through ``D``).
    ship_type : str
        Canonical project ship type.

    Returns
    -------
    dict[str, Any]
        Reference-load fraction, reference condition, and speed exponent.

    Raises
    ------
    MissingParameter
        If the estimate/type pair has no explicit, sourced reference condition.

    Notes
    -----
    The speed at which a source quotes a power value is not generally the speed at
    100% MCR. Keeping this mapping in configuration prevents a universal ``1.0``
    anchor from silently being applied to unlike estimates.
    """
    table = (defaults.get("power_reference") or {}).get(estimate) or {}
    row = (
        table.get("by_ship_type", {}).get(ship_type)
        or table.get(ship_type)
        or table.get("default")
    )
    if not row:
        raise MissingParameter(
            f"estimate {estimate!r} has no power-reference parameters for ship type "
            f"{ship_type!r}. Add defaults.power_reference.{estimate} in "
            "config/vessel_specs.yaml; no shared reference-load default is used."
        )
    load = row.get("load_at_reference")
    if not isinstance(load, (int, float)) or not 0 < load <= 1:
        raise MissingParameter(
            f"estimate {estimate!r} has invalid load_at_reference {load!r} for "
            f"ship type {ship_type!r}; it must be in (0, 1]."
        )
    if not row.get("reference_condition"):
        raise MissingParameter(
            f"estimate {estimate!r} has no reference_condition for ship type "
            f"{ship_type!r}."
        )
    exponent = row.get("speed_exponent", 3.0)
    if not isinstance(exponent, (int, float)) or exponent <= 0:
        raise MissingParameter(
            f"estimate {estimate!r} has invalid speed_exponent {exponent!r} for "
            f"ship type {ship_type!r}; it must be positive."
        )
    return row


# ---------------------------------------------------------------------------
# 2.1 -- TEU capacity
# ---------------------------------------------------------------------------


def teu_from_beam(beam_m: float, coefficient: float = 3.27, exponent: float = 0.29) -> float:
    """Invert Cepowski & Chorab's beam relation.

    ``B = 3.27 * TEU^0.29``  ->  ``TEU = (B / 3.27)^(1/0.29)``

    Beam is the better proxy for container ships because it sets the on-deck row
    count. Their draught relation gives 15,840 TEU for vessel A against beam's
    13,174; their TEU-based LBP relation returns 79.5 m at 13,200 TEU and is
    evidently mis-transcribed, so it is not used.

    IMO Table 17 indexes container ships by TEU and Equasis does not carry it, which
    is the only reason this inversion is needed.
    """
    if beam_m <= 0:
        raise ValueError(f"beam must be positive, got {beam_m}")
    return (beam_m / coefficient) ** (1 / exponent)


def validate_hull_relations(vessel: Vessel, defaults: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Cepowski & Chorab's deadweight relations against the observed hull.

    Not an input to the model. This is the basis for trusting the TEU inversion:
    if the same paper's DWT relations reproduce this hull's dimensions, its beam
    relation is likely sound too.
    """
    block = defaults["hull_from_dwt_validation"]
    dwt = vessel.require_spec("dwt")
    observed = {
        "lbp": vessel.require_spec("lbp_m"),
        "beam": vessel.require_spec("beam_m"),
        "draught": vessel.require_spec("draught_m"),
    }
    out = {}
    for name, relation in ((k, v) for k, v in block.items() if isinstance(v, dict)):
        predicted = relation["coefficient"] * dwt ** relation["exponent"]
        out[name] = {
            "predicted": predicted,
            "observed": observed[name],
            "error_pct": 100 * (predicted - observed[name]) / observed[name],
        }
    return out


# ---------------------------------------------------------------------------
# 2.2 -- estimate A, the IMO EEXI curve fit
# ---------------------------------------------------------------------------


def _capacity(vessel: Vessel, row: dict[str, Any]) -> float:
    """The capacity parameter for one MEPC.333(76) row, with its cap applied.

    Not simply deadweight: containerships cap it (80,000 for speed, 95,000 for
    power) and cruise ships with non-conventional propulsion use GT. Both rules
    come from the resolution and both silently return a wrong number if ignored.
    """
    value = vessel.require_spec("gt" if row.get("capacity") == "gt" else "dwt")
    cap = row.get("capacity_cap")
    return min(value, cap) if cap else value


def estimate_a_eexi(vessel: Vessel, defaults: dict[str, Any], cfg: Config | None = None) -> PowerEstimate:
    """``V_ref,avg = A*B^C`` and ``MCR_avg = D*E^F`` -- MEPC.333(76), Appendix.

    Both tables come from ``config/eexi_parameters.yaml``, which carries all twelve
    ship types at the resolution's full precision, with the containership caps and
    the cruise GT exception encoded.

    For vessel A both caps bind: 156,610 DWT gives 25.55 kn on min(DWT, 80,000) and
    67,912 kW on min(DWT, 95,000). Applying them uncapped returns 28.89 kn and
    113,673 kW -- a 1.6x error in installed power, and the reason an earlier reading
    concluded the method "fails at the top of the container range". It does not; the
    caps exist for that range.
    """
    if cfg is None:
        raise MissingParameter(
            "estimate_a_eexi needs the Config to resolve MEPC.333(76) parameters "
            "from config/eexi_parameters.yaml"
        )
    key = cfg.eexi_type(vessel.require_spec("ship_type"))
    speed_row = cfg.eexi["speed"][key]
    power_row = cfg.eexi["power"][key]

    speed = speed_row["A"] * _capacity(vessel, speed_row) ** speed_row["C"]
    mcr = power_row["D"] * _capacity(vessel, power_row) ** power_row["F"]
    reference = _reference_parameters(defaults, "A", vessel.require_spec("ship_type"))

    return PowerEstimate(
        label="A (EEXI curve fit)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        load_at_reference=reference["load_at_reference"],
        reference_condition=reference["reference_condition"],
        source=f"{cfg.eexi['source']['document']} (via {cfg.eexi['source']['via']})",
        method=(
            f"V = {speed_row['A']} * {speed_row.get('capacity','dwt')}"
            f"{f'(cap {speed_row["capacity_cap"]:,})' if speed_row.get('capacity_cap') else ''}"
            f"^{speed_row['C']}; "
            f"MCR = {power_row['D']} * {power_row.get('capacity','dwt')}"
            f"{f'(cap {power_row["capacity_cap"]:,})' if power_row.get('capacity_cap') else ''}"
            f"^{power_row['F']}"
        ),
        within_fleet_envelope=check_fleet_envelope(
            speed, defaults, vessel.require_spec("ship_type")
        ),
        variants={
            "eexi_type": key,
            "capacity_speed": _capacity(vessel, speed_row),
            "capacity_power": _capacity(vessel, power_row),
            "reference_source": reference["source"],
            "reference_method": reference["method"],
        },
        speed_exponent=reference.get("speed_exponent", 3.0),
    )


# ---------------------------------------------------------------------------
# 2.2 -- estimate B, the calibrated Admiralty coefficient
# ---------------------------------------------------------------------------


def froude_speed_kn(froude_number: float, lbp_m: float) -> float:
    """``V = Fn * sqrt(g * L_BP) / 0.5144`` in knots."""
    return froude_number * math.sqrt(GRAVITY * lbp_m) * KNOTS_PER_MS


def displacement_from_dimensions(
    block_coefficient: float, lbp_m: float, beam_m: float, draught_m: float, density: float
) -> float:
    """``displacement = C_B * L_BP * B * T * rho`` in tonnes."""
    return block_coefficient * lbp_m * beam_m * draught_m * density


def admiralty_power_kw(displacement_t: float, speed_kn: float, c_adm: float) -> float:
    """``MCR = displacement^(2/3) * V^3 / C_adm``."""
    return displacement_t ** (2 / 3) * speed_kn**3 / c_adm


def estimate_b_admiralty(vessel, defaults):
    """Froude-number speed with the Admiralty power relation.

    **Every parameter here is hull-form specific**, so they are keyed by ship type.
    Charchalis published 17 container ships; there is no equivalent for car
    carriers, ro-ros or tankers, and C_adm, the block coefficient and the Froude
    range all differ materially between hull forms.

    Raises:
        MissingParameter: For any ship type without a published calibration.
            Borrowing the container numbers would return a confident wrong figure --
            the failure the three-estimate design exists to expose, not commit.
    """
    ship_type = vessel.require_spec("ship_type")
    block = defaults["admiralty"]
    calibration = (block.get("by_ship_type") or {}).get(ship_type)

    if not calibration:
        available = sorted(k for k, v in (block.get("by_ship_type") or {}).items() if v)
        raise MissingParameter(
            "no Admiralty calibration for ship type " + repr(ship_type) + "." + NL
            + "  C_adm, the block coefficient and the Froude range are all hull-form"
            + " specific, and only " + str(available) + " are calibrated"
            + " (Charchalis 2014 published container ships only)." + NL
            + "  Supply a sourced calibration under defaults.admiralty.by_ship_type."
            + ship_type + ", or drop 'B' from run.power_estimates for this hull." + NL
            + "  No default is substituted."
        )

    c_adm = calibration["c_adm"]["median"]
    fn_min = calibration["froude_number"]["min"]
    fn_max = calibration["froude_number"]["max"]

    lbp = vessel.require_spec("lbp_m")
    speed_min = froude_speed_kn(fn_min, lbp)
    speed_max = froude_speed_kn(fn_max, lbp)
    speed = (speed_min + speed_max) / 2

    displacement_geometric = displacement_from_dimensions(
        calibration["block_coefficient"]["value"], lbp,
        vessel.require_spec("beam_m"), vessel.require_spec("draught_m"),
        block["seawater_density"]["value"],
    )
    displacement_ratio = (
        vessel.require_spec("dwt") / calibration["dwt_to_displacement_ratio"]["value"]
    )
    displacement = (displacement_geometric + displacement_ratio) / 2
    mcr = admiralty_power_kw(displacement, speed, c_adm)
    reference = _reference_parameters(defaults, "B", ship_type)

    return PowerEstimate(
        label="B (Admiralty, calibrated)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        load_at_reference=reference["load_at_reference"],
        reference_condition=reference["reference_condition"],
        source=(calibration["c_adm"]["source"] + "; Froude range from "
                + calibration["froude_number"]["source"]),
        method=("V = Fn*sqrt(g*L_BP)/0.5144 over Fn " + str(fn_min) + "-" + str(fn_max)
                + "; MCR = displacement^(2/3)*V^3/C_adm with C_adm=" + str(c_adm)
                + " (calibrated on " + ship_type + " hulls)"),
        within_fleet_envelope=check_fleet_envelope(speed, defaults, ship_type),
        variants={
            "speed_kn_range": (speed_min, speed_max),
            "displacement_geometric_t": displacement_geometric,
            "displacement_ratio_t": displacement_ratio,
            "mcr_kw_range": (
                admiralty_power_kw(displacement_geometric, speed_min, c_adm),
                admiralty_power_kw(displacement_ratio, speed_max, c_adm),
            ),
            "c_adm": c_adm,
            "c_adm_range": (calibration["c_adm"]["min"], calibration["c_adm"]["max"]),
            "calibrated_on": ship_type,
            "reference_source": reference["source"],
            "reference_method": reference["method"],
        },
        speed_exponent=reference.get("speed_exponent", 3.0),
    )


# ---------------------------------------------------------------------------
# 2.2 -- estimate C, the sourced specification
# ---------------------------------------------------------------------------


def estimate_c_sourced(vessel: Vessel, defaults: dict[str, Any]) -> PowerEstimate:
    """Installed power and service speed from records for this specific hull.

    Most defensible for the pilot, and does not scale.

    Raises:
        MissingParameter: Always, until someone supplies the values. OPEN ITEM 4 --
            no free source has been found. Nothing is substituted, because these two
            numbers look guessable and are not.
    """
    speed = vessel.require_spec(
        "power_C_design_speed_kn",
        because=(
            "Estimate C is a sourced specification (docs/METHODOLOGY.md Section 3, open "
            "item 4). Fill in vessel_specs.yaml vessels.<imo>.power_estimates.C from "
            "shipbuilder or class-society records, or drop 'C' from "
            "config/pilot.yaml run.power_estimates."
        ),
    )
    mcr = vessel.require_spec("power_C_mcr_kw")
    parameter = vessel.spec("power_C_mcr_kw")
    reference = vessel.require_spec(
        "power_C_reference",
        because=(
            "Estimate C needs the load fraction and condition at which its sourced "
            "speed was reported."
        ),
    )

    return PowerEstimate(
        label="C (sourced)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        load_at_reference=reference["load_at_reference"],
        reference_condition=reference["reference_condition"],
        source=parameter.source or "sourced",
        method=parameter.method or "sourced specification",
        within_fleet_envelope=check_fleet_envelope(
            speed, defaults, vessel.require_spec("ship_type")
        ),
        speed_exponent=reference.get("speed_exponent", 3.0),
    )


# ---------------------------------------------------------------------------
# 2.2 -- estimate D, EPA DWT-to-main-engine-power regression
# ---------------------------------------------------------------------------


def epa_main_engine_power_kw(
    dwt_t: float,
    slope_hp_per_dwt: float,
    intercept_hp: float,
    hp_to_kw: float,
) -> float:
    """Estimate rated main-engine power from deadweight.

    Parameters
    ----------
    dwt_t : float
        Vessel deadweight in tonnes.
    slope_hp_per_dwt : float
        Regression slope in hp per tonne DWT.
    intercept_hp : float
        Regression intercept in hp.
    hp_to_kw : float
        Conversion from mechanical horsepower to kW.

    Returns
    -------
    float
        Estimated rated main-engine power in kW.

    Notes
    -----
    This is the EPA (2000) DWT regression. It estimates main-engine power
    directly; Table 17 auxiliary operating loads are not an input.
    """
    if dwt_t <= 0:
        raise ValueError(f"DWT must be positive, got {dwt_t}")
    return (slope_hp_per_dwt * dwt_t + intercept_hp) * hp_to_kw


def estimate_d_epa_dwt(
    vessel: Vessel, defaults: dict[str, Any], cfg: Config | None = None
) -> PowerEstimate:
    """EPA DWT-to-power estimate paired with a configured reference speed.

    Parameters
    ----------
    vessel : Vessel
        Vessel whose DWT and ship type determine the power regression.
    defaults : dict[str, Any]
        Shared EPA regression and, where applicable, Admiralty Froude-speed
        configuration.
    cfg : Config, optional
        Required when the EPA row pairs its MCR with the EEXI reference speed.

    Returns
    -------
    PowerEstimate
        Hybrid scenario: EPA-estimated installed MCR and separately estimated
        reference speed.

    Notes
    -----
    The two components retain separate provenance. The EPA regression does not
    estimate design speed. Each ship type must name either a calibrated Froude
    pairing or the EEXI pairing; no container assumption is borrowed for a vehicle
    carrier.
    """
    ship_type = vessel.require_spec("ship_type")
    epa = defaults["epa_dwt_power"]
    row = (epa.get("by_ship_type") or {}).get(ship_type)
    if not row:
        raise MissingParameter(
            f"no EPA DWT-to-power regression is configured for ship type {ship_type!r}. "
            "Add a sourced row under defaults.epa_dwt_power.by_ship_type or exclude 'D'."
        )
    speed_pairing = row.get("speed_pairing")
    if speed_pairing == "froude":
        speed_calibration = (defaults["admiralty"].get("by_ship_type") or {}).get(ship_type)
        if not speed_calibration:
            raise MissingParameter(
                f"estimate D needs a hull-form Froude-speed range for {ship_type!r}; none is configured."
            )
        fn = speed_calibration["froude_number"]
        speed_min = froude_speed_kn(fn["min"], vessel.require_spec("lbp_m"))
        speed_max = froude_speed_kn(fn["max"], vessel.require_spec("lbp_m"))
        speed = (speed_min + speed_max) / 2
        speed_source = "Froude range from " + fn["source"]
        speed_method = "V = Fn*sqrt(g*L_BP)/0.5144"
        speed_variants: dict[str, Any] = {"speed_kn_range": (speed_min, speed_max)}
    elif speed_pairing == "eexi":
        if cfg is None:
            raise MissingParameter(
                "estimate D needs the Config to resolve its EEXI reference-speed pairing"
            )
        eexi_speed = estimate_a_eexi(vessel, defaults, cfg)
        speed = eexi_speed.design_speed_kn
        speed_source = "EEXI reference speed from " + eexi_speed.source
        speed_method = "V = EEXI reference-speed curve"
        speed_variants = {"speed_from_estimate": "A", "speed_kn": speed}
    else:
        raise MissingParameter(
            f"estimate D needs a recognised speed_pairing for {ship_type!r}; got {speed_pairing!r}."
        )
    dwt = vessel.require_spec("dwt")
    hp = row["slope_hp_per_dwt"] * dwt + row["intercept_hp"]
    mcr = epa_main_engine_power_kw(
        dwt, row["slope_hp_per_dwt"], row["intercept_hp"], epa["hp_to_kw"]
    )
    lo, hi = row["typical_dwt_range_t"]
    reference = _reference_parameters(defaults, "D", ship_type)

    return PowerEstimate(
        label="D (EPA DWT power + " + speed_pairing.upper() + " speed)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        load_at_reference=reference["load_at_reference"],
        reference_condition=reference["reference_condition"],
        source=epa["source"] + "; " + speed_source,
        method=(
            f"MCR = ({row['slope_hp_per_dwt']}*DWT + {row['intercept_hp']}) hp "
            f"* {epa['hp_to_kw']} kW/hp; {speed_method}"
        ),
        within_fleet_envelope=check_fleet_envelope(speed, defaults, ship_type),
        variants={
            "epa_main_engine_hp": hp,
            "speed_pairing": speed_pairing,
            **speed_variants,
            "epa_r_squared": row["r_squared"],
            "epa_typical_dwt_range_t": (lo, hi),
            "epa_extrapolated": not (lo <= dwt <= hi),
            "reference_source": reference["source"],
            "reference_method": reference["method"],
        },
        speed_exponent=reference.get("speed_exponent", 3.0),
    )


# ---------------------------------------------------------------------------
# 8.2 -- fleet-envelope validation
# ---------------------------------------------------------------------------


def check_fleet_envelope(speed_kn, defaults, ship_type="container"):
    """Is this design speed inside the observed fleet range for its ship type?

    Hull-form specific: 6.0-24.5 kn is the CONTAINER fleet's range and says nothing
    about a car carrier or a ro-ro.

    Returns True/False where an envelope is published for the type, and None where
    none is -- unknown, rather than a spurious pass or fail.
    """
    envelope = (defaults.get("fleet_speed_envelope") or {}).get(ship_type)
    if not envelope:
        return None
    return bool(envelope["min_kn"] <= speed_kn <= envelope["max_kn"])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_BUILDERS = {
    "A": estimate_a_eexi,
    "B": estimate_b_admiralty,
    "C": estimate_c_sourced,
    "D": estimate_d_epa_dwt,
}


def build_estimates(vessel: Vessel, cfg: Config) -> dict[str, PowerEstimate]:
    """Build every power estimate named in ``config/pilot.yaml``.

    Requesting an estimate whose inputs are absent raises rather than silently
    dropping it -- a scenario space quietly missing an axis is worse than a failure.
    """
    out: dict[str, PowerEstimate] = {}
    for name in vessel.resolve_power_estimates(cfg.run["power_estimates"]):
        builder = _BUILDERS.get(name)
        if builder is None:
            raise MissingParameter(
                f"unknown power estimate {name!r} in config/pilot.yaml "
                f"run.power_estimates. Known: {sorted(_BUILDERS)}"
            )
        out[name] = (builder(vessel, cfg.defaults, cfg) if name in {"A", "D"}
                      else builder(vessel, cfg.defaults))
    return out


def resolve_teu(vessel: Vessel, cfg: Config) -> float:
    """TEU for a container ship, derived rather than configured.

    Kept derived so the inversion is exercised on every run rather than asserted
    once and copied.
    """
    block = cfg.defaults["teu_from_beam"]
    return teu_from_beam(
        vessel.require_spec("beam_m"), block["coefficient"], block["exponent"]
    )


def size_for_table17(vessel, cfg):
    """(ship_type, size, unit) for the IMO Table 17 range join.

    The size basis is read from Table 17 itself rather than assumed: container ships
    are indexed by TEU, most types by deadweight, and ferries, cruise ships, yachts
    and service craft by gross tonnage. Passing deadweight to a GT-indexed row lands
    in the wrong band and returns a plausible wrong number.

    TEU is the only basis that must be derived (2.1); the rest are observed. Gas
    carriers are indexed by cargo volume (``cbm_capacity``), not deadweight.
    """
    ship_type = vessel.require_spec("ship_type")
    types = cfg.factors["auxiliary_boiler_power"]["ship_types"] or {}
    table = types.get(ship_type)
    if not table:
        raise MissingParameter(
            "IMO Table 17 has no rows for ship type " + repr(ship_type) + "." + NL
            + "  Known: " + str(sorted(types)) + NL
            + "  Auxiliary and boiler demand cannot be priced without them."
        )
    unit = table["size_unit"]
    if unit == "TEU":
        return ship_type, resolve_teu(vessel, cfg), unit
    field_by_unit = {"gt": "gt", "dwt": "dwt", "cbm": "cbm_capacity"}
    field = field_by_unit.get(unit)
    if field is None:
        raise MissingParameter(
            f"IMO Table 17 size unit {unit!r} for ship type {ship_type!r} has no "
            "configured vessel-specification basis."
        )
    try:
        size = vessel.require_spec(field)
    except ConfigError as exc:
        raise MissingParameter(
            f"IMO Table 17 uses {unit!r} for ship type {ship_type!r}, but vessel "
            f"{vessel.imo} has no configured {field!r}."
        ) from exc
    return ship_type, size, unit

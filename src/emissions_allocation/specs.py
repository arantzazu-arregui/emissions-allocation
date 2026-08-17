"""§2 -- ship specifications: TEU inversion and the three power/speed estimates.

The central data constraint of this replication. Selin et al. used IHS World Register
of Shipping, a paid commercial register, for installed power and design speed. The
IMO Fourth GHG Study's own fallback regressions (equations 3 and 4) publish only
symbolic coefficients and are mutually circular -- speed requires power, power
requires speed. **No free source supplies these two parameters.**

Three independent estimates are therefore carried in parallel, with no primary, and
the spread between them is a reported output rather than an error to be resolved:

A. IMO EEXI curve fit (MEPC.333(76)). Returns 28.92 kn for vessel A, which is
   **outside the observed modern container fleet envelope** -- the estimate fails its
   own validation check and that failure is a result, not a bug to be patched.
B. Admiralty coefficient calibrated on Charchalis (2014), Froude-number speed. Both
   displacement conventions are carried because they bracket a real ~18% difference.
C. Sourced specification per hull -- OPEN ITEM 4, absent, raises on use.

Every returned estimate carries its own provenance, so a notebook table can mark
each number as estimated without the caller tracking which is which.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from emissions_allocation.config import Config, MissingParameter, Vessel

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
    source: str
    method: str
    estimated: bool = True
    within_fleet_envelope: bool | None = None
    variants: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        mark = " [estimated]" if self.estimated else ""
        warn = "" if self.within_fleet_envelope is not False else "  <- OUTSIDE FLEET ENVELOPE"
        return (
            f"{self.label}: {self.design_speed_kn:.2f} kn, "
            f"{self.mcr_kw:,.0f} kW{mark}{warn}"
        )


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

    return PowerEstimate(
        label="A (EEXI curve fit)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        source=f"{cfg.eexi['source']['document']} (via {cfg.eexi['source']['via']})",
        method=(
            f"V = {speed_row['A']} * {speed_row.get('capacity','dwt')}"
            f"{f'(cap {speed_row["capacity_cap"]:,})' if speed_row.get('capacity_cap') else ''}"
            f"^{speed_row['C']}; "
            f"MCR = {power_row['D']} * {power_row.get('capacity','dwt')}"
            f"{f'(cap {power_row["capacity_cap"]:,})' if power_row.get('capacity_cap') else ''}"
            f"^{power_row['F']}"
        ),
        within_fleet_envelope=check_fleet_envelope(speed, defaults),
        variants={
            "eexi_type": key,
            "capacity_speed": _capacity(vessel, speed_row),
            "capacity_power": _capacity(vessel, power_row),
        },
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


def estimate_b_admiralty(vessel: Vessel, defaults: dict[str, Any]) -> PowerEstimate:
    """Froude-number speed with the Admiralty power relation.

    Both displacement conventions are computed and carried:

    * ``C_B * L_BP * B * T * rho`` gives 176,807 t for vessel A
    * ``DWT / 0.80`` (Charchalis's ratio) gives 195,762 t

    These differ by ~18% on Charchalis's own worked example, so they bracket a real
    convention difference rather than a rounding error. The headline value uses the
    midpoint of the Froude range and the median calibrated ``C_adm``; the full
    bracket travels in ``variants`` for the notebook to show.

    ``C_adm`` is the weakest joint in this estimate -- calibrated on 1,200-1,400 TEU
    feeders and extrapolated across a tenfold size jump.
    """
    block = defaults["admiralty"]
    c_adm = block["c_adm"]["median"]
    fn_min, fn_max = block["froude_number"]["min"], block["froude_number"]["max"]

    lbp = vessel.require_spec("lbp_m")
    speed_min = froude_speed_kn(fn_min, lbp)
    speed_max = froude_speed_kn(fn_max, lbp)
    speed = (speed_min + speed_max) / 2

    displacement_geometric = displacement_from_dimensions(
        block["block_coefficient"]["value"],
        lbp,
        vessel.require_spec("beam_m"),
        vessel.require_spec("draught_m"),
        block["seawater_density"]["value"],
    )
    displacement_ratio = (
        vessel.require_spec("dwt") / block["dwt_to_displacement_ratio"]["value"]
    )
    displacement = (displacement_geometric + displacement_ratio) / 2

    mcr = admiralty_power_kw(displacement, speed, c_adm)

    return PowerEstimate(
        label="B (Admiralty, calibrated)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        source=(
            f"{block['c_adm']['source']}; Froude range from "
            f"{block['froude_number']['source']}"
        ),
        method=(
            f"V = Fn*sqrt(g*L_BP)/0.5144 over Fn {fn_min}-{fn_max}; "
            f"MCR = displacement^(2/3)*V^3/C_adm with C_adm={c_adm}"
        ),
        within_fleet_envelope=check_fleet_envelope(speed, defaults),
        variants={
            "speed_kn_range": (speed_min, speed_max),
            "displacement_geometric_t": displacement_geometric,
            "displacement_ratio_t": displacement_ratio,
            "mcr_kw_range": (
                admiralty_power_kw(displacement_geometric, speed_min, c_adm),
                admiralty_power_kw(displacement_ratio, speed_max, c_adm),
            ),
            "c_adm": c_adm,
            "c_adm_range": (block["c_adm"]["min"], block["c_adm"]["max"]),
        },
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
            "Estimate C is a SOURCED specification (docs/METHODOLOGY.md §2.2, open "
            "item 4). Fill in vessel_specs.yaml vessels.<imo>.power_estimates.C from "
            "shipbuilder or class-society records, or drop 'C' from "
            "config/pilot.yaml run.power_estimates."
        ),
    )
    mcr = vessel.require_spec("power_C_mcr_kw")
    parameter = vessel.spec("power_C_mcr_kw")

    return PowerEstimate(
        label="C (sourced)",
        design_speed_kn=speed,
        mcr_kw=mcr,
        source=parameter.source or "sourced",
        method=parameter.method or "sourced specification",
        within_fleet_envelope=check_fleet_envelope(speed, defaults),
    )


# ---------------------------------------------------------------------------
# 8.2 -- fleet-envelope validation
# ---------------------------------------------------------------------------


def check_fleet_envelope(speed_kn: float, defaults: dict[str, Any]) -> bool:
    """Is this design speed inside the observed modern container fleet range?

    6.0-24.5 kn across 215 distinct container designs built since 2015. Estimate A
    returns 28.92 kn for vessel A and fails. That is a reported validation result,
    not something to correct -- the estimate is carried through to the CO2 figure so
    the reader can see what an out-of-envelope power assumption does.
    """
    envelope = defaults["container_fleet_speed_envelope"]
    return bool(envelope["min_kn"] <= speed_kn <= envelope["max_kn"])


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_BUILDERS = {
    "A": estimate_a_eexi,
    "B": estimate_b_admiralty,
    "C": estimate_c_sourced,
}


def build_estimates(vessel: Vessel, cfg: Config) -> dict[str, PowerEstimate]:
    """Build every power estimate named in ``config/pilot.yaml``.

    Requesting an estimate whose inputs are absent raises rather than silently
    dropping it -- a scenario space quietly missing an axis is worse than a failure.
    """
    out: dict[str, PowerEstimate] = {}
    for name in cfg.run["power_estimates"]:
        builder = _BUILDERS.get(name)
        if builder is None:
            raise MissingParameter(
                f"unknown power estimate {name!r} in config/pilot.yaml "
                f"run.power_estimates. Known: {sorted(_BUILDERS)}"
            )
        out[name] = (builder(vessel, cfg.defaults, cfg) if name == "A"
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


def size_for_table17(vessel: Vessel, cfg: Config) -> tuple[str, float, str]:
    """``(ship_type, size, unit)`` for the IMO Table 17 range join.

    Container ships are indexed by TEU, everything else by deadweight (or cubic
    metres for liquefied gas tankers, which neither pilot hull is).
    """
    ship_type = vessel.require_spec("ship_type")
    if ship_type == "container":
        return ship_type, resolve_teu(vessel, cfg), "TEU"
    return ship_type, vessel.require_spec("dwt"), "dwt"

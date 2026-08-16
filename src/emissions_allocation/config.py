"""Load and validate ``config/*.yaml``.

Three files, loaded together because they cross-reference:

``pilot.yaml``
    Study period, the vessel list, sensitivity axes, paths.
``vessel_specs.yaml``
    Shared regression constants (``defaults``) plus one block per IMO.
``emission_factors.yaml``
    IMO Fourth GHG Study Tables 16, 17, 19, 20, 21 as data.

Two rules this module enforces, both from the project's ground rules:

**Every estimated parameter carries ``value``, ``source`` and ``method``.** A block
marked ``estimated: true`` that is missing any of the three is a configuration
error, raised at load time. The point is that a researcher with IHS World Register
of Shipping access can substitute observed values without touching model code, and
that a reader is never unsure whether a number was observed or derived.

**A missing parameter is never defaulted.** Several parameters in this project look
guessable and are not -- installed power and design speed above all. Where a value
is absent the loader hands back a :class:`Missing` sentinel, and *using* it raises
:class:`MissingParameter` naming the config key and the methodology section that
explains why it is open. Nothing silently substitutes a plausible number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(Exception):
    """The configuration is malformed or internally inconsistent."""


class MissingParameter(Exception):
    """A required parameter is absent from config and has no defensible default.

    Raised at the point of *use*, not at load, so that a pipeline stage which does
    not need the parameter still runs. The message names the config key and the
    methodology section that explains why it is open.
    """


# ---------------------------------------------------------------------------
# Parameter carriers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Parameter:
    """One configured value together with its provenance.

    ``estimated`` is the flag the notebook renders, so a reader can always tell a
    derived number from an observed one.
    """

    name: str
    value: Any
    source: str | None = None
    method: str | None = None
    estimated: bool = False
    unit: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # An estimated parameter that HAS a value must say where it came from.
        #
        # A null value is the separate, legitimate case of an unresolved open item
        # -- estimate C is written as `{value: null, source: null, method: null}`
        # precisely so that it cannot be mistaken for a real number. Demanding
        # provenance for a value that does not exist would force a placeholder
        # source to be invented, which is the opposite of the intent.
        if self.estimated and self.value is not None:
            for attribute in ("source", "method"):
                if not getattr(self, attribute):
                    raise ConfigError(
                        f"{self.name!r} is marked estimated but has no {attribute!r}. "
                        "Every estimated parameter must carry value, source and method "
                        "so it can be substituted without touching model code."
                    )

    @property
    def is_missing(self) -> bool:
        return self.value is None

    def require(self, because: str = "") -> Any:
        """Return the value, or raise :class:`MissingParameter` if it is absent."""
        if self.is_missing:
            raise MissingParameter(
                f"{self.name!r} has no value in config."
                + (f" {because}" if because else "")
                + " No default is substituted -- see the ground rules in CLAUDE.md."
            )
        return self.value

    def label(self) -> str:
        """Human-readable rendering for notebook tables."""
        marker = " [estimated]" if self.estimated else ""
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.value}{unit}{marker}"


def _as_parameter(name: str, raw: Any) -> Parameter:
    """Coerce a YAML node into a :class:`Parameter`.

    Accepts either a bare scalar or a mapping with ``value``/``source``/``method``.
    A bare scalar is treated as observed, because anything estimated is required
    to carry provenance and so cannot be written as a bare scalar.
    """
    if isinstance(raw, dict) and "value" in raw:
        known = {"value", "source", "method", "estimated", "unit"}
        return Parameter(
            name=name,
            value=raw.get("value"),
            source=raw.get("source"),
            method=raw.get("method"),
            estimated=bool(raw.get("estimated", False)),
            unit=raw.get("unit"),
            extra={k: v for k, v in raw.items() if k not in known},
        )
    return Parameter(name=name, value=raw)


# ---------------------------------------------------------------------------
# Vessel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vessel:
    """One hull, keyed on its IMO number.

    IMO is the key everywhere. GFW's ``vesselId`` fragments -- IMO 9516454 has two,
    one of which carries a null IMO and a former name -- so ``vesselId`` is resolved
    at runtime and used only as an API parameter, never as a join key.
    """

    imo: str
    label: str
    shipnames: tuple[str, ...]
    specs: dict[str, Parameter]
    allocation_keys: dict[str, dict[str, Any]]
    former_shipnames_outside_period: tuple[str, ...] = ()
    raw_specs: dict[str, Any] = field(default_factory=dict)

    def spec(self, name: str) -> Parameter:
        if name not in self.specs:
            raise ConfigError(
                f"vessel {self.imo}: no spec {name!r} in config/vessel_specs.yaml"
            )
        return self.specs[name]

    def require_spec(self, name: str, because: str = "") -> Any:
        return self.spec(name).require(because)

    def allocation_country(self, option: str) -> str | None:
        """Country for one allocation option, or ``None`` if not computable."""
        if option not in self.allocation_keys:
            raise ConfigError(
                f"vessel {self.imo}: no allocation key {option!r}. "
                f"Known: {sorted(self.allocation_keys)}"
            )
        return self.allocation_keys[option].get("country")

    def estimated_specs(self) -> dict[str, Parameter]:
        return {k: v for k, v in self.specs.items() if v.estimated}


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    start_date: date
    end_date: date
    vessels: tuple[Vessel, ...]
    run: dict[str, Any]
    paths: dict[str, Path]
    spatial: dict[str, Path]
    validation: dict[str, Any]
    defaults: dict[str, Any]
    factors: dict[str, Any]

    # -- vessels ------------------------------------------------------------

    def vessel(self, imo: str) -> Vessel:
        for v in self.vessels:
            if v.imo == str(imo):
                return v
        raise ConfigError(
            f"IMO {imo!r} is not in config/pilot.yaml. Known: "
            f"{[v.imo for v in self.vessels]}"
        )

    def __iter__(self) -> Iterator[Vessel]:
        """Iterating the config iterates the fleet.

        This is the property that makes the project a template: scaling from two
        vessels to the full fleet is a loop over this, not an edit to model code.
        """
        return iter(self.vessels)

    # -- study period -------------------------------------------------------

    @property
    def years(self) -> list[int]:
        return list(range(self.start_date.year, self.end_date.year + 1))

    @property
    def elapsed_hours(self) -> int:
        from datetime import datetime, timedelta

        start = datetime.combine(self.start_date, datetime.min.time())
        end = datetime.combine(self.end_date, datetime.min.time()) + timedelta(days=1)
        return int((end - start).total_seconds() // 3600)

    def year_range(self, year: int) -> tuple[str, str]:
        """GFW ``date-range`` for one calendar year, end-exclusive."""
        return f"{year}-01-01", f"{year + 1}-01-01"

    # -- paths --------------------------------------------------------------

    def path(self, key: str) -> Path:
        if key not in self.paths:
            raise ConfigError(f"no path {key!r} in config/pilot.yaml paths:")
        p = self.paths[key]
        p.mkdir(parents=True, exist_ok=True)
        return p

    def spatial_layer(self, key: str) -> Path:
        """Path to a spatial layer, or raise if it is an unresolved open item.

        ``coastline`` is deliberately absent from the shipped config: §4.1 needs
        distance-to-coast and no layer has been chosen. Raising here, with the
        download URL, is the ground rule -- no default is substituted.
        """
        if key not in self.spatial:
            raise MissingParameter(
                f"spatial layer {key!r} is not configured.\n"
                f"  This is an OPEN ITEM. See docs/METHODOLOGY.md and config/pilot.yaml.\n"
                f"  For 'coastline': §4.1's operating-mode matrix needs distance-to-coast.\n"
                f"  The IMO Fourth GHG Study measures it against Natural Earth coastline\n"
                f"  shapefiles (Table 16, printed p.66). Download a coastline layer, put it\n"
                f"  in data/external/, and set spatial.coastline in config/pilot.yaml.\n"
                f"  Configured layers: {sorted(self.spatial)}"
            )
        path = self.spatial[key]
        if not path.exists():
            raise MissingParameter(
                f"spatial layer {key!r} is configured as {path} but that file does not exist."
            )
        return path

    # -- scenario space -----------------------------------------------------

    def scenarios(self) -> list[dict[str, Any]]:
        """The sensitivity cross join: power estimate x HK treatment x window.

        §8.1. Built from config so that estimate C joins automatically the day
        someone fills in a sourced installed power and service speed.
        """
        out = []
        for power in self.run["power_estimates"]:
            for hk in self.run["hk_treatments"]:
                for window in self.run["smoothing_windows"]:
                    out.append(
                        {
                            "scenario_id": f"{power}_hk-{hk}_w{window}",
                            "power_estimate": power,
                            "hk_treatment": hk,
                            "smoothing_window": window,
                        }
                    )
        return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_SPEC_SCALARS = (
    "mmsi", "callsign", "flag", "ship_type", "year_built", "dwt", "gt", "gt_gfw",
    "loa_m", "beam_m", "draught_m", "lbp_m", "engine_type",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} did not parse to a mapping")
    return loaded


def _build_vessel(entry: dict[str, Any], specs_block: dict[str, Any]) -> Vessel:
    imo = str(entry["imo"])
    if not specs_block:
        raise ConfigError(
            f"IMO {imo} is listed in config/pilot.yaml but has no block in "
            f"config/vessel_specs.yaml. Every vessel needs both."
        )

    specs: dict[str, Parameter] = {}
    for name in _SPEC_SCALARS:
        if name in specs_block:
            specs[name] = _as_parameter(f"{imo}.{name}", specs_block[name])

    # Power estimates. 'derived' means specs.py computes it; a mapping means it was
    # sourced by hand. Estimate C is null until someone finds the real numbers.
    for est_name, est in (specs_block.get("power_estimates") or {}).items():
        if est == "derived":
            continue
        if isinstance(est, dict):
            for field_name, raw in est.items():
                specs[f"power_{est_name}_{field_name}"] = _as_parameter(
                    f"{imo}.power_estimates.{est_name}.{field_name}", raw
                )

    keys = specs_block.get("allocation_keys") or {}
    if not keys:
        raise ConfigError(f"vessel {imo}: no allocation_keys in config/vessel_specs.yaml")

    return Vessel(
        imo=imo,
        label=entry.get("label", imo),
        shipnames=tuple(entry.get("shipnames") or ()),
        former_shipnames_outside_period=tuple(
            entry.get("former_shipnames_outside_period") or ()
        ),
        specs=specs,
        allocation_keys=keys,
        raw_specs=specs_block,
    )


def load_config(config_dir: Path | None = None) -> Config:
    """Load and validate the three config files."""
    cfg_dir = Path(config_dir) if config_dir else CONFIG_DIR

    pilot = _load_yaml(cfg_dir / "pilot.yaml")
    vessel_specs = _load_yaml(cfg_dir / "vessel_specs.yaml")
    factors = _load_yaml(cfg_dir / "emission_factors.yaml")

    study = pilot.get("study") or {}
    for key in ("start_date", "end_date"):
        if key not in study:
            raise ConfigError(f"config/pilot.yaml: study.{key} is required")

    entries = pilot.get("vessels") or []
    if not entries:
        raise ConfigError(
            "config/pilot.yaml lists no vessels. At least one is required; see "
            "docs/METHODOLOGY.md §0.2 for how vessel B is selected."
        )

    spec_blocks = vessel_specs.get("vessels") or {}
    vessels = tuple(
        _build_vessel(entry, spec_blocks.get(str(entry["imo"])) or {}) for entry in entries
    )

    seen: set[str] = set()
    for v in vessels:
        if v.imo in seen:
            raise ConfigError(f"IMO {v.imo} is listed twice in config/pilot.yaml")
        seen.add(v.imo)
        if not v.shipnames:
            raise ConfigError(
                f"vessel {v.imo}: no shipnames. The GFW presence filter matches on "
                "shipname, exactly and case-sensitively -- a wrong or missing name "
                "returns HTTP 200 with zero rows and NO ERROR."
            )

    run = pilot.get("run") or {}
    for key in ("smoothing_windows", "hk_treatments", "power_estimates"):
        if not run.get(key):
            raise ConfigError(f"config/pilot.yaml: run.{key} is required and non-empty")

    bad = [w for w in run["smoothing_windows"] if w % 2 == 0]
    if bad:
        raise ConfigError(
            f"config/pilot.yaml: smoothing_windows must be odd (the average is centred); "
            f"got {bad}"
        )

    paths = {k: PROJECT_ROOT / v for k, v in (pilot.get("paths") or {}).items()}
    spatial = {k: PROJECT_ROOT / v for k, v in (pilot.get("spatial") or {}).items()}

    return Config(
        start_date=study["start_date"],
        end_date=study["end_date"],
        vessels=vessels,
        run=run,
        paths=paths,
        spatial=spatial,
        validation=pilot.get("validation") or {},
        defaults=vessel_specs.get("defaults") or {},
        factors=factors,
    )

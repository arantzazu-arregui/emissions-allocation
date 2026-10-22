# Configuration

All non-secret, versioned pipeline settings live in this directory. Vessel-specific
values are configuration, not code: adding a ship is an edit here, not to
`src/emissions_allocation/`.

| File | Contents |
|---|---|
| `pilot.yaml` | Study window, IMO list and exact GFW ship names, scenario axes, coverage and gap thresholds, data paths, spatial layers, territory alignment |
| `vessel_specs.yaml` | Physical specifications, engine class, allocation-country keys, and the source and method of every parameter |
| `eexi_parameters.yaml` | MEPC.333(76) reference-speed and installed-power curves (paper Table C1) |
| `emission_factors.yaml` | Fourth IMO GHG Study operating-mode, auxiliary, SFC, and CO₂-factor tables (paper Tables C5–C9) |

`src/emissions_allocation/config.py` loads and validates these files; see
"Adding a vessel" in the main README for the rules it enforces.

Secrets belong in the local `.env` file or environment variables and must never be committed.

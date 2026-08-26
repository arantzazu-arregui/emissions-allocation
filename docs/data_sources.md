# Data sources and handling

Every source below is public. The project takes that as a constraint, not a convenience:
the deliverable is a template other researchers can run without a commercial licence.

## Inputs

### Global Fishing Watch — AIS activity

Hourly vessel presence from the 4Wings API (`public-global-presence`), port visits from the
Events API (`public-global-port-visits-events`), and identity from the Vessels API. Free
non-commercial token, held in `.env` as `GFW_TOKEN` and never committed.

Raw responses are cached under `data/raw/gfw_cache/` keyed by request, so reruns cost nothing
and a partial run resumes. `data/sample/api/` holds the captured responses from the
investigation phase and **is tracked** — it is the evidence behind the API behaviour recorded
in `CLAUDE.md`. See `scripts/exploratory/README.md`.

Observe the [GFW API terms](https://globalfishingwatch.org/our-apis/documentation#terms-of-use)
when redistributing.

### IMO Fourth Greenhouse Gas Study 2020

Tables 16 (operating-mode matrix), 17 (auxiliary and boiler power), 19 (base SFC), 20 (low
load factors) and 21 (emission factors), plus equations 10 and 11. Extracted to
`config/emission_factors.yaml`.

**Each table was verified against a rendered page, not only against `pdftotext` output.**
Several of this report's tables and all of its equations are images that text extraction
mangles or drops silently. Printed and PDF page numbers are both recorded in the config
because they differ by 28.

### Marine Regions (VLIZ) — CC-BY 4.0

| Layer | Use |
|---|---|
| World EEZ v12 | §5.4 domestic/international test; also the subtrahend for the coastline derivation |
| World High Seas v2 | not-in-any-EEZ mask |
| ECAs, MARPOL Annex VI Reg. 14 | §3.1 fuel switch — 6 polygons, **no Mediterranean** (in force May 2025, after the study period) |
| Marine and Land Zones v4 (`EEZ_land_union`) | §4.1 distance to coast |

Two traps worth recording. The EEZ archive holds **two** GeoPackages — `eez_v12.gpkg` (285
polygons) and `eez_boundaries_v12.gpkg` (2,349 linestrings); choosing the boundaries makes
every point-in-polygon test return nothing with no error. Layer paths are therefore named
explicitly in `config/pilot.yaml`. And Marine and Land Zones is **not a coastline layer** — it
is land merged with EEZ, so land is recovered by differencing it against EEZ v12.

### Global Carbon Budget 2025 — national baselines

*National Fossil Carbon Emissions v2025*, sheet **Territorial Emissions**, header at row
index 11. **Units are MtC — multiply by 3.664 for MtCO₂.** National columns exclude bunkers;
only the World total includes them, so the denominator is clean. The `Regions` sheet supplies
KP Annex B, OECD and EU27 groupings and has **no header row**.

Cite Friedlingstein et al. (2025), ESSD.

### Selin et al. (2021) supplementary Table 1

`data/external/paper/erlabec02supp2.xls` — the paper's own per-country results for all five
allocation options. Used to settle the territory-alignment question: it carries **199
countries and no Hong Kong row**. The fixed replication map therefore assigns Hong Kong to
China. The table retains `Chinese Taipei` with `Taiwan` as its national reference, so Taiwan
is not folded into China.

### Equasis — ownership and management

Flag, tonnage, deadweight, registered owner, ISM manager and commercial manager, with IMO
company identification numbers. Manual per-IMO lookup; no API. Recorded in
`config/vessel_specs.yaml`.

### UNdata — international marine bunkers

Fuel oil and gas oil, transaction "International marine bunkers". Unit is **"Metric tons,
thousand"** — multiply by 1,000. A footnote block follows a blank line at the end of the
file; the parser must stop there. **Fleet-scale use only** — the bunker allocation option is
not computable at n = 2.

## Handling

`data/external/` is a **read-only input** — downloaded source data, never regenerated.
`data/raw/` is API output, `data/interim/` checkpoints, `data/out/` derived tables. All are
git-ignored except `data/sample/`.

## Reproducibility record

For every run, record the API dataset version, date range, spatial and temporal resolution,
vessel filters, region definition, retrieval date, and code commit SHA. The pipeline writes
most of this into `data/interim/`; the notebook prints the rest.

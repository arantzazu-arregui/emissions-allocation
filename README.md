# Emissions allocation

A two-vessel replication of [Selin et al. (2021)](https://doi.org/10.1088/1748-9326/abec02),
*Mitigation of CO₂ emissions from international shipping through national allocation*, built
entirely from public data.

AIS activity from Global Fishing Watch → hourly speed → CO₂ via the IMO Fourth GHG Study
bottom-up model → allocated to countries under flag / owner / manager / operator rules →
compared against Global Carbon Budget national baselines. Study period 2017-01-01 to
2024-12-31.

The deliverable is a **template**. Scaling from two vessels to the full fleet is a loop over
an IMO list in `config/pilot.yaml`, not an edit to model code.

## What it produces

For vessel A (COSCO ITALY, IMO 9516454), 2017–2024:

| | estimate A (EEXI curve fit) | estimate B (Admiralty) |
|---|---|---|
| 8-year CO₂ | 736,770 t | 969,081 t |
| 2024 | 95,178 t | 126,061 t |
| design speed | 28.92 kn — **outside fleet envelope** | 22.62 kn |

The spread is a **reported result, not an error**: no free source supplies installed power or
design speed, so no estimate is primary.

The headline methodological finding, from §7 — identical emissions, two territory
conventions:

| Hong Kong treatment | baseline | ΔE% |
|---|---|---|
| separate | 33.3 Mt CO₂ | 0.287–0.380% |
| folded into China | 12,322 Mt CO₂ | 0.0008–0.0010% |

A ~370× swing in a country's reported burden from a convention choice, not from anything the
ship did.

## Start here

**[`notebooks/01_methodology_walkthrough.ipynb`](notebooks/01_methodology_walkthrough.ipynb)**
walks §0 to §8 with every equation sourced inline, and ships with its outputs. It reads the
cached tables, so it opens instantly.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Put a free non-commercial token from the
[GFW API](https://globalfishingwatch.org/our-apis/) into `.env` as `GFW_TOKEN`. Do not commit
it. DuckDB downloads its `spatial` extension on first use; no geopandas required.

## Run

```powershell
python scripts/run_pipeline.py --stage check      # config, DuckDB, live API assertion
python scripts/run_pipeline.py --all
```

Stages follow the methodology and are individually runnable: `check`, `select`, `activity`,
`specs`, `fuel`, `emissions`, `baselines`, `allocation`, `impacts`, `validate`. API responses
are cached under `data/raw/`, so reruns are free and a partial run resumes. The first full
activity pull is about six minutes a hull; `emissions` takes a few minutes for the
distance-to-coast join.

Regenerate the notebook after a rerun:

```powershell
python notebooks/build_notebook.py
python -m nbconvert --to notebook --execute --inplace notebooks/01_methodology_walkthrough.ipynb
```

## Layout

```text
config/       pilot.yaml, vessel_specs.yaml, emission_factors.yaml — all parameters as data
data/         external (read-only inputs), raw, interim, out; sample/api is tracked
docs/         METHODOLOGY.md is the specification; data_sources.md the handling record
notebooks/    the §0–§8 walkthrough, plus the script that generates it
scripts/      run_pipeline.py; exploratory/ holds the API probes as provenance
src/          one module per methodology section; sql/ holds every join and aggregation
tests/        266 tests
```

Python owns API access, parsing and the physical model. DuckDB owns spatial joins,
aggregation, allocation and reporting. SQL lives in `src/emissions_allocation/sql/*.sql` and
is loaded by name — it is a deliverable in its own right and should be readable on its own.

## Checks

```powershell
pytest
```

## What is deliberately open

Nothing here is defaulted. Each surfaces as a named error or a PENDING marker.

- **Vessel B is not selected.** §0.2 steps 1–4 are automated; step 5 needs an Equasis login.
  Until then the allocation is degenerate — all four options resolve to one budget.
- **Estimate C** (sourced installed power and service speed) has no free source and raises.
- **THETIS-MRV** validation is PENDING; annual CO₂ is unverified against external data.

## Attribution

AIS data from [Global Fishing Watch](https://globalfishingwatch.org/). Spatial layers from
[Marine Regions](https://www.marineregions.org/) (VLIZ), CC-BY 4.0. National baselines from
the [Global Carbon Budget](https://globalcarbonbudget.org/) — Friedlingstein et al. (2025).

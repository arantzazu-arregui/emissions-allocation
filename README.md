# National allocation of international shipping CO2 emissions

An open-data, vessel-level reconstruction of the workflow in Selin et al.
(2021), *Mitigation of CO2 emissions from international shipping through
national allocation*. The project follows two vessels from hourly activity
observations to estimates of international-voyage CO2 emissions, then allocates
those emissions using flag, registered-owner, ISM-manager, and
commercial-manager-as-operator-proxy country rules.

The accompanying paper is available in
[`docs/FinalPaper_ArantzazuArreguiGonzalez.pdf`](docs/FinalPaper_ArantzazuArreguiGonzalez.pdf).

## Project overview

This repository is a reproducible two-vessel pilot for 2017–2024, not a global
shipping inventory or evidence that one allocation rule is preferable at fleet
scale.

It demonstrates an end-to-end public-data workflow for:

1. acquiring hourly Global Fishing Watch (GFW) vessel activity and inferred
   port visits;
2. deriving, smoothing, and gap-treating speed over ground;
3. estimating engine specifications when open registers do not provide them;
4. calculating hourly main-engine, auxiliary-engine, and boiler CO2 emissions;
5. retaining emissions from international port-to-port voyages; and
6. allocating those emissions to countries and comparing them with Global
   Carbon Budget national baselines.

All model data are keyed by the seven-digit IMO number. Vessel names, MMSIs,
flags, and GFW `vesselId` values may change, so they are not used as primary
keys.

| Pilot vessel | IMO | Purpose | Allocation countries after territory alignment |
|---|---:|---|---|
| COSCO ITALY | 9516454 | Large container vessel with co-located commercial roles | China under all four rules; Hong Kong is aligned with China |
| RCC AMERICA | 9277802 | Vehicle carrier selected to expose allocation differences | Bahamas, United Kingdom, and Greece |

## Installation and setup

### Code and resources used

- **Language:** Python 3.11
- **Database and spatial processing:** DuckDB with its `spatial` extension
- **Activity data:** Global Fishing Watch APIs (a free non-commercial token is
  required for live requests)
- **Configuration:** YAML files in [`config/`](config/)
- **Documentation:** [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) is the
  complete operational guide; [`docs/data_sources.md`](docs/data_sources.md)
  records data provenance and handling.

### Python packages used

Install the project requirements:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

The main dependencies are grouped below.

- **Data access and configuration:** `requests`, `pyyaml`
- **Data manipulation and storage:** `pandas`, `pyarrow`, `openpyxl`
- **Database and spatial processing:** `duckdb`, `cartopy`
- **Visualisation:** `matplotlib`
- **Notebook and testing:** `jupyter`, `nbformat`, `pytest`

Set `GFW_TOKEN` in the local `.env` file and do not commit it. Before executing
the spatial, baseline, or allocation stages, download the required external
inputs to `data/external/` as described in
[`docs/data_sources.md`](docs/data_sources.md).

Run a local environment check without calling the API:

```powershell
python scripts/run_pipeline.py --stage check --offline
pytest
```

Remove `--offline` to test a live GFW request. Run the configured workflow, or
an individual restartable stage, with:

```powershell
python scripts/run_pipeline.py --all
python scripts/run_pipeline.py --stage emissions
```

API responses are cached in `data/raw/`, intermediate tables are written to
`data/interim/`, and derived outputs are written to `data/out/`. These generated
directories are ignored by Git, allowing partial runs to resume.

## Data

### Source data

| Source | Role in the workflow |
|---|---|
| [Global Fishing Watch](https://globalfishingwatch.org/) | Hourly vessel presence, inferred port visits, and vessel identity data |
| [IMO Fourth Greenhouse Gas Study 2020](https://www.imo.org/en/ourwork/environment/pages/fourth-imogreenhousegasstudy2020.aspx) | Operating modes, auxiliary/boiler assumptions, fuel consumption, and CO2 factors |
| [Marine Regions / VLIZ](https://www.marineregions.org/) | EEZs, high-seas areas, emission-control areas, and land/EEZ layers |
| [Global Carbon Budget](https://globalcarbonbudget.org/) | National fossil-carbon-emissions baselines |
| Selin et al. (2021) supplementary material | Territory alignment used for replication |
| Equasis and Marine Traffic | Vessel dimensions, technical specifications, and company-role information |

The detailed data record, including dataset versions, citations, licenses, and
file handling, is maintained in [`docs/data_sources.md`](docs/data_sources.md).

### Data acquisition

GFW requests require `GFW_TOKEN`; raw responses are cached by request under
`data/raw/gfw_cache/`. External source files are downloaded manually to
`data/external/`, which is treated as a read-only input directory. The tracked
`data/sample/api/` directory contains captured API responses used to document
and test endpoint behaviour.

### Data preprocessing

The pipeline creates a complete hourly vessel record from GFW presence data,
derives speed over ground from consecutive grid-cell centroids, identifies
long inactive gaps using port visits, and applies configured centred speed
smoothing windows. It then constructs port-to-port voyage legs, joins spatial
context, assigns fuel and operating modes, and calculates emissions for every
configured specification and smoothing scenario.

See [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for formulas, assumptions,
validation rules, and the treatment of coverage correction.

## Code structure

```text
├── config/                      Versioned pipeline configuration
│   ├── pilot.yaml               Study window, vessels, paths, scenarios, and validation settings
│   ├── vessel_specs.yaml        Vessel specifications, allocation keys, and parameter provenance
│   ├── eexi_parameters.yaml     EEXI reference-speed and power-estimation coefficients
│   └── emission_factors.yaml    IMO operating-mode, fuel, SFC, and CO₂-factor tables
├── data/                        Input, cached, intermediate, output, and sample data
│   ├── external/                Downloaded, read-only source inputs
│   ├── raw/                     Cached GFW API responses
│   ├── interim/                 Restartable pipeline tables
│   ├── out/                     Derived outputs
│   └── sample/api/              Tracked captured API responses
├── docs/                        Project documentation and paper
│   ├── METHODOLOGY.md           Operational pipeline guide, assumptions, and formulas
│   ├── data_sources.md          Source provenance, substitutions, and handling requirements
│   ├── data_requirements.md     Input-data requirements and initial data audit
│   └── FinalPaper_ArantzazuArreguiGonzalez.pdf  Final project paper
├── notebooks/                   Executable analysis walkthroughs
│   ├── 00_pipeline_audit.ipynb  Audit of pipeline inputs and API behaviour
│   └── 01_methodology_walkthrough.ipynb  End-to-end methodology and results walkthrough
├── reports/                     Written results and exported figures
│   └── figures/                 Reproducible diagnostic and result figures
├── scripts/                     Executable pipeline utilities
│   ├── run_pipeline.py          Ordered, restartable pipeline entry point
│   └── exploratory/             API investigation and discovery utilities
├── src/                         Installable project source code
│   └── emissions_allocation/    Emissions-allocation Python package
│       ├── __init__.py          Public package exports and project overview
│       ├── activity.py          Presence, port visits, hourly spine, and speed treatment
│       ├── allocation.py        International-voyage and country-allocation logic
│       ├── baselines.py         Global Carbon Budget baseline preparation
│       ├── config.py            YAML configuration loading and validation
│       ├── db.py                DuckDB setup and SQL execution helpers
│       ├── emissions.py         Operating modes, power demand, and CO₂ calculations
│       ├── fuel.py              Fuel assignment and emission-factor handling
│       ├── gfw.py               GFW API client and response assertions
│       ├── impacts.py           Allocation impacts and regional summaries
│       ├── selection.py         Candidate-vessel discovery and filtering
│       ├── specs.py             Vessel-specification and power-estimate scenarios
│       ├── validate.py          Validation and sensitivity checks
│       └── sql/                 DuckDB spatial and reporting queries
├── tests/                       Unit, configuration, API, and model tests
├── .env.example                 Template for the local GFW API token
├── requirements.txt             Python package dependencies
├── README.md                    Project overview, setup, and repository guide
└── .gitignore                   Git exclusions for secrets, environments, and generated data
```

## Results and evaluation

The repository includes reproducible diagnostic figures in
[`reports/figures/`](reports/figures/), covering monthly speed over ground,
design-speed power estimates, fuel by component, cubic fuel-load factors, and
annual CO2 by power estimate.

Evaluation is built into the pipeline rather than limited to a final aggregate.
The `validate` stage checks identity integrity, active-hour coverage, voyage-leg
speed plausibility, operating-mode assignments, specification estimates, and
speed-smoothing sensitivity. Emissions, allocation, and impact tables retain
the full scenario identifier so that uncertainty in installed power, reference
speed, and speed smoothing is not hidden by selecting a single result.

The central limitation is vessel technical data: consistently sourced installed
main-engine power and reference speed are not available from one open source.
For that reason, the pilot reports scenario spread and should not be interpreted
as a fleet-scale estimate without better specification data.


## Acknowledgments and references

This project uses public data and methods from Global Fishing Watch, Marine
Regions/VLIZ, the International Maritime Organization, the Global Carbon
Budget, Equasis, Marine Traffic, and Selin et al. (2021). Please consult
[`docs/data_sources.md`](docs/data_sources.md) for the complete attribution,
source-version, and redistribution record.

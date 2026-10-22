# National allocation of international shipping CO2 emissions

An open-data, vessel-level reconstruction of the workflow in Selin et al.
(2021), *Mitigation of CO2 emissions from international shipping through
national allocation*. The project follows two vessels from hourly activity
observations to estimates of international-voyage CO2 emissions, then allocates
those emissions using flag, registered-owner, ISM-manager, and
commercial-manager-as-operator-proxy country rules.

The accompanying paper, *A Data Driven Analysis of Global Shipping Emissions*
(Arregui Gonzalez, 2026), is available in
[`docs/FinalPaper_ArantzazuArreguiGonzalez.pdf`](docs/FinalPaper_ArantzazuArreguiGonzalez.pdf).

## Research context

International shipping emissions are not assigned to national inventories, and
no allocation rule has been agreed. Selin et al. (2021) used 2015 commercial AIS
data and the World Register of Shipping to compare five allocation options:
flag, owner, operator, and manager country, plus the country of bunker-fuel
sale. They concluded that allocation to the owner country best meets
effectiveness and equity criteria. Because their inputs are proprietary, that
result has not been independently reproduced or updated.

This project asks whether the Selin et al. method can be rebuilt end to end from
openly accessible data. It identifies an open substitute for each commercial
input and measures where each substitution costs coverage or precision.

| Allocation option | Computed here | Country key used |
|---|---|---|
| Flag | Yes | Flag state |
| Owner | Yes | Equasis registered-owner address country |
| Manager | Yes | Equasis ISM-manager address country |
| Operator | Yes, by proxy | Equasis commercial-manager address country (Equasis has no operator field) |
| Bunker fuel | No | Fleet-scale only: national bunker sales cannot locate one ship's refuelling |

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

| Pilot vessel | IMO | Type | Flag | DWT (t) | Port calls / countries, 2017–2024 | Allocation countries after territory alignment |
|---|---:|---|---|---:|---|---|
| COSCO ITALY | 9516454 | Container | HKG | 156,610 | 389 / 17 | China under all four rules; Hong Kong is aligned with China |
| RCC AMERICA | 9277802 | Vehicle carrier, open registry | BHS | 21,182 | 588 / 64 | Bahamas, United Kingdom, and Greece |

RCC AMERICA was also queried under its former name, HOEGH AMERICA.

## Installation and setup

### Code and resources used

- **Language:** Python 3.11
- **Database and spatial processing:** DuckDB with its `spatial` extension
- **Activity data:** Global Fishing Watch APIs (a free non-commercial token is
  required for live requests)
- **Configuration:** YAML files in [`config/`](config/)
- **Documentation:** Section 3 of the
  [final paper](docs/FinalPaper_ArantzazuArreguiGonzalez.pdf) is the method
  reference, with formulas and constants in Appendices B and C;
  [`docs/data_sources.md`](docs/data_sources.md) records data provenance,
  required downloads, and handling.

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
inputs to `data/external/`, using the filenames listed under
[Required downloads](docs/data_sources.md#required-downloads) in
`docs/data_sources.md`. The vessel-specific Equasis records cannot be
bulk-downloaded: look them up manually on [Equasis](https://www.equasis.org/)
and record each value with its provenance in `config/vessel_specs.yaml`.

Run a local environment check without calling the API:

```powershell
python scripts/run_pipeline.py --stage check --offline
pytest
```

Remove `--offline` to also test a live GFW request.

API responses are cached in `data/raw/`, intermediate tables are written to
`data/interim/`, and derived outputs are written to `data/out/`. These generated
directories are ignored by Git, allowing partial runs to resume.

## Running the pipeline

Run every stage in order with:

```powershell
python scripts/run_pipeline.py --all
```

Each stage is restartable and can also be run alone with `--stage <name>`:

| Stage | What it does | Paper section |
|---|---|---|
| `check` | Validates configuration, DuckDB, and the GFW client | — |
| `select` | Optional: searches GFW for an additional contrast vessel | — |
| `activity` | GFW presence and port visits, hourly record, speed over ground, gap classification, voyage legs | 3.1, 3.2 |
| `specs` | Resolves the configured installed-power and reference-speed estimates | 3.3 |
| `fuel` | ECA and EU-to-EU tests and hourly fuel assignment | 3.5 |
| `emissions` | Operating modes, engine power demand, fuel consumption, and CO₂ | 3.4, 3.5 |
| `allocation` | International-voyage share and allocation to countries | 3.6 |
| `baselines` | Global Carbon Budget national baselines | 3.7 |
| `impacts` | Absolute and percentage additions to national carbon budgets | 3.7 |
| `validate` | Identity, coverage, leg-speed, mode, specification, and smoothing checks | 4 |

GFW permits one concurrent 4Wings report, so presence requests run one at a
time. Keep warnings from `validate` in the report; do not change parameters
after seeing results only to suppress them.

### Scenarios

Every result carries a scenario ID built from the cross join of the power
estimates in `run.power_estimates` and the odd, centred speed-smoothing windows
in `run.smoothing_windows` (both in `config/pilot.yaml`). Report the spread
across scenarios rather than choosing a single estimate or window.

### Adding a vessel

All vessel-specific data are configuration, not code:

| File | Contents |
|---|---|
| `config/pilot.yaml` | Dates, IMO list, exact GFW ship names, scenario axes, coverage and gap thresholds, paths, spatial layers, territory alignment |
| `config/vessel_specs.yaml` | Physical specifications, engine class, allocation-country keys, and the provenance of each parameter |
| `config/eexi_parameters.yaml` | MEPC.333(76) reference-speed and installed-power curves (paper Table C1) |
| `config/emission_factors.yaml` | Fourth IMO GHG Study mode, auxiliary, SFC, and CO₂-factor tables (paper Tables C5–C9) |

To add a ship, put its checksum-valid IMO number and every exact historical GFW
ship name in `pilot.yaml`, and its specification and allocation-key block in
`vessel_specs.yaml`. Each sourced or estimated parameter must record its value,
source, and method. The loader rejects invalid IMO numbers, missing names or
country keys, unknown EEXI categories, and even smoothing windows.

To search for an additional contrast vessel:

```powershell
python scripts/run_pipeline.py --stage select --shortlist 18
```

The selector samples GFW cargo vessels under six open registries (PAN, LBR,
MHL, MLT, BHS, CYP). It keeps candidates with a valid IMO number, a unique exact
name, observations in each sampled year, and calls in at least three countries
including an EU port. Confirming that the owner country differs from the flag
has to be done by hand in Equasis.

### Notebooks

After a successful run, re-execute the walkthrough notebook:

```powershell
python -m nbconvert --to notebook --execute --inplace notebooks/01_methodology_walkthrough.ipynb
```

## Data

### Source data

| Source | Role in the workflow |
|---|---|
| [Global Fishing Watch](https://globalfishingwatch.org/) | Hourly vessel presence, inferred port visits, and vessel identity data |
| [IMO Fourth Greenhouse Gas Study 2020](https://www.imo.org/en/ourwork/environment/pages/fourth-imogreenhousegasstudy2020.aspx) | Operating modes, auxiliary/boiler assumptions, fuel consumption, and CO2 factors |
| [Marine Regions / VLIZ](https://www.marineregions.org/) | EEZs, high-seas areas, emission-control areas, and land/EEZ layers |
| [Global Carbon Budget](https://globalcarbonbudget.org/) | National fossil-carbon-emissions baselines |
| Selin et al. (2021) supplementary material | Territory alignment used for replication |
| Equasis and Marine Traffic | Flag, tonnage, ship type, build year, dimensions, and company-role information (no installed power or reference speed) |
| Published estimation methods: MEPC.333(76) EEXI curves, Molland (2008) Admiralty relation, US EPA (2000) regressions, Cepowski and Chorab (2021) | Installed main-engine power and reference speed, which no open register provides |

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

See Section 3 and Appendix B of the
[final paper](docs/FinalPaper_ArantzazuArreguiGonzalez.pdf) for formulas,
assumptions, validation rules, and the treatment of coverage correction.

### Engine specification estimates

Open registers do not publish installed main-engine power (MCR) or reference
speed, so the pipeline carries several estimates instead of choosing one:

| Estimate | Method | Applies to |
|---|---|---|
| A | MEPC.333(76) average EEXI reference-speed and installed-power curves | All twelve EEXI ship categories |
| B | Admiralty relationship (Molland, 2008) with a coefficient back-calculated from 17 container ships (Charchalis, 2014) | Container ships only |
| C | Vessel-specific sourced MCR and reference speed | Reserved; raises an error until a sourced value is supplied |
| D | US EPA (2000) deadweight-to-MCR regression, paired with a speed from A or B | Types covered by the EPA regressions |

### Departures from Selin et al. (2021)

- **Speed:** GFW does not provide transmitted AIS speed, so speed over ground
  is derived from consecutive grid-cell centroids and smoothed over a centred
  window of 1, 3, 5 or 7 hours.
- **Gaps:** all non-inactive missing hours are interpolated (Selin et al.
  required at least one signal that day); gaps of seven days or more without a
  port call are treated as out of service and carry zero emissions.
- **International share:** set from port-to-port voyage legs (Fourth IMO GHG
  Study, Option 2) rather than the whole-ship 95%-in-one-EEZ rule, which is kept
  as a diagnostic.
- **Fuel:** the EU-to-EU distillate condition is tested on observed voyage legs
  instead of a geographical proxy.
- **At berth:** any hour at 1 knot or less inside a GFW port-visit interval is
  `at_berth`, regardless of anchorage distance. The distance-only rule of
  Table 16 placed only 1,143 of COSCO ITALY's 17,427 port-visit hours at berth.
- **Constants and baselines:** Fourth IMO GHG Study factors replace the Third
  Study, and the Global Carbon Budget 2025 replaces the 2018 edition.
- **Scope:** CO₂ only; no draught, weather, or hull-fouling correction, because
  neither loading nor sea state is observable in the activity data.

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
│   ├── data_sources.md          Source provenance, required downloads, substitutions, and handling
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

### Key findings

The reconstruction is feasible from open data, subject to the substitutions
below. Figures are eight-year (2017–2024) totals at a three-hour smoothing
window unless stated.

| Vessel | Estimate | V_ref (kn) | MCR (kW) | f_ref | Total CO₂ (t) | Ratio |
|---|---|---:|---:|---:|---:|---:|
| COSCO ITALY | A, EEXI curve fit | 25.55 | 67,912 | 0.75 | 473,408 | 1.00 |
| COSCO ITALY | B, Admiralty, calibrated | 22.62 | 78,258 | 1.00 | 897,942 | 1.90 |
| COSCO ITALY | D, EPA DWT power with Froude speed | 22.62 | 92,868 | 0.83 | 908,649 | 1.92 |
| RCC AMERICA | A, EEXI curve fit | 19.96 | 14,086 | 0.75 | 191,745 | 1.05 |
| RCC AMERICA | D, EPA DWT power with EEXI speed | 19.96 | 13,282 | 0.75 | 182,511 | 1.00 |

| Substitution | Measured effect |
|---|---|
| Ship specifications: published estimation routes replace a commercial register field | Factor of 1.92 on the container ship, 1.05 on the vehicle carrier |
| Activity data: aggregated hourly presence replaces transmitted AIS positions and speeds | Totals fall 19.3% (container) and 7.9% (vehicle) between raw and seven-hour smoothed speed |
| Gap treatment: in-service hours infilled, out-of-service hours excluded | 1.6% and 1.3% |
| Fuel and berth rules: observed legs and port-visit intervals | No material change to CO₂; about 16,000 hours reclassified |
| Carbon budget and scope: 2025 edition, two vessels, four options | No allocated quantity changes; no distributional result |

- **Installed power and reference speed are the dominant uncertainty.**
  Auxiliary-engine and boiler fuel is identical under every estimate, so the
  whole spread sits in the main engine.
- **Estimates rescale a series rather than reshape it.** The ratio between
  estimates stays between 1.85 and 1.93 in every year for the container ship,
  so ratios and rankings are more robust than absolute tonnages. Because the
  factor differs by hull, this robustness does not carry over automatically to
  allocations.
- **Coverage varies over time.** Interpolation supplies 11.9–13.1% of modelled
  hours in 2017–2021 against 1.1–2.0% in 2022–2024, on both hulls. COSCO ITALY
  has a 6,772-hour out-of-service gap from June 2019 to March 2020.
- **International share of CO₂ in 2024:** 97.5% (COSCO ITALY) and 93.6%
  (RCC AMERICA).

### Limitations and requirements for scaling

The emissions and allocation stages already loop over IMO numbers and are
parallel by vessel. Data acquisition is what does not scale yet:

- **Vessel discovery:** the exact ship-name filter used here does not scale. A
  fleet pull must sweep flag and vessel type at world extent and select on the
  returned IMO, about two orders of magnitude more data, so API quota becomes
  the binding constraint.
- **Registry data:** Equasis and Marine Traffic have no programmatic interface,
  so a fleet run needs a licensed register or a systematic imputation layer.
- **Bunker-fuel option:** needs no new engineering beyond fleet scale, using
  UNdata international marine-bunker flows.
- **Estimate C:** remains unpopulated; no sourced specification was found for
  either hull.
- **Unbounded bias:** draught, weather, and hull-condition effects are not
  captured by any scenario axis.

The paper's recommendation is to acquire installed power and reference speed
as observed fields before anything else; position-level AIS with transmitted
speed is the next most valuable addition. The two-vessel results demonstrate
the procedure and must not be read as evidence about which allocation option is
fairer or more effective.


## Acknowledgments and references

This project uses public data and methods from Global Fishing Watch, Marine
Regions/VLIZ, the International Maritime Organization, the Global Carbon
Budget, Equasis, Marine Traffic, and Selin et al. (2021). Please consult
[`docs/data_sources.md`](docs/data_sources.md) for the complete attribution,
source-version, and redistribution record. The full reference list is in the
paper.

To cite this work:

> Arregui Gonzalez, A. (2026). *A Data Driven Analysis of Global Shipping
> Emissions*. https://github.com/arantzazu-arregui/emissions-allocation

Main reference reconstructed:

> Selin, H., Zhang, Y., Dunn, R., Selin, N. E., & Lau, A. K. H. (2021).
> Mitigation of CO₂ emissions from international shipping through national
> allocation. *Environmental Research Letters, 16*(4), 045009.
> https://doi.org/10.1088/1748-9326/abec02

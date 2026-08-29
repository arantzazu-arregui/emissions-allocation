# Emissions allocation

An open-data reconstruction of the vessel-level workflow in Selin et al. (2021),
*Mitigation of CO2 emissions from international shipping through national
allocation*. The accompanying final paper is
[`docs/FinalPaper_ArantzazuArreguiGonzalez.pdf`](docs/FinalPaper_ArantzazuArreguiGonzalez.pdf).

The project demonstrates the full route from hourly vessel activity to national
allocation of international-voyage CO2. It is a reproducible two-vessel pilot,
not a global inventory and not evidence for preferring one allocation rule at
fleet scale.

## What the pilot does

For each configured vessel and year from 2017 through 2024, the pipeline:

1. obtains hourly Global Fishing Watch (GFW) presence observations and inferred
   port visits;
2. derives, smooths, and gap-treats speed over ground;
3. estimates engine specifications where open registers cannot supply them;
4. calculates hourly main-engine, auxiliary-engine, and boiler CO2 emissions;
5. retains emissions on international port-to-port voyages; and
6. allocates those emissions to flag, registered-owner, ISM-manager, and
   commercial-manager-as-operator-proxy countries, then compares each total
   with its Global Carbon Budget national baseline.

The stable key is the seven-digit IMO number. Ship names, MMSIs, flags, and GFW
`vesselId` values may change and are never used as the primary key.

| Pilot vessel | IMO | Purpose | Allocation countries after territory alignment |
|---|---:|---|---|
| COSCO ITALY | 9516454 | Large container vessel with co-located commercial roles | China under all four rules (Hong Kong flag merged into China) |
| RCC AMERICA | 9277802 | Open-registry vehicle carrier that exposes allocation differences | Bahamas, United Kingdom, and Greece |

The pilot establishes that public data can support the end-to-end workflow, but
also quantifies its main limitation: no consistently available open source
provides vessel-specific installed main-engine power and reference speed. For
COSCO ITALY, the specification-estimate scenarios yield a 1.92-fold spread in
total emissions. Obtain those two specifications before using the method for a
fleet-scale result.

## Run it

Use Python 3.11 and install the repository requirements. A free non-commercial
GFW token is required for live activity acquisition.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GFW_TOKEN` in `.env`; do not commit that file. Download the documented
external inputs into `data/external/` before spatial, baseline, and allocation
stages. `docs/data_sources.md` records the required datasets and their handling.

```powershell
# Validate configuration, DuckDB setup, and one live activity request.
python scripts/run_pipeline.py --stage check

# Run the configured workflow in order.
python scripts/run_pipeline.py --all

# Run an individual, restartable stage.
python scripts/run_pipeline.py --stage emissions

# Run the test suite.
pytest
```

API responses are cached under `data/raw/`; intermediate tables are written to
`data/interim/` and outputs to `data/out/`. These paths are ignored by Git, so
a partial run can resume without repeating completed requests.

The complete operational workflow, formulas, checks, input schemas,
stage-to-module mapping, and extension procedure are in
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Configuration and implementation

Vessel-specific values are data, not code:

- `config/pilot.yaml` defines the study window, vessels, exact GFW query names,
  scenario axes, spatial inputs, territory alignment, and validation anchors.
- `config/vessel_specs.yaml` holds Equasis-derived dimensions, role countries,
  and any sourced engine specifications with provenance.
- `config/eexi_parameters.yaml` holds the complete MEPC.333(76) coefficients.
- `config/emission_factors.yaml` holds the Fourth IMO GHG Study mode, fuel,
  specific-fuel-consumption, and emission-factor tables.

`scripts/run_pipeline.py` is the entry point. Python modules in
`src/emissions_allocation/` handle API access, parsing, physical calculations,
and validation. DuckDB performs spatial joins, range joins, window operations,
allocation, and reporting through readable SQL in
`src/emissions_allocation/sql/`.

To scale the pilot, add one checked IMO record and its exact query-name history
to `pilot.yaml`, add the corresponding technical and allocation data to
`vessel_specs.yaml`, obtain sourced installed power and reference speed where
possible, and rerun. Do not replace the IMO key with a name or GFW identifier.

## Scope and known limits

- The computed quantity is CO2 from international voyages only. Domestic-voyage
  emissions are retained for diagnostics but not allocated.
- The bunker-fuel-sales option is intentionally absent at vessel scale: national
  bunker statistics cannot identify where an individual vessel took fuel.
- Equasis has no operator field. The commercial manager is an explicit operator
  proxy, and company addresses are used as country keys.
- GFW presence stores hourly grid-cell centroids, not transmitted AIS SOG.
  Speed smoothing is therefore a reported sensitivity axis.
- GFW port calls are inferred events, not raw AIS port detections; their
  segmentation can differ from a commercial raw-AIS workflow.

## Repository map

```text
config/                    versioned model and vessel parameters
data/external/             read-only downloaded inputs
data/raw/, interim/, out/  cached API data, checkpoints, and derived outputs
docs/                      methodology, sources, and final paper
notebooks/                 executable walkthrough and result figures
scripts/run_pipeline.py    stage runner
src/emissions_allocation/  pipeline modules and DuckDB SQL
tests/                     unit, configuration, API-assertion, and model tests
```

## Sources and attribution

Activity and port calls: [Global Fishing Watch](https://globalfishingwatch.org/).
Spatial layers: Marine Regions / VLIZ (CC-BY 4.0). National baselines: Global
Carbon Budget 2025, Friedlingstein et al. The detailed source record, including
the Fourth IMO GHG Study and Selin et al. supplementary territory mapping, is
in [`docs/data_sources.md`](docs/data_sources.md).

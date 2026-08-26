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

Two vessels, 2017–2024, chosen to sit on opposite sides of the paper's central finding:

| | COSCO ITALY (9516454) | RCC AMERICA (9277802) |
|---|---|---|
| type | container, 156,610 DWT | vehicles carrier, 21,182 DWT |
| flag | Hong Kong | Bahamas |
| owner / manager / operator | China / China / China | Isle of Man / Greece / Isle of Man |
| **national budgets** | **1** (folded) | **3** |
| 8-year CO₂, w=3 | 642,683 t (A) / 969,081 t (B) | 256,011 t (A) |

**That contrast is the result.** Allocation choice is immaterial for the co-located majority
— 74% of ships in Selin et al., covering 61% of emissions — and decisive for open-registry
ships. Two hulls show it; one cannot.

The spread between power estimates is a **reported result, not an error**: no free source
supplies installed power or design speed, so no estimate is primary. Against EMSA-verified
emissions, estimate A understates vessel A by 36% while estimate B matches within 2%.

Country assignment follows Selin et al.'s supplementary Table 1. Its published country list
does not include Hong Kong, so the fixed replication map assigns Hong Kong to China.

## Start here

**[`notebooks/01_methodology_walkthrough.ipynb`](notebooks/01_methodology_walkthrough.ipynb)**
walks §0 to §8 with every equation sourced inline, and ships with its outputs. It reads the
cached tables, so it opens instantly.

## Terminology

| Term | Definition in this project |
|---|---|
| **Vessel** | One physical ship, identified throughout by its stable IMO number. A vessel name, MMSI, flag, or GFW `vesselId` can change and is not the primary key. |
| **Vessel-hour** | One hourly GFW presence observation for a vessel. It carries the position, derived speed, and activity flags used by the spatial, fuel, and emissions stages. |
| **Port call** | One GFW Events port-visit record, from arrival through departure. It includes anchorage timestamps, a port identifier, and the port country. |
| **Port country** | The ISO3 country code recorded by GFW for a port visit's `startAnchorage.flag`. It identifies the country of the port, not the vessel's flag or ownership country. |
| **Voyage leg** | The derived journey between consecutive port calls. It carries start/end ports, duration, distance, and the `is_eu_eu` indicator used by the fuel rule. |
| **Activity coverage** | Observed vessel-hours divided by expected in-service hours for a vessel and year. It is calculated before any gap treatment. |
| **EEZ** | Exclusive Economic Zone. A vessel-hour is assigned to a World EEZ v12 polygon where possible. Unmatched hours are classified as high seas. |
| **ECA** | Emission Control Area. MARPOL Annex VI Regulation 14 polygons identify hours subject to the ECA fuel rule. |
| **EU-to-EU leg** | A voyage leg whose consecutive start and end ports are both in EU countries. It is determined from port-call data, rather than inferred from EEZ positions. |
| **Scenario** | One combination of installed-power estimate and speed-smoothing window. Emissions, allocations, and impacts retain this key. |
| **Allocation option** | A responsibility rule that assigns annual vessel CO₂ to the flag, owner, ISM manager, or commercial-manager-as-operator-proxy country. |
| **Baseline** | A country's Global Carbon Budget territorial fossil CO₂ emissions, converted from MtC to MtCO₂, against which allocated emissions are compared. |

### Variable dictionary

| Variable | Definition in this project |
|---|---|
| **DWT (t)** | Deadweight tonnage in tonnes: the vessel's maximum carrying capacity, including cargo, fuel, stores, passengers, and crew. |
| **Flag** | The ISO3 country or territory of the vessel's registry. It is distinct from port, owner, manager, and operator countries. |
| **`elapsed_hours`** | Total calendar hours in a vessel-year within the study period. This is the coverage-table field; it is sometimes informally called lapsed hours. |
| **`inactive_hours`** | Hours classified as out of service, such as a confirmed lay-up. They are excluded from the active-coverage denominator. |
| **`observed_hours`** | Vessel-hours with an observed GFW position, rather than an interpolated position. |
| **`coverage_raw`** | `observed_hours / elapsed_hours`. This transparent coverage measure includes inactive hours in the denominator. |
| **`coverage_active`** | `observed_hours / (elapsed_hours - inactive_hours)`. This is the coverage measure used for the annual emissions correction. |
| **MDO/MGO hours** | Vessel-hours assigned to distillate marine diesel oil or marine gas oil by the fuel rule, for example within an ECA or on an EU-to-EU leg. |
| **HFO hours** | Vessel-hours assigned to residual heavy fuel oil by the fuel rule. |
| **`sog_raw`** | Raw speed over ground in knots, derived from the great-circle distance between consecutive hourly GFW positions divided by elapsed hours. It is not a speed field returned by the GFW API. |
| **`sog_w<n>`** | Scenario-specific centred moving-average speed over ground in knots, where `<n>` is the odd smoothing-window width in hours, such as `sog_w3`. It reduces the cubic-power bias created by quantised hourly positions. |
| **`operating_mode`** | The activity category assigned to a vessel-hour: `at_berth`, `anchored`, `manoeuvring`, `slow_transit`, or `normal_cruising`. It determines auxiliary and boiler demand. |
| **`sog`** | Speed over ground in knots carried into `emissions_hour`. It is the scenario's selected smoothed-speed column, such as `sog_w3`. |
| **`me_load`** | Main-engine load as a fraction of installed power, calculated from `sog` relative to the scenario's design speed. |
| **`w_me_kw`** | Main-engine power demand in kW for a vessel-hour. It is zero in modes without a running main engine and below the 7% MCR cutoff. |
| **`w_ae_kw`** | Auxiliary-engine power demand in kW for a vessel-hour, selected from IMO Table 17 by vessel type, size band, and operating mode. |
| **`w_bo_kw`** | Boiler power demand in kW for a vessel-hour, selected from IMO Table 17 by vessel type, size band, and operating mode. |
| **`fc_total_g`** | Total fuel consumption in grams for a vessel-hour: main-engine, auxiliary-engine, and boiler consumption combined. |
| **`co2_tonnes`** | CO₂ emissions in tonnes. At hourly grain it is calculated from `fc_total_g` and the assigned fuel emission factor; at annual grain it is the selected observed or coverage-corrected sum. |
| **`co2_tonnes_observed`** | Annual sum of hourly `co2_tonnes` over observed and modelled active hours, before coverage correction. |
| **`co2_tonnes_corrected`** | `co2_tonnes_observed / coverage_active`: the annual emissions estimate after correcting for missing active-hour observations. |
| **`dominant_eez_iso3`** | ISO3 code of the EEZ containing the most vessel-hours for a vessel in the domestic/international test. |
| **`dominant_eez_hours`** | Number of vessel-hours within `dominant_eez_iso3`. |
| **`hours_in_any_eez`** | Number of vessel-hours assigned to any EEZ in the domestic/international test. |
| **`active_hours_total`** | All non-inactive vessel-hours in the domestic/international test, including high-seas hours. |
| **`hours_disputed`** | Vessel-hours in a joint-regime or overlapping EEZ claim, flagged so the territory assignment is visible. |
| **`dominant_eez_share`** | `dominant_eez_hours / active_hours_total`. A value above 95% classifies the vessel as domestic. |
| **`is_domestic`** | `True` when `dominant_eez_share` exceeds the 95% domestic threshold. |
| **`is_international`** | `True` when the vessel is not domestic in the EEZ diagnostic. Allocation instead uses voyage-level port-country labels, so only emissions from international voyages enter the analysis. |
| **`delta_e_mt_min`** | Smallest national carbon-budget increment, in MtCO₂, across all configured scenarios for a country, year, and allocation option. |
| **`delta_e_mt_max`** | Largest national carbon-budget increment, in MtCO₂, across the same scenario set. |
| **`spread_ratio`** | `delta_e_mt_max / delta_e_mt_min`, showing the multiplicative spread across scenarios. |

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
tests/        307 tests
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

- **Estimate C** (sourced installed power and service speed) has no free source and raises.
  Vessel B uses EEXI A plus EPA-power/EEXI-speed D; both share the EEXI speed, so
  only installed-power uncertainty is bracketed.
- **Estimate B has no calibration for non-container hulls.** `C_adm` comes from 17 container
  ships; it raises for any other hull form rather than extrapolating.

## Attribution

AIS data from [Global Fishing Watch](https://globalfishingwatch.org/). Spatial layers from
[Marine Regions](https://www.marineregions.org/) (VLIZ), CC-BY 4.0. National baselines from
the [Global Carbon Budget](https://globalcarbonbudget.org/) — Friedlingstein et al. (2025).

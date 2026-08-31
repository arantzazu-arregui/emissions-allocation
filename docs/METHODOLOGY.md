# Methodology: open-data national allocation of shipping CO2

This is the implementation guide for Section 3 of the final paper,[A Data Driven Analysis of Global Shipping Emissions](FinalPaper_ArantzazuArreguiGonzalez.pdf).
It translates the paper's seven-step workflow into executable instructions and records the repository components required to run it.

The pipeline reconstructs the vessel-level approach of Selin et al. (2021) with public inputs. It calculates CO2 from engine power demand, activity time, and a fuel-specific emission factor, then allocates international-voyage emissions to countries. 

The pilot covers 2017-01-01 through 2024-12-31 for COSCO ITALY (IMO 9516454) and RCC AMERICA (IMO 9277802). It is a validated demonstration, not a fleet-level result.

The valid seven-digit IMO number is the immutable join key. Do not key tables, caches, or configuration on vessel name, MMSI, flag, or GFW vesselId: these can change, and a hull can have several GFW IDs.

## 0. Prepare the run

### 0.1 Install and obtain inputs

1. Create the Python 3.11 environment and install requirements.txt.
2. Put a GFW non-commercial token in local .env as GFW_TOKEN; never commit it.
3. Download the required external inputs into `data/external/` using the links
   below. Keep the filenames shown here: they are the paths configured by the
   pipeline. Treat this directory as read-only.

   | Destination and required filename | Download | Purpose |
   |---|---|---|
   | `marineregions/World_EEZ_v12_20231025_gpkg.zip` | [Marine Regions downloads: World EEZ v12, GeoPackage](https://www.marineregions.org/downloads.php) | EEZ point-in-polygon join. Select **GeoPackage** for World EEZ v12 (2023-10-25). |
   | `marineregions/World_High_Seas_v2_20241010_gpkg.zip` | [Marine Regions downloads: World High Seas v2, GeoPackage](https://www.marineregions.org/downloads.php) | High-seas reference layer. Select **GeoPackage** for World High Seas v2 (2024-10-10). |
   | `marineregions/eca_reg14_sox_pm.zip` | [Marine Regions ECA Regulation 14 dataset](https://doi.org/10.14284/397) | MARPOL Annex VI Regulation 14 ECA fuel assignment. Download the shapefile archive. |
   | `marineregions/EEZ_land_union_v4_202410.zip` | [Marine Regions Marine and Land Zones v4 dataset](https://doi.org/10.14284/698) | Coast-distance proxy. Download the `EEZ_land_union_v4_202410.zip` archive. |
   | `gcb/National_Fossil_Carbon_Emissions_2025_v0.3.xlsx` | [Global Carbon Budget 2025: National fossil carbon emissions](https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/) | Required territorial-emissions baseline. The live download may have a newer revision; use the configured v0.3 file for a reproducible rerun, or update the configuration and methodology together. |
   | `gcb/Global_Carbon_Budget_2025_v0.6.xlsx` | [Global Carbon Budget 2025: full workbook](https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/) | Companion workbook retained with the baseline release. |
   | `gcb/Global-Carbon-Budget-v2025-Dataset-Descriptions.pdf` | [Global Carbon Budget 2025: dataset descriptions](https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/) | Metadata for the two GCB workbooks. |
   | `imo/Fourth-IMO-GHG-Study-2020-Full-report-and-annexes_compressed.pdf` | [Fourth IMO GHG Study 2020: full report and annexes (PDF)](https://greenvoyage2050.imo.org/wp-content/uploads/2021/07/Fourth-IMO-GHG-Study-2020-Full-report-and-annexes_compressed.pdf) | Source report for the emission-factor configuration. |


   Also manually retrieve the vessel-specific Equasis/public-register records
   described in [Section 3](#3-acquire-ship-specifications), using
   [Equasis](https://www.equasis.org/), and record their values and provenance
   in `config/vessel_specs.yaml`. These records are not distributed as a
   reproducible bulk download.

   The following `data/external/` files are optional rather than required for
   the two-vessel allocation rerun:

   | Destination | Download | Use |
   |---|---|---|
   | `undata/UNdata_Export_*_FuelOil.zip` and `undata/UNdata_Export_*_GasOil.zip` | [UNdata Energy Statistics database](https://data.un.org/) | Not used by the pilot. These are inputs only for the future fleet-scale bunker-fuel allocation option. |
4. Keep API responses in data/raw/, transient tables in data/interim/, and
   derived results in data/out/. These directories are ignored by Git; the API
   cache enables restartable runs.
5. Validate configuration, DuckDB, and a live request before acquiring a run:

   ~~~powershell
   python scripts/run_pipeline.py --stage check
   pytest
   ~~~

Captured responses in data/sample/api/ and scripts in scripts/exploratory/
document GFW endpoint behaviour. They are provenance, not production stages.
GFW permits one concurrent 4Wings report, so presence requests are sequential.

### 0.2 Configure a fleet and sensitivity space

All vessel-specific data are configuration, not code.

| File | Contents |
|---|---|
| config/pilot.yaml | dates, IMO list, exact GFW ship names, scenario axes, GFW coverage/gap thresholds, paths, spatial layer names, territory alignment, validation anchors |
| config/vessel_specs.yaml | physical specifications, engine class, role-country keys, and parameter provenance |
| config/eexi_parameters.yaml | complete MEPC.333(76) speed/MCR curves and type aliases |
| config/emission_factors.yaml | Fourth IMO GHG Study mode, fuel, SFC, and CO2-factor tables |

For a new hull, add its valid IMO and every exact historical GFW query name to
pilot.yaml; add its specification and allocation-key block to vessel_specs.yaml.
A sourced or estimated parameter records value, source, and method. The
configuration loader rejects invalid IMOs, absent names/role keys, unknown EEXI
categories, and even smoothing windows.

The scenario space is the cross join of configured power estimates and odd,
centred SOG windows. Config.scenarios() creates a scenario ID that remains in
every emissions, allocation, and impacts table. Do not choose one estimate or
window without reporting the sensitivity.

To discover an additional contrast vessel:

~~~powershell
python scripts/run_pipeline.py --stage select --shortlist 18
~~~

The selector samples GFW cargo vessels under six open registries (PAN, LBR,
MHL, MLT, BHS, CYP). Retain candidates with a valid IMO, unique exact name,
observations in each sampled year, calls in at least three countries including
an EU port, and—after manual Equasis verification—an owner country different
from flag. The final registry check cannot be automated through a public API.

## 1. Ingest and preprocess AIS data (paper Section 3.1)

Run:

~~~powershell
python scripts/run_pipeline.py --stage activity
~~~

gfw.py and activity.py construct the hourly activity spine, coverage, port
calls, and voyage legs.

### 1.1 Acquire GFW presence and verify it

For every configured vessel and calendar year, request GFW 4Wings
public-global-presence:latest once using:

~~~text
spatial-resolution  = HIGH
temporal-resolution = HOURLY
group-by            = VESSEL_ID
date-range          = YYYY-01-01, YYYY+1-01-01
filters[0]          = shipname in (configured exact, case-sensitive names)
geojson             = world extent (-180,-85 to 180,85)
~~~

Presence cannot be reliably filtered by IMO, MMSI, or vesselId. Validate each
response before retention:

1. it is non-empty;
2. it contains exactly the configured, checksum-valid IMO;
3. credited observations are at least 10% of elapsed annual hours; and
4. credited hours do not exceed elapsed hours by more than 1%.

A misspelled ship name returns HTTP 200 with zero records, so these are required
integrity checks. Flag annual observed coverage below 95% as low confidence but
do not discard it automatically. Collapse multiple high-resolution cells in one
vessel-hour to an hours-weighted centroid.

Fetch GFW identity data to resolve every associated vesselId. Use those IDs only
to request port events; retain IMO as the database key. Cache raw requests under
data/raw/.

### 1.2 Create the hourly spine and derive SOG

Create a complete hourly record for the study period. Derive raw SOG from
consecutive observed centroid positions using the true elapsed time:

~~~text
a       = sin²(Δφ/2) + cos(φ1) cos(φ2) sin²(Δλ/2)
d       = 2 × 6371.0088 × asin(√a) km
SOG_raw = d / (1.852 × Δt) knots
~~~

GFW supplies cell centroids, not transmitted AIS SOG. Since power is
approximately proportional to the cube of speed, centroid jumps make a material
upward bias. Apply every configured centred moving average (normally
w = 1, 3, 5, 7) only within a contiguous underway segment. Do not smooth across
a port visit or out-of-service interval; leave port-visit SOG unsmoothed and
inactive SOG null.

For non-inactive missing hours, fill positions by nearest neighbour and
interpolate derived SOG linearly between bracketing observations (one-sided at a
record boundary). Preserve a flag for every filled value. This adopts Selin et
al.'s interpolation family but not its daily eligibility rule.

### 1.3 Classify long absences and measure coverage

Obtain port calls before deciding whether a missing run is inactive. A contiguous
gap at least run.inactivity_gap_days (seven days by default) is out-of-service
only if no GFW port call starts within it. Set inactive hours to zero emissions;
do not interpolate or coverage-correct them. If a port call falls inside a long
gap, raise an error because the presence pull is inconsistent. Short reception
gaps remain active and are infilled.

~~~text
coverage_raw    = observed_hours / elapsed_hours
coverage_active = observed_hours / (elapsed_hours - inactive_hours)
~~~

Only the active measure is eligible for the optional annual correction. An
unobserved hour is never automatically inactive. The separate 2012-to-present
observed-activity screen labels a vessel-year observed_active only when it has
the configured minimum observed hours across the configured minimum days. It is
an AIS-observation label, not registry status.

## 2. Consolidate ship movement (paper Section 3.2)

The activity stage and DuckDB SQL build the movement layer. Relevant SQL files
are 12_voyage_leg.sql, 20_eez_join.sql, 21_eca_join.sql,
22_distance_to_port.sql, and 23_distance_to_coast.sql.

1. Request GFW Events port visits for every resolved vesselId at confidence
   levels 3 and 4. Paginate with nextOffset, union all IDs, and deduplicate by
   event ID.
2. Keep each event's start/end timestamps, start/end anchorages, anchorage
   coordinates, port ISO3, atDock, confidence, duration, and EEZ MRGID. GFW
   events are inferred, not raw AIS truth.
3. Construct a voyage leg from the end of one call to the start of the next.
   Label it international when port countries differ, domestic when they agree,
   and unlabelled when either country is absent.
4. Mark a leg EU-to-EU when both ports are EU27; include the United Kingdom
   through 2020. This drives one branch of fuel assignment.
5. Intersect non-inactive vessel-hours with World EEZ v12 using sovereign ISO3.
   Treat unmatched hours as high seas and flag joint-regime/overlapping claims.
   This is diagnostic only: a vessel is domestic if more than 95% of all
   non-inactive hours, including high seas in the denominator, lie in one EEZ.

Allocation uses voyage legs, not EEZs. The hour-to-leg range join labels a
preceding international voyage through the destination port-call end, making
that stay part of the preceding voyage in the international-emissions split.

## 3. Acquire ship specifications (paper Section 3.3)

Run:

~~~powershell
python scripts/run_pipeline.py --stage specs
~~~

Use Equasis/public registry sources for flag, DWT, GT, dimensions, ship type,
build year, registered owner, ISM manager, and commercial manager. Enter them,
with provenance, in config/vessel_specs.yaml. Equasis has no public API,
operator field, installed power, or reference speed. Commercial manager is
therefore a clearly labelled operator proxy; the address country is the country
key.

Both pilot hulls use the slow-speed-diesel default absent better evidence. Where
the IMO table needs containership capacity, infer it from beam:

~~~text
beam_m = 3.27 × TEU^0.29
~~~

Resolve and retain all applicable estimates rather than declaring one primary.

| Estimate | Method | Restriction |
|---|---|---|
| A | MEPC.333(76) average EEXI speed and MCR curves | Container speed capacity is capped at 80,000 DWT; power capacity at 95,000 DWT |
| B | Admiralty relationship from Froude speed, displacement, and a Charchalis-calibrated C_adm | Container only; do not borrow its calibration for vehicle carriers |
| C | sourced vessel MCR, reference speed, and reference-load condition | preferred when supplied; raises while fields are missing |
| D | US EPA DWT-to-MCR regression paired with a stated reference speed | changes MCR only; does not independently bracket speed |

Keep MCR, reference speed, reference-load fraction, input values, range, source,
and method with every estimate. A uses 75% MCR at reference speed; D uses its
EPA service-load convention; B is calibrated at unit reference load and is best
read as an upper-bound route. The stage must raise for missing fields instead of
substituting plausible values.

## 4. Estimate hourly engine power demand (paper Section 3.4)

Run:

~~~powershell
python scripts/run_pipeline.py --stage emissions
~~~

emissions.py calculates the physical model; DuckDB supplies spatial/movement
attributes. For every scenario and vessel-hour, calculate:

~~~text
Load_i = min(1, f_ref × (SOG_w,i / V_ref)^3)
W_ME,i = MCR × Load_i
~~~

Set main-engine demand to zero below the 7% MCR cutoff and in at_berth or
anchored mode. Do not add draught, weather, or hull-fouling corrections: they
are not observable from the public activity data and must be stated as omitted.

Assign one ordered Fourth IMO GHG Study Table 16 operating mode: at_berth,
anchored, manoeuvring, slow_transit, or normal_cruising. Use scenario SOG,
main-engine load, distance to the vessel's own port-call anchorages, and coast
distance. Coast is derived from Marine and Land Zones v4 minus matching EEZ v12
polygons, so it represents the territorial-sea baseline rather than physical
shoreline; preserve this caveat.

The production configuration improves on Table 16's distance-only berth rule:
when a qualified GFW port-visit interval is active and SOG is at most 1 knot,
classify the hour as at_berth regardless of anchorage distance. This avoids
misclassifying berth positions with offshore inferred anchorages. Set
run.use_port_visit_intervals to false for the strict Table 16 sensitivity.

Use the emission_factors.yaml range lookup by ship type, size band, and mode for
auxiliary-engine and boiler demand. Slow transit and normal cruising use the sea
column. Below 150 kW MCR, both demands are zero; at 150--500 kW, auxiliary
demand is 5% MCR and boiler demand remains table-based; above 500 kW, use the
published values.

## 5. Calculate CO2 emissions (paper Section 3.5)

Run:

~~~powershell
python scripts/run_pipeline.py --stage fuel
python scripts/run_pipeline.py --stage emissions
~~~

21_eca_join.sql marks ECA hours and 30_fuel_assignment.sql assigns fuel. Assign
MDO/MGO when the engine is high-speed diesel, the hour lies in a MARPOL
Regulation 14 ECA, or it falls on an EU-to-EU leg; otherwise assign HFO. An hour
inside a port visit has no leg row, so it is tested only for ECA/high-speed
conditions. The 2017--2024 ECA input covers Baltic, North Sea, US Caribbean,
and North American areas. Add the Mediterranean ECA for later years.

The 2020 sulphur cap creates no CO2 branch: HFO and low-sulphur HFO have the
same Table 21 carbon factor. It would matter for a future SOx/PM inventory.

Apply the Fourth IMO GHG Study load correction to main-engine SFC only:

~~~text
SFC_ME,i = SFC_base × (0.455 × Load_i² - 0.710 × Load_i + 1.280)
FC_i = W_ME,i × SFC_ME,i + W_AE,i × SFC_AE + W_BO,i × SFC_BO
CO2_year = Σ(FC_i × EF_fuel / 1,000,000)
~~~

At hourly resolution the power terms are kWh, fuel is grams, and annual CO2 is
tonnes. Configuration contains the paper's Table 19/21 values: slow-speed
diesel SFC 175/165 g kWh⁻¹ (HFO/MDO), auxiliary 195/185, boiler 340/320, and
CO2 factors 3.114/3.206 g CO2 per g fuel. Auxiliary and boiler SFC are not load
corrected. CO2's low-load factor is 1.00 everywhere; do not apply one.

Retain observed and optionally coverage-corrected annual totals:

~~~text
CO2_corrected = CO2_observed / coverage_active
~~~

Never divide by raw calendar coverage when a long gap is out-of-service; that
would create emissions for a vessel not in service.

## 6. Allocate emissions to countries (paper Section 3.6)

Run:

~~~powershell
python scripts/run_pipeline.py --stage allocation
~~~

50_allocation.sql allocates international-voyage CO2 only. For every vessel,
year, and scenario:

1. assign emissions on labelled legs directly to domestic or international;
2. divide unlabelled emissions (before/after legs or missing country) according
   to the directly labelled emissions split, not the hour split; and
3. allocate the full international total under four options: flag, registered
   owner, ISM manager, and commercial-manager operator proxy.

Apply territory_alignment.merge_into from pilot.yaml before joining baselines.
The fixed Selin-replication rule maps Hong Kong to China and Isle of Man to the
United Kingdom. The loader requires every resulting country to have a Global
Carbon Budget baseline; never silently omit an unmatched territory.

Do not calculate the bunker-fuel-sales option for individual vessels. National
bunker-delivery data are aggregate and cannot locate a vessel's refuelling. A
full-fleet extension can calculate this fifth Selin option from UNdata
international marine-bunker flows and documented fuel factors.

## 7. Compute allocation impacts (paper Section 3.7)

Run:

~~~powershell
python scripts/run_pipeline.py --stage baselines
python scripts/run_pipeline.py --stage impacts
python scripts/run_pipeline.py --stage validate
~~~

baselines.py reads the Global Carbon Budget 2025 Territorial Emissions sheet
(header row index 11). Values are MtC; convert to MtCO2 with 3.664. National
values exclude bunker emissions and form the denominator. Raise for a missing
baseline rather than treating it as zero.

For allocated CO2 E_c,o in tonnes and baseline B_c in MtC, report the absolute
increment and:

~~~text
ΔE%_c = 100 × E_c,o / (10^6 × 3.664 × B_c)
~~~

70_impacts.sql and 71_impacts_by_region.sql produce country/year and regional
outputs. Rankings and concentration measures are useful pipeline checks but
have no substantive interpretation for two vessels.

## 8. Validate, inspect, and extend

Run the complete sequence:

~~~powershell
python scripts/run_pipeline.py --all
~~~

validate.py reports identity integrity, active-hour coverage, voyage-leg speed
plausibility, port-call/track agreement, fleet-envelope checks of power
estimates, and cubic SOG-smoothing sensitivity. Retain warnings in the report;
do not change parameters after seeing results merely to suppress them.

Inspect notebooks/01_methodology_walkthrough.ipynb. After a successful rerun:

~~~powershell
python notebooks/build_notebook.py
python -m nbconvert --to notebook --execute --inplace notebooks/01_methodology_walkthrough.ipynb
~~~

Scale by adding one well-provenanced IMO record at a time; retain query metadata,
API/layer versions, retrieval date, code commit, parameter sources, scenario
IDs, and validation outputs. Do not collapse installed-power/reference-speed or
GFW-SOG uncertainty into an unlabelled total. The final paper identifies these
open-data substitutions as the main limits to resolve before interpreting
fleet-level allocation results.

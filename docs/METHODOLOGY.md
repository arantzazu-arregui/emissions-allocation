# Methodology

**Project:** National allocation of international shipping CO2 emissions: a public-data, two-vessel implementation informed by Selin et al. (2021).

**Implementation status:** This document describes the pipeline implemented in `src/emissions_allocation/`, its SQL stages, and the checked configuration in `config/`. It is not a prospective specification. Configuration is the authoritative source for vessel-specific values and run switches.

**Study period:** 2017-01-01 through 2024-12-31. API date ranges have an inclusive start and exclusive end.

## 1. Scope and study design

The project is a reproducible two-vessel template for allocating **international-voyage CO2** to flag, registered-owner, ISM-manager, and operator-proxy countries. It demonstrates how allocation rules can agree for a co-located ship and diverge for an open-registry ship. It does not claim to reproduce fleet-level findings such as country rankings, concentration shares, or the share of the global fleet with co-located commercial roles.

| | Vessel A | Vessel B |
|---|---|---|
| Ship | COSCO ITALY, IMO 9516454 | RCC AMERICA, IMO 9277802 |
| Type | Container ship; 156,610 DWT | Vehicle carrier; 21,182 DWT |
| Flag | Hong Kong | Bahamas |
| Owner budget | China | United Kingdom (Isle of Man mapped to UK) |
| ISM-manager budget | China | Greece |
| Operator-proxy budget | China | United Kingdom (Isle of Man mapped to UK) |
| Role in the pilot | Co-located commercial roles | Open-registry contrast |

The bunker-fuel-sales allocation option is not calculated. National bunker-sales statistics cannot identify where either individual ship took fuel; it is a fleet-scale allocation construct.

The stable key is the seven-digit IMO number. Vessel name, MMSI, flag, and Global Fishing Watch (GFW) `vesselId` may change. All vessel-specific estimates and allocation keys are held in YAML, so extending the work is a loop over configured IMOs rather than a model rewrite.

Python handles API access, parsing, gap treatment, and the physical model. DuckDB handles spatial joins, range joins, window operations, aggregation, allocation, and reporting. SQL is retained in `src/emissions_allocation/sql/`.

## 2. Vessel selection and activity eligibility

### 2.1 Candidate selection

The selection stage searches GFW hourly presence at world extent on one mid-year day for each study year. It uses the supported GFW filters: the six open-registry flags `PAN`, `LBR`, `MHL`, `MLT`, `BHS`, and `CYP`, and GFW vessel type `cargo`. Candidate IMO numbers and exact ship names are read from the returned records.

A candidate must have:

- a valid IMO number;
- a name unique within the sampled candidate pool, because the GFW name filter is exact but not inherently unique;
- observations in every sampled study year;
- port calls in at least three countries and at least one EU port call; and
- for the second pilot vessel, an Equasis-verified registered-owner country different from the flag country.

The final ownership test is a manual Equasis step: Equasis does not provide the required public API. The shortlist is ranked by EU port calls and then all port calls. RCC AMERICA was selected through this process and is configured with both its current and historical query names (`RCC AMERICA`, `HOEGH AMERICA`).

### 2.2 GFW observed-activity screen

Separately from the fixed emissions period, the pipeline retrieves the available GFW presence archive from 2012 to four days before retrieval. A vessel-year is labelled `observed_active` when it has at least 24 observed presence-hours on at least three calendar days. Otherwise it is `unobserved`.

This is an AIS-observation label, not an IMO registry-status label. In particular, `unobserved` is never converted to inactive. Every study year must pass this screen before the vessel can enter the emissions workflow.

## 3. Activity data, speeds, port calls, and coverage

### 3.1 GFW activity acquisition and integrity checks

For each configured vessel and calendar year, the pipeline requests GFW 4Wings `public-global-presence:latest` with:

```text
spatial-resolution  = HIGH
temporal-resolution = HOURLY
group-by            = VESSEL_ID
date-range          = YYYY-01-01, YYYY+1-01-01
filters[0]          = shipname in (configured exact names)
geojson             = world extent
```

The request returns hourly cell-centroid positions with identity fields inline. GFW does not permit filtering this dataset by IMO, MMSI, or `vesselId`; the exact, case-sensitive `shipname` filter is used and the result is post-filtered by IMO.

Each annual pull must be non-empty, contain exactly the configured valid-checksum IMO, have at least 10% of elapsed-year observations, and not exceed elapsed hours by more than 1%. Coverage below 95% is retained but flagged as low confidence. Long missing runs are checked against a different GFW endpoint, as described below. These checks protect against GFW's silent empty response for a wrong ship name and against partially returned reports.

If more than one high-resolution cell is credited in one vessel-hour, positions are collapsed to an hours-weighted centroid. A normal hourly response has one position per vessel-hour.

### 3.2 Port visits and voyage legs

Port visits are downloaded from GFW Events for every `vesselId` associated with the configured IMO, using confidence levels 3 and 4. Results are paginated with `nextOffset`, unioned across IDs, and deduplicated by event ID. A port call records the start and end timestamp, start and end anchorages, port-country ISO3 code, anchorage coordinates, `atDock`, confidence, and EEZ MRGID.

Consecutive calls form a voyage leg. The previous call's end is the departure time and end anchorage; the current call's start is the arrival. A leg is international when its two port-country codes differ. The destination call belongs to the preceding leg for international-emissions attribution, so the label interval runs from departure through the destination call's end. For the fuel rule, a leg is EU-to-EU when both port countries are EU27, with the United Kingdom included through 2020.

### 3.3 Speed derivation and smoothing

Raw speed over ground is derived from consecutive observed centroid positions, using the true elapsed time between observations:

```text
a       = sin²(Δφ/2) + cos(φ1) cos(φ2) sin²(Δλ/2)
d       = 2R asin(√a),  R = 6371.0088 km
SOG_raw = d / (1.852 Δt)  [kn]
```

The GFW product supplies cell centroids rather than transmitted AIS SOG. Centroid quantisation produces a material cubic-power bias, so the pipeline carries centred moving-average windows of 1, 3, 5, and 7 hours. A window is applied only within a contiguous underway segment. It never crosses a port-visit interval or an out-of-service interval; port-visit speeds remain unsmoothed and inactive speeds are null.

### 3.4 Gap treatment and coverage

The pipeline creates a complete hourly spine. Missing positions are nearest-neighbour filled; `SOG_raw` is calculated first from observed pairs and then linearly interpolated over gaps. Every filled hour is marked `is_interpolated`.

A missing run of at least seven days is classified as out of service only when it contains no GFW port call. This is a cross-endpoint assertion: a long presence gap containing a port call raises an error rather than being treated as inactivity. Inactive hours remain in the spine for audit but receive no emissions and are excluded from the active coverage denominator.

```text
coverage_raw    = observed_hours / elapsed_hours
coverage_active = observed_hours / (elapsed_hours - inactive_hours)
```

The configured primary run uses interpolated active hours and sets `coverage_correction: false`. Its annual totals are not divided by coverage. Both `co2_tonnes_observed` and the counterfactual `co2_tonnes_corrected = co2_tonnes_observed / coverage_active` are retained in annual output. The code rejects a run that combines active-hour interpolation with coverage scaling.

An explicitly separate sensitivity branch, `imo2020_port_phase`, replaces only short non-inactive gaps with the observed mean SOG for a port-event phase (port, six-hour transition, or voyage). Its threshold is the median inter-port voyage duration bounded to 6--72 hours. It is exported separately and is not used by allocation or impacts.

## 4. Technical specifications and power scenarios

Equasis and public vessel registers supply vessel type, DWT, GT, build year, dimensions, flag, and company roles. No free source used here provides reliable installed main-engine power and reference speed for both ships. The IMO Fourth GHG Study's fallback regressions are not used because their published coefficients are symbolic and circular. Every estimated YAML parameter must carry a value, source, and method; missing parameters raise rather than receiving a default.

Both vessels are assigned slow-speed diesel (SSD), the Fourth IMO GHG Study default for an oil-propelled ship that cannot otherwise be classified from the public data.

### 4.1 Power and reference-speed estimates

Each scenario carries its own installed power `MCR`, reference speed `V_ref`, reference-load fraction `f_ref`, and speed exponent. No estimate is declared primary.

| Estimate | Implementation | Availability |
|---|---|---|
| A | MEPC.333(76) EEXI average curves for `V_ref` and `MCR` | Both vessels |
| B | Calibrated container-ship Admiralty relation with a Froude speed range | COSCO ITALY only |
| C | Vessel-specific sourced speed and MCR | Not configured; raises if selected |
| D | EPA DWT-to-rated-main-engine-power regression with a documented speed pairing | Both vessels |

**Estimate A.** The full 12-category EEXI parameter tables, aliases, capacity basis, and containership caps are stored in `config/eexi_parameters.yaml`:

```text
V_ref = A B^C
MCR   = D E^F
```

For containerships, `B = min(DWT, 80,000)` and `E = min(DWT, 95,000)`. Vessel A consequently uses the capped EEXI values 25.55 kn and 67,912 kW, not the uncapped extrapolation. Estimate A uses `f_ref = 0.75` and exponent 3.

This capped speed is still outside the configured container service-speed envelope of 6.0--24.5 kn, so the fleet-envelope check deliberately returns **FAIL** for Vessel A Estimate A on every validation run. Because `Load ∝ 1 / V_ref³` for fixed observed SOG, the high EEXI reference speed suppresses its inferred main-engine load.

**Estimate B.** This is available only for the container ship. It uses a Froude range of 0.19--0.21, `L_BP = 345 m`, two displacement conventions, and an Admiralty coefficient calibrated from Charchalis (2014) Table 1 (median 482; range 352--593):

```text
V   = Fn √(g L_BP) / 0.5144
MCR = Δ^(2/3) V³ / C_adm
```

The runnable point scenario uses the midpoint of the Froude-derived speeds (22.62 kn) and the arithmetic mean of the geometric and DWT-ratio displacement estimates (176,711 t and 195,763 t). It yields 78,258 kW; the endpoint combinations remain in the output metadata as a range diagnostic. Its reference-load fraction is provisionally 1.0 because the source table does not resolve the installed-MCR fraction. It is not applied to RCC AMERICA: no vehicle-carrier calibration is configured and the code deliberately raises rather than borrowing container parameters.

**Estimate D.** EPA (2000) estimates rated main-engine horsepower directly from DWT and converts hp to kW. For COSCO ITALY it uses the container relation `0.80 DWT - 749.4`, paired with the midpoint of its configured container Froude-speed range and `f_ref = 0.83`. This is an extrapolation beyond the regression's stated 20,000--70,000 DWT range. For RCC AMERICA it uses the pooled container/RoRo/auto-carrier/reefer relation `0.719 DWT + 2,581`, paired with Estimate A's EEXI vehicle-carrier speed and `f_ref = 0.75`. The latter changes power but does not independently bracket speed.

The active primary scenario set is 12 scenarios for Vessel A (A, B, D × four smoothing windows) and 8 for Vessel B (A, D × four windows). The generic run configuration contains A, B, and D, but each vessel may narrow it.

### 4.2 Table 17 size basis

Table 17 is selected by the size unit specified for the ship type. COSCO ITALY's TEU value is derived at runtime from beam using Cepowski and Chorab (2021):

```text
TEU = (beam_m / 3.27)^(1 / 0.29)
```

RCC AMERICA is range-joined on DWT. The table, including small-MCR overrides, is data in `config/emission_factors.yaml`.

## 5. Fuel, operating mode, and hourly CO2

### 5.1 Fuel assignment

Fuel is assigned at vessel-hour grain. An hour is assigned MDO/MGO when any of the following is true:

1. the configured main-engine type is high-speed diesel;
2. its position intersects a MARPOL Annex VI Regulation 14 ECA polygon; or
3. it lies on an EU-to-EU voyage leg.

Otherwise it is assigned HFO. Port-visit hours are not members of a voyage-leg interval for this fuel rule. The ECA layer contains the Baltic, North Sea, US Caribbean, and North American areas; the Mediterranean ECA is outside the 2017--2024 study period. The 2020 sulphur cap creates no CO2 date branch because HFO and low-sulphur HFO have the same CO2 factor in IMO Table 21.

Fuel-specific factors and post-2001 base SFC values come from the Fourth IMO GHG Study: SSD 175/165 g kWh⁻¹ for HFO/MDO; auxiliary engines 195/185; boilers 340/320. The CO2 factors are 3.114 g CO2/g fuel for HFO and 3.206 for MDO.

### 5.2 Operating mode

The pipeline implements IMO Table 16 as an ordered SQL decision matrix with `at_berth`, `anchored`, `manoeuvring`, `slow_transit`, and `normal_cruising`. It uses the inclusive speed bands `≤1`, `(1, 3]`, `(3, 5]`, and `>5 kn`, distance to a vessel-visited port, and distance to coast. In the `(3, 5]` and `>5 kn` bands, the Table 16 threshold is `me_load ≤ 0.65` for `slow_transit` and `me_load > 0.65` for `normal_cruising`, except where the higher-priority port/coast rules select `manoeuvring`. The 0.65 threshold is taken directly from IMO Fourth GHG Study 2020 Table 16 (printed p. 66; PDF p. 94).

Distance to port is the nearest start or end anchorage from the vessel's own GFW port-call history. Distance to coast is derived from Marine Regions Marine and Land Zones v4: each land-plus-EEZ polygon is differenced with the matching EEZ v12 polygon, producing 253 non-empty land polygons from 328 source features. The method measures to the territorial-sea baseline rather than a physical shoreline. The pipeline asserts geometry types and feature counts, and fails if the coastline layer cannot be loaded.

The Table 16 1--5 nm port column applies only to liquid tanker types. It is not applied to either pilot vessel.

The configured implementation makes one documented departure from the source table: at `SOG ≤ 1 kn`, a vessel-hour within a GFW port-visit interval is classified `at_berth` regardless of distance to the anchorage point. This is enabled by `use_port_visit_intervals: true`. It avoids treating a confirmed in-port stay as anchored merely because the inferred anchorage coordinate is offshore. `atDock` is preserved for diagnostics but is not used to split a whole port-visit interval.

### 5.3 Power, fuel consumption, and CO2

For each scenario, main-engine load and demand are:

```text
Load_i = min(1, f_ref (SOG_i / V_ref)^n)
W_ME,i = MCR Load_i
```

Main-engine demand is zero below 7% load and in `at_berth` and `anchored` modes. Load is calculated before mode assignment, resolving the Table 16/mode-power ordering: the matrix uses load only above 3 kn, where these two modes are not selected.

Auxiliary and boiler demand are obtained from the Table 17 range join by ship type, size band, and mode. `slow_transit` and `normal_cruising` use the Table 17 sea column. For Vessel A's 12,000--14,499 TEU band:

| Mode | Boiler (kW) | Auxiliary engine (kW) |
|---|---:|---:|
| At berth | 630 | 1,300 |
| Anchored | 630 | 1,800 |
| Manoeuvring | 630 | 3,250 |
| Sea | 0 | 2,050 |

The main-engine SFC is load corrected with IMO equation (10):

```text
SFC_ME,i = SFC_base (0.455 Load_i² - 0.710 Load_i + 1.280)
```

Auxiliary and boiler SFC values are not load corrected. With `Δt = 1 hour`:

```text
FC_i      = (W_ME,i SFC_ME,i + W_AE,i SFC_AE + W_BO,i SFC_BO) Δt
E_CO2,i   = FC_i EF_f / 10^6  [tonnes]
E_ship,y  = Σ_i E_CO2,i
```

The low-load factor is not applied: Table 20 gives CO2 an LLF of 1.00 at every load. Inactive hours do not appear in `emissions_hour`.

## 6. International-emissions attribution and national allocation

The allocation numerator is voyage-based international CO2, not all vessel CO2. A leg between different port countries is international. The label covers the sea passage and its destination port call. Per vessel-year and scenario, CO2 from hours before the first complete leg, after the final complete leg, or otherwise without a valid country label is apportioned by the labelled **CO2** share that is international:

```text
international CO2 = direct international CO2
                  + unlabelled CO2 × direct international CO2
                    / (direct international CO2 + direct domestic CO2)
```

This is a GFW-event implementation of Fourth IMO GHG Study Option 2. It does not reproduce that study's raw-AIS port-detection algorithm.

An EEZ domestic/international classification is also produced as a diagnostic. It intersects active hours with World EEZ v12 and labels a vessel domestic only when more than 95% of all active hours, including high-seas hours in the denominator, lie in one sovereign EEZ. Unmatched hours are high seas. Where claims overlap, one country is selected deterministically by lowest MRGID and the hour is flagged `is_disputed`; these diagnostics do not change voyage-based allocation.

For each option, the model assigns every vessel-year's international total to the configured key country:

```text
E_c,option = Σ_ships E_ship,international × 1[key_option(ship) = c]
```

The four options are flag, registered owner, ISM manager, and commercial-manager-as-operator-proxy. Equasis reports addresses, not necessarily country of incorporation, and has no operator field; the address-derived country and proxy are explicitly recorded in `vessel_specs.yaml`. GFW `registryOwners.flag` is not used for ownership because it mirrors the ship flag in the investigated case.

Territory alignment is applied to the Global Carbon Budget join key, not by changing the vessel's observed flag or company location. The fixed replication map includes Hong Kong → China and Isle of Man → United Kingdom, following Selin et al.'s supplementary country treatment.

## 7. Baselines and impacts

National baselines come from Global Carbon Budget 2025, `National_Fossil_Carbon_Emissions_2025_v0.3.xlsx`, sheet `Territorial Emissions`, with header row index 11. The wide table is reshaped to country-year and converted from MtC to MtCO2:

```text
B_c [Mt CO2] = B_c [Mt C] × 3.664
```

National columns exclude bunker fuels. The workbook's `International Shipping` column is retained only as a fleet-scale sanity check, not as a model input. Allocation joins the configured GCB country name; a missing baseline is an error rather than a null or zero denominator.

For every option, year, and scenario:

```text
ΔE_c  = allocated_CO2_c [Mt CO2]
ΔE%_c = 100 ΔE_c / B_c
```

Outputs also contain within-option rank and share of allocated emissions, plus GCB-region aggregates including OECD, EU27, and Kyoto Protocol Annex B. Country memberships are many-to-many, so regional totals must never be summed across groups.

Ranks, top-20 concentration, and fleet shares are calculated to exercise the scalable SQL pathway but are not interpreted at two vessels. The meaningful pilot outputs are annual CO2, allocation-rule contrasts, regional contrasts, and scenario spread.

## 8. Outputs, sensitivity, and validation

### 8.1 Key outputs

| Output | Grain / purpose |
|---|---|
| `vessel_hour_<imo>.parquet` | Complete hourly activity spine, speeds, interpolation and inactivity flags |
| `port_call_<imo>.parquet` | GFW event calls and anchorage data |
| `voyage_leg_<imo>.parquet` | Consecutive port pairs and international/EU-to-EU labels |
| `coverage_<imo>.parquet` | Raw and active coverage by year |
| `fuel_assignment_<imo>.parquet` | Fuel assignment by hour |
| `emissions_hour_<imo>.parquet` | Hourly physical model, keyed by scenario |
| `emissions_year_<imo>.parquet` | Annual ship emissions, observed and counterfactual corrected totals |
| `gfw_observed_activity_<imo>.parquet` | Archive-wide GFW observed-active/unobserved screen by vessel-year |
| `imo2020_sog_sensitivity_<imo>.parquet` | Audit of the non-primary port-phase SOG sensitivity |
| `emissions_hour_imo2020_port_phase_<imo>.parquet` / `emissions_year_imo2020_port_phase_<imo>.parquet` | Non-primary gap-treatment sensitivity outputs |
| `baseline.parquet` | Global Carbon Budget national baselines for the study period |
| `international_emissions_year.parquet` | International-voyage totals and boundary-allocation diagnostics |
| `allocation.parquet` / `impacts.parquet` | Country allocation and carbon-budget increments |
| `impacts_by_region.parquet` | Allocation impacts aggregated to GCB regions |
| `scenario_spread.parquet` | Min, median, max, and multiplicative impact spread across scenarios |

### 8.2 Sensitivity treatment

The primary uncertainty surface is the full cross-product of each vessel's allowed power scenarios and smoothing windows. Scenario IDs retain both dimensions, for example `A_w3`. Scenario spread is reported as the minimum, median, maximum, and multiplicative range in `ΔE` and `ΔE%`; estimates are not averaged into a preferred value.

The adapted `imo2020_port_phase` SOG-infill branch is a non-primary diagnostic. Other uncertainties are documented but not numerically propagated: Equasis address-to-country inference, commercial-manager operator proxy, public-data engine assignment, reference-speed/power estimates, centroid SOG as a proxy for speed through water, derived coastal baseline, weather, fouling, draught, and auxiliary-load variation.

### 8.3 Validation and QA

The validation stage returns PASS, WARN, FAIL, or PENDING rather than silently skipping unavailable checks.

| Check | Implemented test |
|---|---|
| Identity integrity | One configured IMO in every activity series; checksum validation at acquisition |
| Hour conservation | Observed hours divided by active hours, with low-coverage years flagged |
| Smoothing | `mean(v³) / mean(v)³` for each window over active hours |
| Leg speeds | Great-circle port-to-port distance divided by leg duration; implausible anchorage artefacts diagnosed |
| Port-call agreement | Stationary/maneuvering mode hours compared with GFW port-visit durations |
| Fleet speed envelope | Vessel A A fails at 25.55 kn against the configured 6.0--24.5 kn container envelope; vehicle-carrier estimates are unassessed because no vehicle envelope is configured |
## 9. Limitations and unresolved inputs

- Estimate C remains unavailable for both vessels. A vessel-specific sourced MCR, reference speed, and reference condition would be the strongest power validation input.
- Vessel A Estimate A has a documented fleet-envelope FAIL: its 25.55 kn EEXI reference speed exceeds the configured 24.5 kn container maximum and is retained as a failing scenario, not corrected post hoc.
- Selected GFW products expose processed hourly centroids, not transmitted SOG, navigational status, draught, weather, hull condition, or a vessel-specific speed-power curve.
- Long no-presence/no-port-call intervals are treated as out of service for the emissions model, while the broader GFW observed-activity screen deliberately does not infer registry status from AIS absence.
- GFW port events are inferred events and can split or aggregate physical port stays differently from the raw-AIS port-detection algorithm in the Fourth IMO GHG Study.
- The coast-distance layer measures to a derived territorial-sea baseline. Natural Earth shorelines would be closer to the source study's coastline input.
- Overlapping and joint EEZ claims are flagged but use a deterministic technical assignment for the diagnostic only.
- Two vessels cannot support fleet-level rankings, concentration measures, or causal claims about national responsibility.

## Sources

- Selin, H., Zhang, Y., Dunn, R., Selin, N. E., and Lau, A. K. H. (2021). *Mitigation of CO2 emissions from international shipping through national allocation*. Environmental Research Letters 16, 045009. https://doi.org/10.1088/1748-9326/abec02
- International Maritime Organization (2020). *Fourth IMO Greenhouse Gas Study 2020*: Tables 16, 17, 19, 20, and 21; equations 10 and 11.
- International Maritime Organization (2021). *2021 Guidelines on the method of calculation of the attained Energy Efficiency Existing Ship Index (EEXI)*, Resolution MEPC.333(76), paragraph 2.2.3.5 and Appendix. https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.333(76).pdf
- Charchalis, A. (2014). *Determination of main dimensions and estimation of propulsion power of a ship*. Journal of KONES 21(2), 39--44.
- Cepowski, T., and Chorab, P. (2021). *Determination of design formulas for container ships at the preliminary design stage*. Ocean Engineering 238, 109727.
- United States Environmental Protection Agency (2000). *Analysis of Commercial Marine Vessels Emissions and Fuel Consumption Data*, EPA420-R-00-002.
- Global Fishing Watch APIs v3: 4Wings presence, Events, Vessels, and Insights.
- Flanders Marine Institute / Marine Regions: World EEZ v12, Marine and Land Zones v4, and MARPOL Annex VI ECA layers.
- Friedlingstein et al. (2025). *Global Carbon Budget 2025*: National Fossil Carbon Emissions v2025.
- Equasis: vessel registry, ownership, and management records.

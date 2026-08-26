# Data requirements for a single-vessel replication of Selin et al. (2021)

**Repository reviewed:** `C:\Users\arant\dev\emissions-allocation`
**Reviewed:** 16 August 2026
**Purpose:** establish, before any pipeline code is written, exactly which datasets, fields, and
granularities are required to take *one* ship from raw AIS presence to an allocated CO₂ figure —
and to make every step generalisable to the full ~44,000-ship international fleet.

**Pilot design decisions (agreed):**

| Decision | Choice |
|---|---|
| Vessel selection | Reproducible script (`select_vessel.py`), criteria-based, not hand-picked |
| Study period | 2017-01-01 → 2024-12-31 (full GFW coverage era; 8 years, 70,128 vessel-hours max) |
| Origin/destination | GFW `PORT_VISIT` events + EEZ transit sequence from Marine Regions |

---

## 1. Audit of the existing repository

### 1.1 What is there

| Component | State | Assessment |
|---|---|---|
| `scripts/fetch_presence.py` | Working | Two request patterns against 4Wings `/v3/4wings/report`: (a) `ENTIRE` × `FLAG` × `LOW` for regional flag shares; (b) `DAILY` × `VESSEL_ID` × `HIGH` for per-vessel "pseudo-tracks". Regions supplied as GeoJSON polygons or EEZ region ids. Clean separation of request-building and I/O. |
| `scripts/analyze_presence.py` | Working | DuckDB over Parquet globs, camelCase→snake_case view, six queries → CSV. Good pattern to keep. |
| `scripts/make_sample.py` | Working, **output invalid** | Attempts a 7-way speed-bin split plus Vessels API identity lookups. See §2.1. |
| `data/raw/presence_english_channel_2024-01-01_2024-01-31.parquet` | 40,471 rows | Columns: `date, flag, hours, lat, lon, vesselIDs`. Grid-cell aggregate; no vessel identity. |
| `data/raw/tracks_us_socal_2024-01-01_2024-01-31.parquet` | 38,247 rows, 1,544 vessels | Columns include `vesselId, imo, mmsi, callsign, shipName, flag, vesselType, geartype, lat, lon, hours, date, entryTimestamp, exitTimestamp, firstTransmissionDate, lastTransmissionDate`. |
| `data/sample/gfw_data_sample.xlsx` | Present | 4 sheets; `SpeedProfiles` is not usable (§2.1). |
| `docs/data_sources.md` | Thin | Documents the public source datasets and their handling requirements. |
| `config/`, `src/`, `notebooks/`, `tests/` | Scaffolding only | `src/emissions_allocation/` contains only a docstring. All configuration is hard-coded at the top of `fetch_presence.py`. |

### 1.2 The single most useful thing already established

The `DAILY × VESSEL_ID × HIGH` request returns **vessel identity inline** — `imo`, `mmsi`,
`callsign`, `shipName`, `flag`, `vesselType`, plus first/last transmission dates — on every
grid-cell record. That means steps 1 and 2 of the paper's method (acquire AIS activity,
identify unique ships with valid IMO numbers) are satisfied by a single endpoint, with no
separate identity join required. This is a materially better position than the paper's own
AIS-static-message reconstruction.

---

## 2. Findings that change the plan

### 2.1 The speed filter is silently not being applied — **blocking**

In `data/sample/gfw_data_sample.xlsx`, sheet `SpeedProfiles`, every one of the 50 vessels has
**identical hours in all seven speed bins** (144 h in each of `<2`, `2-4`, `4-6`, `6-10`,
`10-15`, `15-25`, `>25`; total 1,008 h over a 7-day window). A vessel cannot spend the same
number of hours in every speed band, and 1,008 h exceeds the 168 h the week contains. The script
contains a guard that prints a warning for exactly this condition, so the warning either fired
and was not acted on, or the sort ordering masked it.

The API *does* support the field. GFW documents `speed` as a filterable field on
`public-global-presence:latest` (alongside `flag`, `vessel_type`, `vessel-groups`), and the
`gfwr` reference manual lists the same seven bins. So the capability is real and the syntax in
`make_sample.py` is what is wrong — most likely the `AND` composition inside a single
`filters[0]` string. **Speed is the entire input to the emissions model**, so this must be
resolved before anything else. Test matrix in §6.1.

### 2.2 You cannot request a global region — **architectural**

`region` is a required parameter for presence reports; there is no worldwide option, and the
existing script always supplies a bounding-box GeoJSON. Following one ship across eight years of
ocean therefore cannot be a single global query. Two candidate resolutions, in preference order:

1. **World-extent user polygon + vessel filter.** Submit a `-180,-90 → 180,90` GeoJSON with a
   filter restricting the report to the pilot vessel. If GFW accepts it, the response is bounded
   by the vessel (≤ 8,760 records/year), not by the polygon. Untested — this is validation test
   V2 in §6.1. If it works, the whole pull is 7 bins × 8 years = 56 requests.
2. **Event-guided regional pulls.** Query `PORT_VISIT` events first (globally, vessel-filtered,
   no region constraint), derive the sequence of EEZs and ocean basins the voyages cross, then
   issue presence requests only for those regions and only for the relevant date windows. More
   requests, but it degrades gracefully and it is the pattern the full-fleet study will need
   anyway.

Note that `vessel_id` is documented as a filter field for `public-global-fishing-effort` but
**not** for `public-global-presence`, whose documented filters are `flag`, `vessel_type`,
`speed`, `vessel-groups`. If a direct vessel filter is rejected, the fallback is
`flag = <pilot flag> AND vessel_type in ("cargo")`, group by `VESSEL_ID`, and post-filter in
DuckDB — workable for one ship, wasteful for a fleet.

### 2.3 High seas are not covered by EEZ polygons — **structural gap**

An international voyage spends most of its hours outside any EEZ. Region sources are limited to
EEZ / MPA / RFMO / user shapefile, none of which tile the open ocean. If resolution 1 in §2.2
fails, you need an explicit high-seas geometry: Marine Regions publishes **World High Seas v2**
(2024-10-10) and **IHO Sea Areas v3**, either of which can supply ocean-basin polygons. A coarse
30° × 30° global tile grid (72 tiles) is the crude alternative.

### 2.4 The current activity ranking selects the wrong ships — **method**

`data/out/top_vessels.csv` returns 20 vessels each with exactly 720.0 hours — the arithmetic
maximum for a 30-day January — and `VesselIdentity` shows 8 of 10 lookups with
`registry_records = 0` and no IMO number. Ranking by presence hours inside a coastal bounding
box selects small domestic craft that never leave the box (harbour boats, patrol and passenger
launches), which are precisely the ships Selin et al. exclude. Vessel selection must filter on
`imo IS NOT NULL` **and** `vesselType IN ('CARGO','TANKER','CARRIER')` **and** a
geographic-spread criterion, before ranking on anything.

### 2.5 Daily temporal resolution destroys the speed signal — **method**

`hours` is returned as an integer per cell per day. A vessel-day therefore collapses to a set of
cells with no ordering and no within-day timing, which cannot yield a speed. The pilot must use
`temporal-resolution = HOURLY` with `spatial-resolution = HIGH`, giving one record per
vessel-hour-cell — an ordered position series at ~0.01° (≈1.1 km) precision.

---

## 3. Target pipeline for the pilot

```
A. Select vessel        → one IMO-registered cargo/tanker, active 2017–2024
B. Vessel identity      → IMO, MMSI, flag, type, registry history
C. Hourly presence      → ordered vessel-hour positions, global, 2017–2024
D. Speed                → (i) derived from position deltas  (ii) GFW 7-bin hours [cross-check]
E. Origin / destination → PORT_VISIT event sequence → voyage legs
F. Spatial context      → EEZ (domestic/international test) + ECA (fuel switch)
G. Technical specs      → installed ME power, design speed, DWT/GT, aux/boiler
H. Emissions            → hourly power × emission factor → ship-year CO₂
I. Allocation           → flag / owner / operator / manager / bunker, vs national baselines
```

Stages A–F are AIS/geospatial and fully public. Stage G is the weak link (§5.1). Stages H–I are
arithmetic over the preceding outputs plus published coefficients.

---

## 4. Data requirements, stage by stage

### A–D. Ship activity and speed — Global Fishing Watch

| Item | Specification |
|---|---|
| **Source** | GFW APIs v3, `https://gateway.api.globalfishingwatch.org/v3/` |
| **Dataset** | `public-global-presence:latest` (4Wings report), `public-global-vessel-identity` (Vessels API) |
| **Endpoint** | `POST /v3/4wings/report`; `GET /v3/vessels/search`, `GET /v3/vessels/{id}` |
| **Auth** | Free non-commercial bearer token; already provisioned as `GFW_TOKEN` |
| **Request params** | `spatial-resolution=HIGH`, `temporal-resolution=HOURLY`, `group-by=VESSEL_ID`, `date-range=<year>`, `format=JSON`, `filters[0]=<vessel + speed-bin expression>` |
| **Fields needed** | `vesselId, imo, mmsi, callsign, shipName, flag, vesselType, lat, lon, hours, date, entryTimestamp, exitTimestamp, firstTransmissionDate, lastTransmissionDate` |
| **Granularity** | One record per vessel × hour × 0.01° cell |
| **Volume (pilot)** | ≤ 70,128 vessel-hours over 8 years; ~56 requests if §2.2 resolution 1 holds, several hundred if not |
| **Quota** | ~50,000 requests/day, **1 concurrent report** (HTTP 429 otherwise); results retained 30 min → requires sequential execution with per-(year × bin) checkpointing |
| **Status** | Have token and working request code. **Need:** speed-filter fix (§2.1), hourly resolution switch, global/vessel-scoped request pattern |
| **Known limits** | Observed hours only (gaps where no satellite/terrestrial reception); speed available only as 7 bins; cell centroids, not true positions; no ordered track object — ordering must be reconstructed from timestamps |

**Derived speed (D-i).** With hourly records, SOG for hour *t* = haversine distance between
consecutive cell centroids ÷ 1 h. Centroid quantisation at 0.01° introduces ≈ ±0.6 kn on a
one-hour step — about 4% at a 15 kn cruise, which is acceptable and must be stated as a method
caveat. This is the pilot's primary speed series because it is continuous, unlike the bins.

**Binned speed (D-ii).** Seven filtered requests per period return hours-per-bin. Used as an
independent cross-check on the derived series and as the fallback method that scales to the full
fleet (7 requests per region-year instead of a per-vessel track). Bin edges 10–15 and 15–25 kn
straddle typical design speeds, so a bin-edge sensitivity test is required.

**AIS draught is unavailable.** The Fourth IMO GHG Study's AIS-reported and
voyage-specific draught workflow requires raw, timestamped static AIS messages. The
selected GFW presence, port-visit, and vessel-identity datasets do not provide a
draught field, so no draught resampling, infilling, reporting-quality flag, or
voyage-median calculation is possible. The project retains a fixed registry design
draught only for its Admiralty/displacement power-estimation sensitivity; it does not
estimate cargo or use draught as a vessel-hour emissions-model input. A future
cargo-intensity extension would require raw AIS draught observations and an
authoritative design-draught source.

**Coverage correction.** Observed hours < elapsed hours wherever reception fails. Correct as
`hours_corrected = hours_observed ÷ coverage`, with coverage from the GFW Insights/coverage
metric (available from 2017-01-01 — this is why the study period starts in 2017). Selin et al.
interpolated 32% of hours in 2015; this is a shared limitation, not a new one.

### E. Origin and destination — GFW Events API

| Item | Specification |
|---|---|
| **Endpoint** | `GET /v3/events` with `vessels[0]=<vesselId>`, `datasets[0]=<port-visit events dataset>`, `start-date`, `end-date`, `confidences=3,4` |
| **Event type** | `PORT_VISIT` — confirmed available for all vessel types including `CARGO` and `BUNKER_OR_TANKER`; confidence 1 is not downloadable |
| **Fields needed** | event id, `start`, `end`, `position` (lat/lon), port/anchorage identifier, port country, `regions` block (EEZ, RFMO, FAO) |
| **Derivation** | Sort events by `start`; each consecutive pair (port *n* → port *n+1*) is one voyage leg with an origin, a destination, a departure time and an arrival time |
| **Granularity** | One record per port call; a busy container ship generates roughly 40–120 calls/year |
| **Status** | Implemented from GFW `PORT_VISIT` events; consecutive calls form voyage legs and supply the primary international-emissions attribution. |
| **Verified fields** | `port_visit.startAnchorage.flag` supplies the start-port ISO3; `endAnchorage.flag` supplies the departure ISO3 for the next leg. `topDestination`, event timestamps, confidence, and `atDock` are retained for QA. |

Voyage legs supply the primary international-emissions attribution: a leg between ports in two
different countries is international, and the destination call inherits that label. This follows
Fourth IMO GHG Study Option 2, but uses GFW's inferred port visits rather than reproducing its
raw-AIS stop detection. The paper's ">95% of signals in one EEZ" test is retained only as a
vessel-level cross-check.

### F. Spatial context — Marine Regions (VLIZ)

| Product | Version | Use | Format |
|---|---|---|---|
| **World EEZ** | v12 (2023-10-25, 122 MB) | Assign each vessel-hour to an EEZ; domestic/international test; per-country hour attribution | GeoPackage, Shapefile, KML |
| **World High Seas** | v2 (2024-10-10, 6.85 MB) | Tile open-ocean legs when EEZ polygons do not apply (§2.3) | GeoPackage, Shapefile, KML |
| **ECAs — SOx and PM (MARPOL Annex VI reg. 14)** | current | Fuel-switch rule: inside ECA → MGO/MDO, outside → HFO | Shapefile |
| **ECAs — NOx** | current | Not needed for CO₂; download for completeness | Shapefile |
| **Marine and Land Zones** | v4 (2024-10-10) | Optional: resolve port coordinates to a country when a port call sits inside a river mouth or lies outside the EEZ layer | Shapefile, GeoPackage |

License CC-BY 4.0, attribution to VLIZ / Marine Regions required. **Caveat:** the ECA shapefile
predates the Mediterranean SOx ECA (in force May 2025) — irrelevant for a study period ending in
2024, but must be added if the horizon is later extended. Verify against MEPC.1/Circ.778/Rev.4.

GFW's own `region-id` filtering uses Marine Regions EEZ v11 (2019). Mixing v11 region ids with
locally-applied v12 polygons will produce small discrepancies in disputed and joint-regime areas.
Pick one and state it. Joint regimes and overlapping claims carry `iso3 = null` with sovereignty
in `isoSov1/2/3` — you need an explicit rule (exclude from the domestic test, or split hours
between claimants).

### G. Technical specifications — **the gap**

| Parameter | Needed for | Public source |
|---|---|---|
| Installed main-engine power (kW) | Cubic power law | **Not in GFW. Not in Equasis.** Categorical regression on ship type × DWT — the paper's own fallback (Zhang et al. 2019); IMO Fourth GHG Study tables as the free equivalent |
| Design speed (kn) | Cubic power law denominator | Same — estimated from type × size class |
| DWT / GT | Drives both regressions above | Equasis (free account, per-IMO lookup) |
| Ship type, year built | Regression class selection | GFW `vesselType` + Equasis |
| Auxiliary engine and boiler load by operating mode | Aux/boiler emissions | IMO Fourth GHG Study default tables by ship class × mode |

Selin et al. state their engine power demand is "based on engines' maximum power, ships'
real-time speed, and ships' design speed," taken from the World Register of Shipping (IHS
Maritime & Trade) — a paid commercial registry, which their data-availability statement confirms
is available only for purchase. **No free global equivalent exists.** They already fell back to
categorical regression (Zhang et al. 2019) for ships absent from WRS; this replication applies
that fallback to *every* ship. Every engine parameter here is therefore estimated rather than
observed. For a single pilot
vessel this is manageable — specs go into a small `config/vessel_specs.yaml` with a documented
provenance field per parameter — but it must be reported as a first-order uncertainty, and the
template should make the specs a swappable input so a PhD researcher with IHS access can drop in
real values without touching the model code.

### H. Emission factors — IMO GHG Studies

Free PDFs from the IMO. Required constants: CO₂ per tonne fuel (HFO 3.114, MGO/MDO 3.206
tCO₂/t), specific fuel oil consumption by engine type and load, auxiliary/boiler demand by ship
class and operating mode, and load-factor adjustments at low load. Fuel assignment follows the
paper: MGO/MDO for high-speed main engines, inside ECAs, and on voyages between EU ports; HFO
otherwise. "Between EU ports" is directly determinable from the port-visit sequence in stage E —
an improvement on the EEZ proxy that gridded-only data would have forced.

### I. Allocation inputs

| Option | Source | Fields | Status |
|---|---|---|---|
| Flag | GFW `flag` (registry-derived), cross-checked against Equasis | ISO3 | Have. **Never** use MMSI MID-prefix flag |
| Owner | Equasis "registered owner" country | Country | Manual per-IMO lookup; scripted for the fleet |
| Operator | **No free source.** Equasis "commercial manager" used as proxy | Country | Definitional mismatch — partially collapses the operator and manager options. Must be caveated |
| Manager | Equasis "ISM manager" country | Country | Have access |
| Bunker fuel | UNdata Energy Statistics: Fuel oil (`cmID:RF`) + Gas oil/diesel oil (`cmID:DL`), transaction "International marine bunkers" | Country × year × tonnes | Convert to CO₂ locally: t × 3.114 (HFO) / 3.206 (MGO). Differs definitionally from the paper's IEA source |
| National baselines | Global Carbon Budget 2025, **"National fossil carbon emissions v2025"** (XLSX) | Territorial CO₂ by country × year | Denominator for ΔE%. Requires manual merging of overseas territories into parent countries, alignment to the UNFCCC party list, and an EU27 aggregate. Cite Friedlingstein et al. (2025, ESSD) |

Because GCB 2025 is a newer vintage than the paper's GCB 2018, percentage increases will not
reproduce the published figures exactly. That is expected and should be framed as a
robustness result, not an error.

---

## 5. Gap register

| # | Gap | Severity | Resolution |
|---|---|---|---|
| G1 | Speed filter returns identical hours in all bins | **Blocking** | Test matrix V1 (§6.1) before any production pull |
| G2 | No global region; single-vessel global track not directly expressible | **Blocking** | Test V2; fall back to event-guided regional pulls (§2.2) |
| G3 | No free source of installed engine power or design speed | **High** | IMO Fourth GHG Study regressions on type × DWT; specs as swappable config; report as first-order uncertainty |
| G4 | No free operator-country field | **High** | Use Equasis commercial manager as proxy; caveat that operator and manager options partly collapse |
| G5 | High seas not covered by EEZ region source | Medium | Marine Regions World High Seas v2 / IHO Sea Areas v3 as user shapefiles |
| G6 | Observed-only hours; reception gaps | Medium | Coverage-metric scaling (2017+); validate against elapsed time between port calls |
| G7 | Speed available only in 7 bins; cell-centroid positions | Medium | Derived-speed series as primary; bin-edge sensitivity analysis |
| G8 | Port-visit response schema unverified | Medium | Test V4; one live call resolves it |
| G9 | GFW uses EEZ v11, downloads are v12 | Low | Choose one version and document; handle `iso3 = null` joint regimes explicitly |
---

## 6. Validation before the production pull

### 6.1 Tests to run first (cheap, one API call each)

| # | Test | Passes if |
|---|---|---|
| V1 | Speed filter syntax: try `filters[0]='speed in ("10-15")'` alone; `filters[0]='vessel_type in ("cargo") AND speed in ("10-15")'`; and separate `filters[0]`/`filters[1]` params — on a small region, one day | Hours differ across bins, and the 7 bins sum to the unfiltered total |
| V2 | World-extent GeoJSON with a vessel-scoped filter, one month, `HOURLY` | Response returns and contains only the pilot vessel |
| V3 | Vessel filter on presence: does `vessel_id in ('<id>')` work, or only `flag`/`vessel_type`/`vessel-groups`? | Determines the request architecture (§2.2) |
| V4 | One `PORT_VISIT` events call for a known cargo vessel | Response includes port identifier, port country, start and end |
| V5 | Hour conservation: for one transoceanic leg, observed hours vs elapsed time between departure and arrival | Ratio near 1 after coverage correction; quantifies the gap problem |
| V6 | IMO coverage: what fraction of `CARGO`/`TANKER` records carry a valid IMO? | Establishes whether the full-fleet study universe is reachable |

### 6.2 Validation built into the pilot

- Sum of binned hours = derived-track hours (internal consistency of D-i vs D-ii)
- Port-call sequence implies plausible average leg speeds (great-circle distance ÷ leg duration)
- Bin-edge sensitivity: recompute with bin lower edge, midpoint, and upper edge

---

## 7. Proposed repository changes

The current design hard-codes analysis settings at the top of `fetch_presence.py`. For a template
that other researchers extend from one vessel to thousands, configuration must be data, not code.

```
config/
  pilot.yaml              study period, vessel selection criteria, output paths
  vessel_specs.yaml       per-IMO engine/hull parameters + provenance per field
  emission_factors.yaml   IMO GHG Study constants, fuel assignment rules
src/emissions_allocation/
  gfw.py                  authenticated client: retry, 429 back-off, checkpointing
  presence.py             hourly vessel-hour pulls + speed-bin pulls
  events.py               port visits → voyage legs
  geo.py                  EEZ / ECA / high-seas spatial joins
  speed.py                derived SOG, bin reconciliation, coverage correction
  emissions.py            power law, aux/boiler, fuel assignment, CO₂ integration
  allocation.py           5 allocation options vs GCB baselines
scripts/
  00_validate_api.py      the six tests in §6.1 — run this first
  01_select_vessel.py     criteria-based pilot selection
  02_fetch_vessel.py      presence + events for the selected vessel
  03_build_voyages.py     legs, origin/destination, international/domestic
  04_estimate_emissions.py
  05_allocate.py
data/interim/             checkpoints, one file per (year × speed bin)
```

Everything vessel-specific lives in `config/pilot.yaml`; scaling to *n* vessels becomes a loop
over an IMO list rather than an edit to the model code. That is the property that makes this a
template rather than a one-off.

---

## 8. Decisions still open

1. **Speed method as published result.** Derived-from-position (continuous, higher resolution,
   does not scale) versus 7-bin hours (coarse, scales to the full fleet). Recommendation: publish
   the derived series for the pilot, report the binned series alongside it, and quantify the
   difference — that quantification is itself the contribution that lets PhD researchers use the
   cheap method on the full fleet with a known error bar.
2. **Engine specification source.** Confirm the pilot's DWT and type from Equasis, then decide
   whether design speed and installed power come from the IMO regression or from a manually
   sourced public figure for that specific ship. The latter is more accurate for the pilot but
   less honest about what the scaled method can deliver.
3. **Coverage correction on or off by default.** It changes the headline CO₂ number. Recommend
   reporting both, with the correction as the primary figure.
4. **Whether I run the API calls.** The token lives on your machine and this session cannot make
   authenticated outbound calls. The validation script (`00_validate_api.py`) is written to be
   run locally by you, and its printed output is what unblocks §6.1.

---

## Sources

- [Global Fishing Watch API documentation](https://globalfishingwatch.org/our-apis/documentation)
- [gfwr reference manual (r-universe)](https://globalfishingwatch.r-universe.dev/gfwr/gfwr.pdf) — event types, vessel-type coverage, presence speed bins
- [gfwr package site](https://globalfishingwatch.github.io/gfwr/)
- [Marine Regions downloads](https://www.marineregions.org/downloads.php) — EEZ v12, High Seas v2, IHO Sea Areas v3, MARPOL ECA shapefiles
- [UNdata — Fuel oil (cmID:RF)](http://data.un.org/Data.aspx?d=EDATA&f=cmID%3ARF)
- [UNdata — Gas oil/diesel oil (cmID:DL)](http://data.un.org/Data.aspx?d=EDATA&f=cmID%3ADL)
- [Global Carbon Budget 2025 data hub](https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/) — National fossil carbon emissions v2025
- Zhang, Y., Fung, J.C.H., Chan, J.W.M., Lau, A.K.H. (2019). The significance of incorporating unidentified vessels into AIS-based ship emission inventory. *Atmospheric Environment* — the categorical-regression method the paper uses for missing engine specifications
- Selin, H., Zhang, Y., Dunn, R., Selin, N.E., Lau, A.K.H. (2021). Mitigation of CO₂ emissions from international shipping through national allocation. *Environmental Research Letters* 16, 045009. [doi:10.1088/1748-9326/abec02](https://doi.org/10.1088/1748-9326/abec02)

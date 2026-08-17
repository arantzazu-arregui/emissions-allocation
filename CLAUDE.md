# CLAUDE.md — working notes for this repository

Read this before writing code. It records what has already been tested against the live APIs and what has been ruled out. Most items here cost a round-trip to discover; re-deriving them wastes time and, worse, several of the failure modes are **silent**.

The specification is `docs/METHODOLOGY.md`. This file is the operational companion: constraints, gotchas, and decisions already made.

---

## Project in one paragraph

A two-vessel replication of Selin et al. (2021), *Mitigation of CO₂ emissions from international shipping through national allocation* (Environ. Res. Lett. 16, 045009), built entirely from public data. AIS activity from Global Fishing Watch → hourly speed → CO₂ via the IMO Fourth GHG Study bottom-up model → allocated to countries under flag / owner / manager / operator rules → compared against Global Carbon Budget national baselines. Study period 2017-01-01 to 2024-12-31. The deliverable is a **template** other researchers extend from two vessels to the full fleet, so vessel-specific values belong in config, never in code.

---

## Environment

- Python venv at `.venv`; `requirements.txt` at root. **geopandas/shapely/pyogrio are NOT used** — DuckDB's `spatial` extension reads GeoPackage and shapefile through GDAL, including from inside a zip via `/vsizip/`, which keeps every point-in-polygon and distance operation in `src/emissions_allocation/sql/*.sql`. The extension downloads itself on first use.
- GFW token in `.env` as `GFW_TOKEN`. Free non-commercial licence. **Never commit it.**
- Quota: ~50,000 requests/day, **1 concurrent 4Wings report** (429 otherwise), report results retained 30 minutes.
- `data/external/` holds downloaded source data — treat as **read-only inputs**, never regenerate.
- `data/raw/` is API output, `data/interim/` checkpoints, `data/out/` derived tables. All git-ignored.
- `data/sample/api/round1..4/` holds raw API responses captured during investigation. **Read these instead of re-querying** when you need to know a response shape.

---

## Architecture decisions already made

| Decision | Choice | Why |
|---|---|---|
| Engine | DuckDB | already in requirements; reads Parquet and GeoPackage directly; spatial extension; zero setup |
| Python vs SQL | *lookups, joins, windows and aggregations are SQL; physical formulas are Python* | keeps the model readable and the analysis expressive |
| Power/design speed | three estimates carried in parallel, **no primary** | no free source exists; see below |
| Hong Kong | computed **both** ways, reported as sensitivity | decisive for vessel A and not obviously correct either way |
| Sensitivity scope | full ranges on power/speed and smoothing window; qualitative elsewhere | those two dominate |
| Vessel key | **IMO number**, always | `vesselId` fragments — one hull has several |

Scenario space is a SQL `CROSS JOIN`: 3 power estimates × 2 HK treatments × N smoothing windows. Every downstream table is scenario-keyed.

---

## GFW API — what works

**Presence, the only way to scope to one vessel:**

```
POST /v3/4wings/report
  spatial-resolution  = HIGH
  temporal-resolution = HOURLY
  group-by            = VESSEL_ID
  datasets[0]         = public-global-presence:latest
  date-range          = 2024-01-01,2025-01-01
  format              = JSON
  filters[0]          = shipname in ("COSCO ITALY")
  body                = { "geojson": <world polygon -180,-85 → 180,85> }
```

- A **world-extent polygon is accepted.** One request per calendar year.
- **A full year returns in ~44 s, in one response, with no pagination.** 8,782 records for 2024. The whole 8-year pull is ~6 minutes per vessel.
- Records carry identity inline: `imo, mmsi, callsign, shipName, flag, vesselType, lat, lon, hours, date` where `date` is a real hourly timestamp (`"2024-01-15 13:00"`). No identity join needed.
- Working filter fields: **`flag`, `vessel_type`, `speed`, `shipname`** — nothing else.
- `shipname` matching is **exact and case-sensitive**. `"cosco italy"` → 0 rows. `"COSCO"` → 0 rows. `in (...)` accepts a list, so a rename history fits one request.

**Events (port visits):**

```
GET /v3/events?vessels[0]=<vesselId>&datasets[0]=public-global-port-visits-events:latest
   &start-date=&end-date=&confidences[0]=3&confidences[1]=4&limit=100&offset=<n>
```

Paginates properly: `total` is a real count here, `nextOffset` advances and ends `null`. Port country is `port_visit.startAnchorage.flag` (ISO3); port id like `"usa-longbeach"`; `topDestination` is the human name; `regions.eez` gives MRGIDs that join cleanly to EEZ v12.

**Coverage (Insights):**

```
POST /v3/insights/vessels
  { "includes":["COVERAGE"], "startDate":"...", "endDate":"...",
    "vessels":[{"vesselId":"...","datasetId":"public-global-vessel-identity:latest"}] }
```

Returns 201 with `coverage{blocks, blocksWithPositions, percentage}`. **`startDate` must be ≥ 2020-01-01.** Prefer computing coverage yourself as `observed hours ÷ elapsed hours` — it works for the whole period and matched 99.98% for 2024 where Insights said 99.897% on a different unit.

**Identity:** `GET /v3/vessels/search?query=<IMO>&datasets[0]=public-global-vessel-identity:latest` then `GET /v3/vessels/{id}?dataset=...`. **Omit `includes`** — it 422s.

---

## GFW API — what does NOT work (do not retry)

| Attempt | Result |
|---|---|
| `filters[0]='vessel_id in (...)'` on presence | 422, `Function with name VESSEL_UPPER does not exist`. Every syntax: `=`, `in`, single and double quotes. |
| `filters[0]='imo in (...)'`, `mmsi`, `ssvid` | 422, `Unknown expression or table expression identifier`. **The columns do not exist in query scope. You cannot filter by IMO.** |
| `POST /v3/vessel-groups` | **403 Not authorized by permissions.** A free token cannot create vessel groups. Three body shapes tried. |
| Separate `filters[0]` and `filters[1]` params | **The second condition is silently dropped.** 526 rows vs 24 for the same intended filter. Always compose one string with `AND`. |
| `GET /v3/insights`, `/v3/vessels/{id}/coverage` | 404 |
| `includes[0]=OWNERSHIP` on `/v3/vessels/{id}` | 422 |

---

## Silent failure modes — assert against these

**A wrong ship name returns HTTP 200 with zero records and no error** (`entries: [{"public-global-presence:v4.0": null}]`). This is the single most dangerous behaviour in the whole API. Every presence pull must assert:

1. result is **non-empty**;
2. exactly **one distinct IMO** is present, and it is the expected one;
3. observed hours are within tolerance of elapsed hours for the period.

Also:

- **`total` in a 4Wings report response is NOT a record count.** A zero-row response still reports `total: 1`. It counts datasets. Never paginate on it. (In `/v3/events` it *is* a real count — the two endpoints differ.)
- **HTTP status and the body's `statusCode` disagree** — HTTP 422 with `"statusCode": 503` inside. Retry logic must read both. These failures reproduce across runs 33 minutes apart, so they are server behaviour, not transient.
- Pagination is inconsistent per endpoint: `/vessel-groups` **requires** `offset` with `limit`; `/vessels/search` and `/insights/vessels` **reject** `offset`.
- `combinedSourcesInfo` shows **multiple `vesselId`s per hull**. Never key on `vesselId`.

---

## Research dead ends — already checked, do not repeat

- **IMO Fourth GHG Study equations (3) and (4)** (design speed, installed power) publish only symbolic coefficients `b₁…b₄`, and are **mutually circular** — speed needs power, power needs speed. Unusable. The equations are typeset as images, so `pdftotext` shows blank lines where they should be; render the page instead (`pdftoppm -f 62 -l 62`).
- **Cepowski & Chorab (2021)** does *not* estimate power. It estimates hull dimensions from DWT, TEU and speed — speed is an **input**. Useful only for the TEU inversion and the fleet envelope.
- **Charchalis (2014)** publishes no power formula either; its Figure 5 is an image at 1,300 TEU only. Its **Table 1 of 17 ships is usable to calibrate the Admiralty coefficient** — that is what it's for here.
- **GFW `registryOwners.flag`** returns the vessel's own flag (HKG), contradicting Equasis's Shanghai owner address. It appears to echo the ship flag, not owner domicile. **Do not use it for the owner allocation.**
- **THETIS-MRV is EU-scope** — validation only, never an input. The project allocates globally.
- **Bunker-fuel allocation is not computable** at two vessels. It needs national fuel-sales statistics. Out of scope by construction, not by omission.

**When a source is a PDF and a number seems missing, render the page before concluding it isn't there.** Equations and tables are frequently images that text extraction drops silently. This has happened three times in this project.

---

## External data — layouts already verified

| File | Gotcha |
|---|---|
| `World_EEZ_v12_20231025_gpkg.zip` → layer `eez_v12` | 285 features, EPSG:4326. `ISO_SOV1` is **never null**. `POL_TYPE`: 229 `200NM`, 21 `Joint regime`, 35 `Overlapping claim`. GFW event MRGIDs join cleanly (8456 → US EEZ). |
| `World_High_Seas_v2_20241010_gpkg.zip` | a single global multipolygon; use as not-in-any-EEZ mask |
| `eca_reg14_sox_pm.zip` | 6 polygons: Baltic, North Sea, US Caribbean, North American 1–3. **No Mediterranean** (in force May 2025, after our period). |
| `National_Fossil_Carbon_Emissions_2025_v0.3.xlsx` | sheet **Territorial Emissions**, **header at row index 11** (0-based). Wide: rows = years 1850–2024, cols = 232 countries. **Units are MtC — multiply by 3.664 for MtCO₂.** National values **exclude** bunkers; World includes them. Has an `International Shipping` column (2024: 170.15 MtC = 623 MtCO₂) for cross-checking. `Regions` sheet has ready-made KP Annex B / OECD / EU27 groupings. |
| UNdata bunker CSVs | unit is **"Metric tons, thousand"** — ×1000 before applying emission factors. A **footnote block follows a blank line** at the end of the file; the parser must stop there. |
| `Fourth-IMO-GHG-Study-2020-Full-report-and-annexes_compressed.pdf` | 524 pp. Extract with `pdftotext -layout`. Table 10 engine shares p.48; Table 16 mode matrix p.66; Table 17 aux/boiler p.68; Table 19 SFC p.70; Table 20 LLF p.74; Table 21 EF p.74. Equations 10 and 11 are on **PDF page 99** and are images. |

---

## Pinned values

**Emission factors (IMO Table 21):** HFO 3.114 · LSHFO 1.0% 3.114 · MDO/MGO 3.206 · LNG 2.750 · Methanol 1.375 g CO₂/g fuel.

**SFC base, 2001+ (Table 19):** slow-speed diesel HFO 175 / MDO 165 · auxiliary engines 195 / 185 · boilers 340 / 320 g/kWh.

**Load correction (equation 10):** `SFC_ME,i = SFC_base · (0.455·Load_i² − 0.710·Load_i + 1.280)`. Minimises at Load = 0.78, matching the stated ~80% MCR optimum. Auxiliaries and boilers are **not** load-corrected (equation 11).

**Low load:** CO₂'s LLF is **1.00 at every load** — no adjustment. But the main engine reports **nothing below 7% MCR**.

**IMO 2020 sulphur cap is immaterial to CO₂** — low-sulphur HFO carries the same carbon content and factor as HFO. Scrubber fitting affects SOx only.

**EEXI curve fit (MEPC.333(76)):** `V = A·DWT^B`, `P_ME = C·DWT^D`. Containership A=3.240 B=0.183 C=0.504 D=1.030. Validates well for bulk carriers and tankers across their range and for container ships up to ~50,000 DWT; **fails at the top of the container range** — returns 28.92 kn for vessel A, above the 24.5 kn maximum of the modern container fleet.

**Admiralty coefficient**, calibrated on Charchalis Table 1 (17 ships): median **482**, mean 478, sd 57, range 352–593.

**Derived speed is biased, not just noisy.** Cell-centroid quantisation makes hour-to-hour speeds oscillate (3.36 → 21.63 kn while cruising ~15). Since power ∝ v³ this does not average out: `mean(v³)` = 2,654 vs `(mean v)³` = 1,588, a **1.67× overestimate**, falling to 1.19× with a 3-hour centred moving average. Smoothing is mandatory and its window is a sensitivity parameter.

---

## Vessel A — COSCO ITALY (fixed)

IMO **9516454** · MMSI 477845600 · callsign VRNE4 · flag HKG · 154,592 GT / **156,610 DWT** · Container Ship · built 2014 · LOA 365.90 m · beam 51.20 m · draught 16.0 m · **≈13,200 TEU** (derived by inverting `B = 3.27·TEU^0.29`) · vesselId `103dbbea9-9221-5f81-a00f-657a515745bb`.

Allocation keys — registered owner COSCO ITALY SHIPPING LTD (Shanghai, company IMO 4178111); ISM manager SHANGHAI OCEAN SHIPPING CO LTD (5193283); commercial manager COSCO SHIPPING LINES CO LTD (1043944). **All three resolve to China; flag is Hong Kong.**

389 port calls 2017–2024, all confidence 4, continuous every year. 35 EU calls (NLD 9, DEU 10, BEL 9, GRC 6, POL 1) → **THETIS-MRV validation is available**. 27 consecutive EU→EU legs → the EU-port MGO rule genuinely fires. North Sea and North American ECAs both apply. **24.9% of the period is spent in port** (17,427 h), so auxiliary and boiler demand is a large share of total emissions, not a correction term.

## Vessel B — not yet selected

Must be an open-registry hull (PAN/LBR/MHL/MLT/BHS/CYP) with owner country ≠ flag country, mid-range DWT, **distinctive ship name**, continuous coverage, ≥1 EU port call. Selection procedure in `docs/METHODOLOGY.md` §0.2. Vessel A alone gives a degenerate allocation — all four options resolve to China — so vessel B is what makes the comparison interpretable.

---

## Open items

1. **Vessel B selection** — run METHODOLOGY §0.2 (steps 1–4 automated), then Equasis for criterion 7, its allocation keys and DWT. Until then the allocation is degenerate.
2. ~~Selin supplementary Table 1~~ — **CLOSED**. `data/external/paper/erlabec02supp2.xls` carries 199 countries with **no Hong Kong row**, and no Taiwan or Macao: aligned to the UNFCCC party list. The paper **folds Hong Kong into China**, so `folded_into_china` is the replication-faithful treatment. Both still computed.
3. ~~Coastline layer~~ — **CLOSED**. Marine and Land Zones v4 ships as `EEZ_land_union` (land merged with EEZ), so it is not a coastline. Land is derived as `union MINUS eez_v12` joined on `MRGID_EEZ` → 253 polygons. Caveat: EEZ v12 starts at the territorial-sea baseline, so internal waters read as land; affects 0.2% of vessel A's positions, all in harbours. The IMO study uses Natural Earth shorelines, which would avoid this.
4. **Sourced installed power and service speed** per hull, for estimate C. No free source found; raises on use.
5. ~~Smoothing window validated on one day~~ — now measured across the full series: v³ bias **1.94× unsmoothed, 1.38× at w=3**, lowest at w=3. Larger than the one-day sample suggested (1.67×/1.19×), and not directly comparable — the published figures covered one cruising day, this covers the whole series including port time.
6. **Joint-regime / overlapping-claim EEZ rule** — 56 polygons. Default `ISO_SOV1` with affected hours reported separately; the supplementary table turns out not to resolve it.
7. **Deliverable format** — notebook at `notebooks/01_methodology_walkthrough.ipynb`, generated by `notebooks/build_notebook.py`.

## THETIS-MRV: the first external validation, and what it says

Downloaded 2026-08-16 for IMO 9516454. **Only 2018 and 2019 exist** — EU MRV
monitoring began 1 January 2018 (so no 2017), and the hull made **zero EU calls from
2020 onward**, which GFW port-visit data independently confirms (EU calls: 23 in
2017, 8 in 2018, 4 in 2019, then none). Two independent sources agreeing the vessel
left the Europe trade after 2019 is itself a validation of the port-call extraction.

| RP | verified CO2 (t) |
|---|---|
| 2018 | 61,414.5862 |
| 2019 | 35,594.2757 |

**GFW port visits are NOT MRV "ports of call".** MRV counts a voyage from the *last
port of call* to an EEA port, and its definition of port of call **excludes stops at
anchorage**. GFW records Suez Canal transit anchorages (`egy-suezsouthanchorage`,
`egy-portsaid`, both `at_dock: false`) as port visits, so a naive leg reconstruction
breaks the Asia→Europe voyage at Suez and counts only the short Suez→Europe portion.
That undercounts MRV scope by ~4x. **321 of 389 GFW port visits are not at dock.**

Rebuilding MRV scope from `at_dock` calls only:

| year | estimate A | estimate B | verified | A ratio | B ratio |
|---|---|---|---|---|---|
| 2018 | 45,890 t | **60,363 t** | 61,415 t | 0.75x | **0.98x** |
| 2019 | — | — | 35,594 t | reconstruction fails |

**Estimate B matches verified emissions to within 2% for 2018; estimate A understates
by 25%.** This is the first evidence able to choose between the two power estimates,
and it favours B — the one that also passes the fleet-envelope check. Treat as
indicative, not settled: one year, and the 2019 reconstruction returns zero because
`at_dock` flags only 68 of 389 calls and none of the 2019 EU calls.

Next step: wire the dock-based scope reconstruction into `validate.compare_thetis_mrv`,
and find a better port-of-call signal than `at_dock`.

## Findings that contradict METHODOLOGY.md

Recorded because the specification was written before implementation.

| § | Finding |
|---|---|
| §1.7, §4.5 | **Coverage is not negligible.** Assumed so from 2024 alone (99.98%); measured 36%–99.98% across the period. Two contiguous lay-ups (2018-05-19→07-29, 71 d; 2019-06-10→2020-03-18, 282 d) with zero presence AND zero port calls. A uniform `E/coverage` correction would multiply 2019 by 2.77× for a hull that was not sailing. Gaps are now classified before correcting; the divisor is `coverage_active`. |
| §4.1 | **Table 16's 'Port 1–5 nm' column is tanker-only** — footnoted in the source, dropped in METHODOLOGY. Applying it to a container ship would allow At berth up to 5 nm out. |
| §4.1 | **At berth cannot be found from anchorage distance.** GFW anchorage coordinates sit off the berth: 1,143 h at berth against 17,427 h inside port visits. Port-visit intervals are used instead (`run.use_port_visit_intervals`), moving ~16,000 h to the correct auxiliary load — worth 0.6% of total CO₂. |
| §4.1/§4.2 | **The mode matrix is circular as written** — mode needs load, load-zeroing needs mode. Resolved by computing load from smoothed speed first; consistent because the matrix consults load only above 3 kn. |
| §1.5/§1.7 | **Gap-fill ordering is unspecified.** Nearest-neighbour position fill before speed derivation manufactures zeros. Order used: spine → position fill → derive on observed pairs with true Δt → linear speed fill. |
| §2.2 | **Estimate B's power range is wider than quoted.** 69,600–85,100 kW comes from rounding the Froude speeds to 22–23 kn; the full 21.48–23.75 kn range gives 64,788–93,656 kW. |
| §2 | GT disagrees: Equasis 154,592 vs GFW `tonnageGt` 153,666. Not a model input; both recorded. |
| §1/§8.2 | **GFW splits one port stay into several "visits"** as a hull shifts between adjacent anchorage polygons. 41 of 388 legs are under 2 h, all same-country, concentrated in the Pearl River Delta and Yangshan (10 are `chn-yangshan` → `chn-yangshan`, the same port id). Five imply >30 kn, because the distance is measured between the two ports' representative anchorage points while the vessel barely moved. Worth 1.0 h of 52,292 leg-hours and 21 misclassified vessel-hours — no material effect. **Diagnosed, not merged**: merging would need an invented threshold for "the same call" and would break the 389-port-call validation figure. A cross-border impossible leg would be a real fault and still WARNs. |

---

## Conventions

- Vessel-specific values go in `config/`, never in code. Scaling to the fleet must be a loop over an IMO list.
- Every estimated parameter carries a `source` and `method` field alongside its value, so a researcher with IHS access can substitute observed values without touching model code.
- Notation follows three systems by section — IMO for §1/§3/§4, naval architecture for §2, Selin for §5–§7. See METHODOLOGY.md. Note `MCR` means installed power throughout, because MEPC.333(76) and the Fourth GHG Study use `P_ME` for different quantities.
- Sequential API calls only — one concurrent report.

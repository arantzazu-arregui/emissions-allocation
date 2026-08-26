# CLAUDE.md — working notes for this repository

Read this before writing code. It records what has already been tested against the live APIs and what has been ruled out. Most items here cost a round-trip to discover; re-deriving them wastes time and, worse, several of the failure modes are **silent**.

The specification is `docs/METHODOLOGY.md`. This file is the operational companion: constraints, gotchas, and decisions already made.

---

## Project in one paragraph

A two-vessel replication of Selin et al. (2021), *Mitigation of CO₂ emissions from international shipping through national allocation* (Environ. Res. Lett. 16, 045009), built entirely from public data. AIS activity from Global Fishing Watch → hourly speed → CO₂ via the IMO Fourth GHG Study bottom-up model → allocated to countries under flag / owner / manager / operator rules → compared against Global Carbon Budget national baselines. Study period 2017-01-01 to 2024-12-31. The deliverable is a **template** other researchers extend from two vessels to the full fleet, so vessel-specific values belong in config, never in code.

---

## Environment

- Python venv at `.venv`; `requirements.txt` at root. `geopandas`, `shapely`, `pyogrio` are **not yet installed** and are needed for §4–5.
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
- **Bunker-fuel allocation is not computable** at two vessels. It needs national fuel-sales statistics. Out of scope by construction, not by omission.

**Two source-handling rules, each learned the hard way in this project.**

**Render the page before concluding a number isn't there.** Equations and tables are frequently images that text extraction drops silently. This has happened three times here.

**Never take coefficients from a paper that reproduces a standard — go to the standard.** Sun et al. (2026) reproduce the MEPC.333(76) EEXI table faithfully in appearance but drop the containership DWT caps and the GT exception for cruise ships. Using their version gave vessel A a 1.6x error in installed power and produced a wrong conclusion about the method's validity. The primary document is free and takes two minutes to check.

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

**EEXI curve fit — take from the resolution, never from a reproduction.** Primary source: [MEPC.333(76)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.333(76).pdf), paragraph 2.2.3.5 and Appendix. `V_ref,avg = A·B^C`, `MCR_avg = D·E^F`. Full 12-row tables are in `docs/METHODOLOGY.md` §2.4 and belong in `config/eexi_parameters.yaml`.

Three details that secondary reproductions (e.g. Sun et al. 2026 Table 1) drop, and that silently produce wrong numbers:
1. **Containerships are capped** — `B = min(DWT, 80 000)` for speed, `E = min(DWT, 95 000)` for power. Above the cap the estimate is flat.
2. **Cruise passenger ships with non-conventional propulsion use GT, not DWT.**
3. Coefficients carry 5–6 significant figures (10.6585, not 10.658).

Vessel A is 156,610 DWT so both caps bind: **V = 25.55 kn, MCR = 67,912 kW**. Uncapped it would give 28.89 kn / 113,673 kW — a 1.6× error, and the reason an earlier draft wrongly concluded the method "fails at the top of the container range". It does not; the caps exist for that range. Corrected, it agrees with the Admiralty estimate (69,600–85,100 kW).

**Admiralty coefficient**, calibrated on Charchalis Table 1 (17 ships): median **482**, mean 478, sd 57, range 352–593.

**Derived speed is biased, not just noisy.** Cell-centroid quantisation makes hour-to-hour speeds oscillate (3.36 → 21.63 kn while cruising ~15). Since power ∝ v³ this does not average out: `mean(v³)` = 2,654 vs `(mean v)³` = 1,588, a **1.67× overestimate**, falling to 1.19× with a 3-hour centred moving average. Smoothing is mandatory and its window is a sensitivity parameter.

---

## Vessel A — COSCO ITALY (fixed)

IMO **9516454** · MMSI 477845600 · callsign VRNE4 · flag HKG · 154,592 GT / **156,610 DWT** · Container Ship · built 2014 · LOA 365.90 m · beam 51.20 m · draught 16.0 m · **≈13,200 TEU** (derived by inverting `B = 3.27·TEU^0.29`) · vesselId `103dbbea9-9221-5f81-a00f-657a515745bb`.

Allocation keys — registered owner COSCO ITALY SHIPPING LTD (Shanghai, company IMO 4178111); ISM manager SHANGHAI OCEAN SHIPPING CO LTD (5193283); commercial manager COSCO SHIPPING LINES CO LTD (1043944). **All three resolve to China; flag is Hong Kong.**

389 port calls 2017–2024, all confidence 4, continuous every year. 35 EU calls (NLD 9, DEU 10, BEL 9, GRC 6, POL 1); 27 consecutive EU→EU legs → the EU-port MGO rule genuinely fires. North Sea and North American ECAs both apply. **24.9% of the period is spent in port** (17,427 h), so auxiliary and boiler demand is a large share of total emissions, not a correction term.

## Vessel B — not yet selected

Must be an open-registry hull (PAN/LBR/MHL/MLT/BHS/CYP) with owner country ≠ flag country, mid-range DWT, **distinctive ship name**, continuous coverage, ≥1 EU port call. Selection procedure in `docs/METHODOLOGY.md` §0.2. Vessel A alone gives a degenerate allocation — all four options resolve to China — so vessel B is what makes the comparison interpretable.

---

## Open items

1. **Vessel B selection** — run METHODOLOGY §0.2, then Equasis for its allocation keys and DWT.
2. **Selin et al. supplementary Table 1** (`stacks.iop.org/ERL/16/045009/mmedia`) — governs territory merging and the Hong Kong decision.
3. **Coastline layer** for distance-to-coast in the operating-mode matrix — Marine Regions *Marine and Land Zones v4*.
4. **Sourced installed power and service speed** per hull, for estimate C.
5. **Smoothing window** validated on more than one day.
6. **Joint-regime / overlapping-claim EEZ rule** — 56 polygons affected.
7. **Deliverable format** and fleet-readiness vs pilot legibility.

---

## Conventions

- Vessel-specific values go in `config/`, never in code. Scaling to the fleet must be a loop over an IMO list.
- Every estimated parameter carries a `source` and `method` field alongside its value, so a researcher with IHS access can substitute observed values without touching model code.
- Notation follows three systems by section — IMO for §1/§3/§4, naval architecture for §2, Selin for §5–§7. See METHODOLOGY.md. Note `MCR` means installed power throughout, because MEPC.333(76) and the Fourth GHG Study use `P_ME` for different quantities.
- Sequential API calls only — one concurrent report.

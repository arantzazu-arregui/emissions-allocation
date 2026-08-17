# Methodology

**Project:** National allocation of international shipping CO₂ emissions: a two-vessel replication of Selin et al. (2021), built entirely from public data sources.

**Status:** specification. Every equation and parameter below is traced to a source, and every item marked ⚠ is an open question, not a settled value.

**Pilot vessels:** two, chosen to sit on opposite sides of the paper's central finding.

| | Vessel A | Vessel B |
|---|---|---|
| Ship | COSCO ITALY, IMO 9516454 | RCC AMERICA, IMO 9277802 |
| Type | Container, 156,610 DWT, built 2014 | Vehicle carrier, 21,182 DWT, built 2003 |
| Flag | Hong Kong | Bahamas |
| Owner | Shanghai → China | Isle of Man, allocated to the United Kingdom |
| Represents | the **co-located majority**: Selin et al. find 74% of ships have owner, operator and manager in one country, covering 61% of emissions | the **open-registry minority** that drives the paper's equity argument |

**Study period:** 2017-01-01 to 2024-12-31.

***

## Scope and limits

Two vessels demonstrate the *machinery* of national allocation, plus one qualitative contrast between allocation rules. They cannot reproduce the paper's *findings*, which are distributional: top-20 country rankings, concentration shares, OECD versus non-OECD splits, the 74%-of-ships/61%-of-emissions co-location statistic. Those require the full fleet.

Two consequences follow directly:

* **The bunker-fuel allocation option is not computable at this scale.** It rests on national marine-bunker *sales* statistics; allocating one ship's emissions to a bunkering country would require knowing where it took fuel, which no public dataset records. Four of the paper's five options are reproduced here; the fifth is a fleet-scale construct by nature.
* **The international-versus-domestic classification is trivially satisfied.** Selin et al. classify a ship as domestic if >95% of its signals fall inside one country's EEZ. Vessel A calls at ports in seventeen countries. The test is implemented anyway, because the fleet-scale version needs it.

**Why two vessels rather than one.** Vessel A alone produces a degenerate allocation result. Its flag, owner, ISM manager and commercial manager all resolve to China once Hong Kong is folded into China under the UNFCCC party list: so all four computable options return the same country and the comparison that motivates the paper produces nothing. Keeping Hong Kong separate yields a single flag-versus-owner divergence, but that rests entirely on a contestable methodological choice (Section 6.4).

This is not a poorly chosen vessel; it is a *typical* one, and that is the point. The pair reproduces the paper's equity argument in miniature: **allocation choice is immaterial for the co-located majority and decisive for open-registry ships.** One vessel cannot show that. Two can, and the second also sits below the EEXI cap thresholds, so its power estimate scales with its actual size rather than being pinned at the cap (Section 2.2).

The pipeline is designed so that scaling from two vessels to the full fleet is a loop over an IMO list, not a rewrite.

***

## Notation

This document deliberately uses **three notation systems**, each governing the sections whose equations come from that source, so that a reader checking a section against its source reads the same symbols in both.

| Sections | Governing system | Source |
|---|---|---|
| Section 1, Section 3, Section 4 | IMO notation | *Fourth IMO GHG Study 2020* |
| Section 2 | Naval-architecture convention | Charchalis (2014), Cepowski & Chorab (2021), MEPC.333(76) |
| Sections 5–7 | Allocation notation | Selin et al. (2021) |

### IMO notation: Section 1, Section 3, Section 4

| Symbol | Meaning | Unit |
|---|---|---|
| `i` | hourly index | – |
| `SOG_i` | speed over ground in hour *i* (derived, Section 1.5) | knots |
| `SOḠ_i` | smoothed speed over ground (Section 1.6) | knots |
| `MCR` | maximum continuous rating: installed main-engine power | kW |
| `Ẇ_ME,i` | main-engine power demand in hour *i* | kW |
| `Ẇ_AE,i` | auxiliary-engine power demand | kW |
| `Ẇ_BO,i` | auxiliary-boiler power demand | kW |
| `Load_i` | main-engine load, `Ẇ_ME,i / MCR` | 0–1 |
| `CF_L` | main-engine load correction factor | – |
| `SFC_base` | base specific fuel consumption | g/kWh |
| `SFC_ME,i` | load-corrected main-engine SFC | g/kWh |
| `FC_i` | fuel consumption in hour *i* | g |
| `EFf` | fuel-based emission factor | g CO₂ / g fuel |
| `EFe` | energy-based emission factor (not used: CO₂ is fuel-based) | g/kWh |
| `LLF` | low load factor | – |

> **Two collisions to be aware of.**
> The IMO's own list of abbreviations defines **AB** = Auxiliary Boiler, but its equation (11) writes `FC_AE|BO,i`, using **BO**. This document uses `BO` in equations, matching the equation, and notes `AB` where the abbreviation list is quoted.
> Separately, **MEPC.333(76) writes `P_ME` for *installed* power** while the Fourth GHG Study writes `Ẇ_ME,i` for *instantaneous* demand. Same subscript, different quantity, different document. This document uses **`MCR`** for installed power throughout to keep the two apart.

### Naval-architecture notation: Section 2

| Symbol | Meaning | Unit |
|---|---|---|
| `V` | design / service speed | knots |
| `Δ` | displacement | tonnes |
| `C_B` | block coefficient | – |
| `Fn` | Froude number | – |
| `C_adm` | Admiralty coefficient | – |
| `L_BP` | length between perpendiculars | m |
| `B`, `T` | beam, draught | m |

### Allocation notation: Sections 5–7

| Symbol | Meaning | Unit |
|---|---|---|
| `E_c` | CO₂ allocated to country *c* | t or Mt |
| `B_c` | national CO₂ baseline for country *c* | Mt CO₂ |
| `ΔE_c` | absolute addition to *c*'s budget | Mt CO₂ |
| `ΔE%_c` | percentage addition to *c*'s budget | % |

**Architecture.** Python handles API access, parsing, and the physical model. DuckDB handles spatial joins, aggregation, allocation and reporting. The dividing principle: *lookups, joins, windows and aggregations are SQL; physical formulas are Python.*

***

# 0. Select vessels

### Purpose
Choose the study vessels by reproducible criteria rather than by hand, so a researcher extending this work can apply the same filter to select any number of ships.

### 0.1 Criteria

| # | Criterion | Why |
|---|---|---|
| 1 | Valid IMO number present in GFW records | Selin et al. restrict the study universe to IMO-registered ships |
| 2 | `vesselType` in CARGO, TANKER, CARRIER | the international merchant fleet |
| 3 | **Distinctive ship name** | the presence filter matches on name, exactly and case-sensitively (Section 1.2); a common name pulls multiple hulls |
| 4 | Continuous GFW presence across all study years | avoids a truncated series being read as an emissions decline |
| 5 | Port calls in ≥3 countries | confirms international operation |
| 6 | ≥1 EU port call | unlocks THETIS-MRV validation (Section 8.2) |
| 7 | For vessel B: flag in an open registry **and** owner country ≠ flag country | produces the flag-versus-owner divergence under study |
| 8 | For vessel B: deadweight below the EEXI cap for its type | above the cap the EEXI estimate is flat, so a capped hull gets a less size-specific figure (Section 2.4) |

### 0.2 Procedure

Candidate discovery cannot use the ship-name filter, because names are what we are searching *for*. It uses the flag and type filters instead, which are the other two working presence filters:

1. Query 4Wings presence at world extent for representative days, filtered by open registry and merchant vessel type, then grouped by `VESSEL_ID`. The selection run covered six open registries and produced a large candidate pool cheaply.
2. Extract distinct `imo`, `shipName`, `flag` from the payload. Discard blank IMOs.
3. Discard names that are not distinct within the pool (criterion 3).
4. For surviving candidates, pull port-visit events for the full period and apply criteria 4–6.
5. Look up the shortlist in Equasis and apply criterion 7: registered-owner country must differ from flag country.
6. Rank by hour coverage and take the top candidate.

### Outputs
`config/pilot.yaml`: the selected IMO numbers with the criteria values that justified each selection, so the choice is auditable rather than asserted.

### Status
Vessel A is fixed. Vessel B is RCC AMERICA, IMO 9277802. It was selected from the open registry candidate pool, has a distinctive name, continuous study period coverage, 588 port calls across 64 countries, and 122 EU port calls. Its Bahamas flag, Isle of Man owner and commercial manager, and Greek ISM manager provide the intended allocation contrast.

***

# 1. Acquire ship activity data and identify unique ships

### Purpose
Produce a continuous, ordered, hourly position-and-speed series for the vessel across the full study period, plus the sequence of port calls that defines its voyages.

This section covers workflow steps 1, 2, and 6. GFW hourly presence positions replace raw AIS messages; speed is derived from consecutive positions rather than represented by speed-bin midpoints. IMO is the stable hull key, so changing names, MMSIs, or GFW `vesselId` values do not create new ships. Coverage is calculated before any gap filling; interpolated hours are flagged, and centroid-derived speeds are smoothed before the cubic power calculation.

### Inputs

| Source | Endpoint / dataset | Access |
|---|---|---|
| Global Fishing Watch 4Wings | `POST /v3/4wings/report`, `public-global-presence:latest` | free non-commercial token |
| Global Fishing Watch Events | `GET /v3/events`, `public-global-port-visits-events:latest` | same token |
| Global Fishing Watch Vessels | `GET /v3/vessels/search`, `GET /v3/vessels/{id}` | same token |

### Implementation

**1.1 Resolve identity.** Search the Vessels API by IMO number to obtain `vesselId`, `shipname`, flag, GT and the registry record. Retain the full name history from `selfReportedInfo`, since the presence filter matches on name.

> `combinedSourcesInfo` returns **multiple `vesselId` values for one hull** (this vessel has two). Anchor every downstream join on the IMO number. `vesselId` is not a stable key across GFW identity dataset versions.

**1.2 Pull hourly presence.** One request per calendar year:

```
POST /v3/4wings/report
  spatial-resolution  = HIGH        (0.01°)
  temporal-resolution = HOURLY
  group-by            = VESSEL_ID
  date-range          = <year>-01-01,<year+1>-01-01
  filters[0]          = shipname in ("COSCO ITALY")
  body                = { geojson: world polygon (-180,-85 → 180,85) }
```

Returns one record per vessel-hour-cell: `date` (an hourly timestamp), `lat`, `lon`, `hours`, plus `imo`, `mmsi`, `flag`, `shipName`, `vesselType` inline.

> **Three hard assertions.** A wrong ship name returns HTTP 200 with zero records and no error. The loader must assert (a) the result is non-empty, (b) exactly one distinct IMO is present, (c) observed hours are within tolerance of elapsed hours. Silent emptiness is the dominant failure mode of this endpoint.

**1.3 Post-filter on IMO.** `imo` is present in the payload but is *not* a filterable field. Filter in the loader, not in the request.

**1.4 Pull port visits.** Paginated, `limit=100`, advancing on `nextOffset`, confidences 3 and 4 (confidence 1–2 are not downloadable). Each event yields start and end timestamps, start/intermediate/end anchorage with a port identifier and **port-country ISO3**, duration, and an EEZ MRGID.

**1.5 Derive speed over ground.** For consecutive hourly positions, great-circle distance over elapsed time:

```
a      = sin²(Δφ/2) + cos φ₁ · cos φ₂ · sin²(Δλ/2)
d      = 2R · asin(√a)                     R = 6371.0088 km
SOG_i  = d / (1.852 · Δt)                  knots, Δt in hours
```

`SOG` is the IMO's term and the AIS field name, but note the difference in provenance: in the Fourth GHG Study `SOG` is *transmitted* by the vessel, whereas here it is *derived* from consecutive cell centroids. Section 1.6 exists because of that difference.

**1.6 Smooth the speed series.** *This step is not cosmetic.* GFW credits each vessel-hour to a single 0.01° cell. A ship crossing ~22 cells per hour lands unpredictably within them, so consecutive-centroid speeds oscillate: 3.36 to 21.63 kn observed while the vessel was cruising steadily at ~15 kn. Because propulsion power scales as v³, the error does not average out:

```
mean(v³) = 2 654     vs     (mean v)³ = 1 588        →  1.67× overestimate
after a 3-hour centred moving average                →  1.19× overestimate
```

Apply a centred moving average of width *w*:

```
SOḠ_i = (1/w) · Σ SOG_{i+k},   k = −(w−1)/2 … +(w−1)/2
```

⚠ *w* is a free parameter, currently validated on one day of data. It is one of the two variables carried through the sensitivity analysis (Section 8).

**1.7 Compute observed coverage.**

```
coverage = observed hours ÷ elapsed hours in period
```

Measured at **8,782 / 8,784 = 99.98%** for 2024. Selin et al. interpolated 32% of their hours from 2015 AIS; for a large container ship on major trade lanes in 2024, gap-filling is close to unnecessary. Where gaps do occur, interpolate position by nearest-neighbour and speed linearly, following the paper, and flag interpolated hours.

### Outputs

| Table | Grain | Key fields |
|---|---|---|
| `vessel_hour` | one row per vessel per hour | `imo, ts, lat, lon, hours, sog_raw, sog_smoothed, is_interpolated` |
| `port_call` | one row per port visit | `imo, start_ts, end_ts, port_id, port_iso3, duration_h, confidence, eez_mrgid` |
| `voyage_leg` | one row per consecutive port pair | `imo, depart_ts, arrive_ts, origin_iso3, dest_iso3, is_eu_eu` |

`voyage_leg` is built in SQL with `LAG` over `port_call` ordered by time.

### Limitations
Positions are cell centroids, not true fixes. Speed is derived, not transmitted: GFW does not expose AIS speed over ground directly. AIS navigational status is unavailable, which matters in Section 4. Presence hours are observed-only.

***

# 2. Acquire ship registry data and complete ship specifications

### Purpose
Assemble the technical parameters the emission model requires, and be explicit that two of them cannot be observed from any free source.

This section covers workflow steps 3 and 4. Equasis replaces paid WRS/IHS data for public registry fields and is linked by IMO. Because it does not provide engine power, design speed, or a true operator field, power and speed are estimated for every pilot hull using the three parallel scenarios in Section 2.2; commercial manager is used as the documented operator proxy in Section 5.

### Inputs

| Parameter | Source | Value (pilot) | Status |
|---|---|---|---|
| IMO, MMSI, callsign, flag | GFW / Equasis | 9516454 / 477845600 / VRNE4 / HKG | observed |
| Gross tonnage | Equasis | 154,592 (since 2023) | observed |
| Deadweight | Equasis | 156,610 t | observed |
| Ship type | Equasis | Container Ship | observed |
| Year built | Equasis | 2014 | observed |
| LOA, beam, draught | public vessel registers | 365.90 m, 51.20 m, 16.0 m | observed |
| TEU capacity | derived, Section 2.1 | ≈ 13,200 | **estimated** |
| Design speed `V` | derived, Section 2.2 | three estimates | **estimated** |
| Installed power `MCR` | derived, Section 2.2 | three estimates | **estimated** |
| Engine type | assigned, Section 2.3 | slow-speed diesel | assigned |
| Owner / manager / operator country | Equasis | see Section 5 | observed |

> **The central data constraint of this replication.** Selin et al. used IHS World Register of Shipping, a paid commercial register, for installed power and design speed. The IMO Fourth GHG Study's own fallback regressions (its equations 3 and 4) publish only symbolic coefficients `b₁…b₄` and are mutually circular: speed requires power, power requires speed: so they cannot generate both from scratch. **No free source supplies these two parameters.** Three independent estimates are therefore carried in parallel, with no primary.

### 2.1 TEU capacity

IMO Table 17 indexes container ships by TEU, not deadweight. Equasis does not carry TEU. Invert Cepowski & Chorab's (2021) beam relation, fitted on 215 distinct container designs built 2015–2020:

```
B = 3.27 · TEU^0.29        →     TEU = (B / 3.27)^(1/0.29)
```

At B = 51.20 m this gives **13,174 TEU**, placing the vessel in Table 17's 12,000–14,499 TEU band. Their draught relation gives 15,840, but beam is the better proxy for container ships because it sets the on-deck row count.

Their deadweight relations validate well against this hull, which is the basis for trusting the inversion:

```
LBP = 3.656 · DWT^0.38  →  344.4 m   (actual LOA 365.9 m; LBP ≈ 0.95 LOA)
B   = 1.15  · DWT^0.32  →   52.8 m   (actual 51.2 m)
T   = 0.624 · DWT^0.27  →   15.8 m   (actual 16.0 m)
```

⚠ Their TEU-based LBP relation, `LBP = 3.16·TEU^0.34`, returns 79.5 m at 13,200 TEU and is evidently mis-transcribed. Not used.

### 2.2 Design speed and installed power: three estimates

**Estimate A: IMO EEXI curve fit.** Source: **IMO Resolution MEPC.333(76)**, *2021 Guidelines on the method of calculation of the attained Energy Efficiency Existing Ship Index (EEXI)*, adopted 17 June 2021: **paragraph 2.2.3.5 and the Appendix**. The guidelines provide these for the case where "the speed-power curve is not available or the sea trial report does not contain the EEDI or design load draught condition".

```
V_ref,avg = A · B^C            [knots]
MCR_avg   = D · E^F            [kW]
```

`B` and `E` are **not** simply DWT. They are DWT for most ship types, GT for cruise passenger ships, and for containerships they are **capped**. The full tables are reproduced in Section 2.4 because scaling this work to the fleet requires all twelve ship types, not one row.

For a containership: `A = 3.2395`, `B = min(DWT, 80 000)`, `C = 0.18294`; `D = 0.5042`, `E = min(DWT, 95 000)`, `F = 1.03046`.

Vessel A is 156,610 DWT, so both caps bind:

```
V_ref,avg = 3.2395 · 80 000^0.18294  = 25.55 kn
MCR_avg   = 0.5042 · 95 000^1.03046  = 67 912 kW
```

> **Correction, recorded deliberately.** An earlier draft of this document took the coefficients from a secondary reproduction (Sun et al. 2026, Table 1) which prints them without the DWT caps and without the GT exception. Applying them uncapped gives 28.89 kn and 113,673 kW, and led to the conclusion that the EEXI method "fails at the top of the container range". **It does not.** The caps exist precisely to handle that range. Corrected, Estimate A (67,912 kW) agrees closely with Estimate B's Admiralty range (69,600–85,100 kW), where the uncapped figure had disagreed by a factor of 1.6. Always take these coefficients from the resolution, never from a reproduction.

> Note also that MEPC.333(76) writes `P_ME` for installed power in other contexts, whereas the Fourth GHG Study writes `Ẇ_ME,i` for instantaneous demand. Downstream, this document always calls installed power `MCR`.

The method validates well across the fleet: 80,000 DWT bulk carrier → 14.46 kn / 10,672 kW; 300,000 DWT tanker → 16.08 kn / 26,000 kW; 50,000 DWT container ship → 23.47 kn / 34,863 kW. The remaining tension is modest: 25.55 kn still sits just above 24.5 kn, the maximum service speed among 215 distinct container designs built since 2015 (Cepowski & Chorab, Table 1). That is a reference line fitted to a historical fleet, applied to a modern slow-steaming hull: a known and bounded bias, not a failure.

**Estimate B: Admiralty coefficient, calibrated.** Design speed from the Froude number:

```
Fn = v / √(g · L_BP)      →      V = Fn · √(g · L_BP) / 0.5144   [knots]
```

At L_BP = 345 m and Fn = 0.19–0.21 (the modern container fleet mean is 0.21, and larger recent ships trend lower), **V = 21.5–23.8 kn**.

Installed power from the Admiralty relation:

```
MCR = Δ^(2/3) · V³ / C_adm
```

`C_adm` is calibrated on Charchalis's (2014) published table of 17 container ships with matched speed, power and displacement: **median 482, mean 478, sd 57, range 352–593**: consistent with the textbook 400–600 band.

Displacement by two routes:

```
Δ = C_B · L_BP · B · T · ρ      C_B = 0.61, ρ = 1.025   →  176,807 t
Δ = DWT / 0.80                  (Charchalis ratio)      →  195,762 t
```

Giving **69,600–85,100 kW at 22–23 kn**.

⚠ `C_adm` is calibrated on 1,200–1,400 TEU feeders and extrapolated across a tenfold size jump. This is the weakest joint in the estimate. Note also that Charchalis's displacement column runs ~18% above `C_B·L·B·T·ρ` on his own worked example, so the two displacement routes bracket a real convention difference, not a rounding error.

**Estimate C: sourced specification.** Installed power and service speed taken from shipbuilder or class-society records for this specific hull, where obtainable. Most defensible for the pilot; does not scale.

All three are carried through to the CO₂ result. The spread between them is a reported output, not an error to be resolved.

### 2.3 Engine type

The IMO study classifies engines from an IHS field unavailable to us, but states that **slow-speed diesel is the default for any oil-propelled ship not otherwise classifiable**, and Table 10 gives engine-type shares by year. A 13,200 TEU container ship built in 2014 is a two-stroke slow-speed diesel on any reading. Assigned SSD, documented as an assignment.

### 2.4 EEXI estimation parameters: complete tables, all ship types

Reproduced from **IMO Resolution MEPC.333(76), Appendix**. These belong in `config/eexi_parameters.yaml` as data, not in code, so that extending this work from two vessels to the full fleet requires no edit to the model.

**Table 2.4a: Parameters to calculate `V_ref,avg`**, where `V_ref,avg = A · B^C` [knots]

| Ship type | A | B | C |
|---|---|---|---|
| Bulk carrier | 10.6585 | DWT | 0.02706 |
| Gas carrier | 7.4462 | DWT | 0.07604 |
| Tanker | 8.1358 | DWT | 0.05383 |
| **Containership** | 3.2395 | **min(DWT, 80 000)** | 0.18294 |
| General cargo ship | 2.4538 | DWT | 0.18832 |
| Refrigerated cargo carrier | 1.0600 | DWT | 0.31518 |
| Combination carrier | 8.1391 | DWT | 0.05378 |
| LNG carrier | 11.0536 | DWT | 0.05030 |
| Ro-ro cargo ship (vehicle carrier) | 16.6773 | DWT | 0.01802 |
| Ro-ro cargo ship | 8.0793 | DWT | 0.09123 |
| Ro-ro passenger ship | 4.1140 | DWT | 0.19863 |
| Cruise passenger ship, non-conventional propulsion | 5.1240 | **GT** | 0.12714 |

**Table 2.4b: Parameters to calculate `MCR_avg`** (or `MPP_avg`), where `MCR_avg = D · E^F` [kW]

| Ship type | D | E | F |
|---|---|---|---|
| Bulk carrier | 23.7510 | DWT | 0.54087 |
| Gas carrier | 21.4704 | DWT | 0.59522 |
| Tanker | 22.8415 | DWT | 0.55826 |
| **Containership** | 0.5042 | **min(DWT, 95 000)** | 1.03046 |
| General cargo ship | 0.8816 | DWT | 0.92050 |
| Refrigerated cargo carrier | 0.0272 | DWT | 1.38634 |
| Combination carrier | 22.8536 | DWT | 0.55820 |
| LNG carrier | 20.7096 | DWT | 0.63477 |
| Ro-ro cargo ship (vehicle carrier) | 262.7693 | DWT | 0.39973 |
| Ro-ro cargo ship | 37.7708 | DWT | 0.63450 |
| Ro-ro passenger ship | 9.1338 | DWT | 0.91116 |
| Cruise passenger ship, non-conventional propulsion | 1.3550 | **GT** | 0.88664 |

**Three implementation rules that a single-row extract would hide:**

1. **Containerships are capped**: 80,000 DWT for speed, 95,000 DWT for power. Above the cap the estimate is flat, not extrapolated. This is the single most consequential detail in both tables.
2. **Cruise passenger ships with non-conventional propulsion use GT, not DWT.** Passing DWT for that row silently returns a wrong number.
3. **Ship-type strings must map to these twelve categories exactly.** GFW returns coarse types (`CARGO`, `TANKER`, `CARRIER`); Equasis returns finer ones (`Container Ship`, `Bulk Carrier`). The mapping between them belongs in config, and any unmapped type must raise rather than fall through to a default.

⚠ Coefficients here carry the resolution's full precision. Secondary reproductions round them (10.658 for 10.6585, 0.541 for 0.54087) and, more importantly, drop the caps and the GT exception. Use this table.

### Outputs
`config/vessel_specs.yaml`: one block per IMO, each parameter carrying a `value`, `source` and `method` field, so a researcher with IHS access can substitute observed values without touching model code.
`config/eexi_parameters.yaml`: Tables 2.4a and 2.4b as data, with the cap and GT rules encoded.

***

# 3. Assign fuel type and emission factors

### Purpose
Assign a fuel to every vessel-hour and attach the corresponding emission factor and specific fuel consumption.

Fuel is assigned at vessel-hour grain using engine type, ECA geometry, and voyage context. Port-visit events identify actual consecutive EU-port pairs, so the EU rule uses `voyage_leg.is_eu_eu` rather than an EEZ-only proxy. The IMO Fourth GHG Study supplies both fuel-based CO₂ emission factors and fuel-/engine-specific SFC values; the 2020 sulphur cap does not change CO₂ factors.

### Inputs
* IMO Fourth GHG Study 2020, Tables 18, 19, 20, 21
* Marine Regions ECA shapefile, MARPOL Annex VI Regulation 14 (SOx and PM): 6 polygons: Baltic Sea, North Sea, US Caribbean, North American areas 1–3
* `voyage_leg` from Section 1

### 3.1 Fuel assignment rule

Following Selin et al., a vessel-hour is assigned distillate fuel (MDO/MGO) when **any** of the following holds, and residual fuel (HFO) otherwise:

1. the main engine is high-speed: not applicable here;
2. the position falls inside an ECA polygon;
3. the hour belongs to a voyage leg between two EU ports.

Condition 2 is a point-in-polygon test in DuckDB. Condition 3 is read directly from `voyage_leg.is_eu_eu`: a genuine improvement on the EEZ proxy that gridded-only data would have forced.

Both conditions are live for this vessel: it makes 39 US calls (North American ECA) and 28 calls at Dutch, German and Belgian ports (North Sea ECA), with **27 consecutive EU→EU legs** across the period.

⚠ The ECA shapefile predates the Mediterranean SOx ECA (in force May 2025). Irrelevant to a period ending in 2024; must be added if the horizon extends.

### 3.2 The IMO 2020 sulphur cap

The study period straddles the 0.50% global sulphur cap of 1 January 2020, which moved most of the fleet from HFO to VLSFO. **For CO₂ purposes this is immaterial**: the Fourth IMO GHG Study assigns low-sulphur HFO the same carbon content and emission factor as HFO (Table 21, `LSHFO 1.0%` → 3.114). Whether the vessel carries a scrubber therefore affects SOx, not CO₂. The fuel-switch date is recorded for transparency but does not change the result.

### 3.3 Emission factors (Table 21)

| Fuel | Carbon content | `EF_f` (g CO₂ / g fuel) |
|---|---|---|
| HFO | 0.8493 | **3.114** |
| LSHFO 1.0% | 0.8493 | **3.114** |
| MDO (incl. MGO) | 0.8744 | **3.206** |
| LNG | 0.7500 | 2.750 |
| Methanol | 0.3750 | 1.375 |

### 3.4 Base specific fuel consumption (Table 19, 2001+ column)

| Engine | HFO | MDO |
|---|---|---|
| Slow-speed diesel (main) | 175 | 165 |
| Auxiliary engines | 195 | 185 |
| Boilers | 340 | 320 |

### 3.5 Low-load adjustment (Table 20)

Low-load factors for CO₂ are **1.00 at every load**: CO₂ varies directly with fuel consumption, which is already load-dependent, so no adjustment is applied. One rule does carry over: **the main engine reports no fuel consumption or emissions below 7% MCR.**

### Outputs
`config/emission_factors.yaml`, and a `fuel_assignment` table at vessel-hour grain carrying `fuel_type`, `in_eca`, `is_eu_eu_leg`.

***

# 4. Calculate CO₂ emissions

### Purpose
Convert the hourly activity series into CO₂ mass, per hour, summed to ship-year.

Each vessel-hour receives an operating mode, smoothed-speed main-engine load, the 7% MCR threshold, auxiliary and boiler demand, load-corrected main-engine SFC, and the assigned fuel factor before it is summed to ship-year.

### Inputs
`vessel_hour` (Section 1), `vessel_specs` (Section 2), `fuel_assignment` (Section 3), IMO Tables 16 and 17.

### 4.1 Operating mode

IMO Table 16 assigns one of five modes from speed, main-engine load, distance to port and distance to coast:

| SOG (kn) | ME load | Port ≤1 nm | Port 1–5 nm | Coast ≤1 nm | Coast 1–5 nm | Coast ≥5 nm |
|---|---|---|---|---|---|---|
| ≤1 | – | At berth | At berth | Anchored | Anchored | Anchored |
| 1–3 | – | Anchored | Anchored | Anchored | Anchored | Anchored |
| 3–5 | ≤0.65 | Manoeuvring | Manoeuvring | Manoeuvring | Manoeuvring | Slow transit |
| 3–5 | >0.65 | Manoeuvring | Manoeuvring | Manoeuvring | Manoeuvring | Normal cruising |
| >5 | ≤0.65 | Manoeuvring | Slow transit | Slow transit | Slow transit | Slow transit |
| >5 | >0.65 | Manoeuvring | Normal cruising | Normal cruising | Normal cruising | Normal cruising |

Implemented as a `CASE` expression in SQL. Distance to port comes from the port-call anchorage coordinates; distance to coast requires a coastline layer (Marine Regions *Marine and Land Zones v4*).

⚠ GFW does not expose AIS navigational status, so "at berth" and "anchored" are separated by distance alone. This matters: for the 12,000–14,499 TEU band, Table 17 gives 1,300 kW auxiliary at berth against 1,800 kW anchored. **This vessel spends 24.9% of the study period inside port visits**: 17,427 hours of ~70,128, median stay 30.1 hours: so auxiliary and boiler demand is a large share of the total, not a correction term.

### 4.2 Main-engine power demand

```
Load_i  = ( SOḠ_i / V )³                     capped at 1.0
Ẇ_ME,i  = MCR · Load_i
```

with `Ẇ_ME,i = 0` where `Load_i < 0.07` (Section 3.5) and in At berth / Anchored modes.

### 4.3 Auxiliary engine and boiler demand

From IMO Table 17, by ship type × size band × operating mode. For a 12,000–14,499 TEU container ship:

| Mode | `Ẇ_BO,i` (kW) | `Ẇ_AE,i` (kW) |
|---|---|---|
| At berth | 630 | 1,300 |
| Anchored | 630 | 1,800 |
| Manoeuvring | 630 | 3,250 |
| At sea | 0 | 2,050 |

Implemented as a range join on the TEU band. Threshold rules: below 150 kW `MCR`, auxiliary and boiler are zero; between 150 and 500 kW, auxiliary is 5% of `MCR`. Neither applies here.

### 4.4 Fuel consumption

Main engine, load-corrected: **IMO equation (10)**, verbatim:

```
SFC_ME,i = SFC_base · ( 0.455·Load_i² − 0.710·Load_i + 1.280 )
                      └──────────── CF_L ────────────┘
```

The parenthetic term is the IMO's `CF_L`. The quadratic minimises at `Load_i` = 0.78, matching the study's stated ~80% MCR optimum: an internal check that the coefficients are transcribed correctly.

Auxiliary engines and boilers are **not** corrected by `CF_L`: **IMO equation (11)**:

```
FC_AE|BO,i = SFC_base · Ẇ_AE|BO,i
```

### 4.5 Hourly and annual CO₂

```
FC_i      = [ Ẇ_ME,i·SFC_ME,i + Ẇ_AE,i·SFC_base,AE + Ẇ_BO,i·SFC_base,BO ] · Δt   [g fuel]
E_CO2,i   = FC_i · EFf_i / 10⁶                                                    [tonnes]
E_ship,y  = Σ_i E_CO2,i                                                           [t / year]
```

with `Δt = 1 h`. Where coverage correction is applied, `E_ship,y ÷ coverage`; measured coverage of 99.98% makes this a negligible adjustment for this vessel, but it is computed and reported either way.

`LLF` does not appear because CO₂'s low-load factor is 1.00 at every load (Section 3.5).

### Outputs

| Table | Grain |
|---|---|
| `emissions_hour` | vessel × hour × scenario: mode, loads, fuel, CO₂ |
| `emissions_year` | vessel × year × scenario |

"Scenario" is the cross join of three power/speed estimates × the smoothing-window values (Section 8).

### Limitations
Every engine parameter is estimated, not observed. Weather, hull fouling, draught-dependent resistance and auxiliary load variation are outside the model, as they are in the source paper.

***

# 5. Classify international ships and allocate emissions to countries

### Purpose
Attribute the vessel's annual CO₂ to countries under each allocation rule.

This section covers workflow steps 5, 9, and 10. First, EEZ assignment and the >95%-of-hours rule identify international ships. Annual emissions are then aggregated by flag, registered owner, ISM manager, and commercial-manager-as-operator-proxy country. The bunker-fuel-sales option remains a documented fleet-scale extension, not a two-vessel output, because individual refuelling locations are unavailable.

### Inputs
`emissions_year` (Section 4); Equasis company records; EEZ v12 and High Seas v2 for the domestic/international test.

### 5.1 Allocation keys

| Option | Source | Vessel A (COSCO ITALY) | Vessel B |
|---|---|---|---|
| Flag country | Equasis / GFW registry | **Hong Kong** | **Bahamas** |
| Owner country | Equasis registered owner | COSCO ITALY SHIPPING LTD, Shanghai → **China** | AMERICA MARITIME LTD, Isle of Man → **United Kingdom** |
| Manager country | Equasis ISM manager | SHANGHAI OCEAN SHIPPING CO LTD → **China** | STAMCO SHIP MANAGEMENT CO LTD, Greece → **Greece** |
| Operator country | Equasis commercial manager (proxy) | COSCO SHIPPING LINES CO LTD → **China** | RAY CAR CARRIERS LTD, Isle of Man → **United Kingdom** |
| Bunker-fuel country | not computable at this scale |: |: |

**The contrast is the result.** Vessel A's four options converge on a single country: entirely so once Hong Kong folds into China, and 3-to-1 otherwise. Vessel B's diverge by construction. Reporting them side by side is what makes the allocation comparison interpretable at this scale: it shows that the choice of allocation rule redistributes responsibility for some ships and not others, and that the ships it moves are systematically those on open registries. That is Selin et al.'s equity finding, reproduced at n = 2 instead of n = 44,000.

Equasis supplies an **IMO company identification number** for each role (4178111 owner, 5193283 ISM manager, 1043944 commercial manager), which gives the fleet-scale version a stable join key rather than fuzzy name matching.

### 5.2 Two caveats on the keys

**Operator is a proxy.** Equasis has no operator field; commercial manager stands in. This partially collapses the operator and manager options and must be reported as such.

**Equasis gives an address, not a country of incorporation.** "COSCO ITALY SHIPPING LTD, C/O COSCO Shipping Lines, Shanghai" is a single-purpose company whose place of registration is not stated. Selin et al. used WRS's owner-country field, which is a different construct.

⚠ GFW's `registryOwners.flag` returns HKG for this owner: identical to the vessel's flag and contradicting the Shanghai address. It appears to echo the ship's flag rather than record owner domicile. **Not used**, pending a test on a case where the two must differ.

### 5.3 Allocation

```
E_c,option = Σ_ships E_ship · 1[ key_option(ship) = c ]
```

At n = 2 this reduces to assigning each vessel's total to one country per option. The SQL is written as the general aggregation so the fleet case needs no change.

### 5.4 International/domestic test

Assign each vessel-hour to an EEZ by point-in-polygon against EEZ v12 (285 polygons, EPSG:4326); hours matching no polygon fall to the high seas. A ship is domestic if >95% of its hours lie in a single country's EEZ.

⚠ EEZ v12 contains 21 joint-regime and 35 overlapping-claim polygons. `ISO_SOV1` is never null (unlike GFW's own layer), so the default is to assign to `ISO_SOV1` and report affected hours separately. **This rule is not yet settled**: Selin et al.'s supplementary Table 1 documents their territory handling and has not been consulted.

### Outputs
`allocation`: country × year × option × scenario, in tonnes CO₂.

***

# 6. Establish baseline carbon budgets

### Purpose
Establish the national baseline against which allocated emissions are measured.

Use national fossil CO₂ emissions excluding land-use change as the denominator, align territories to the UNFCCC treatment, and retain an EU27 aggregate for fleet-scale comparability. The baseline is Global Carbon Budget 2025; its territorial-emissions data are in MtC and must be converted to MtCO₂ before impact calculation. Hong Kong is reported both separately and folded into China because the choice is decisive for Vessel A.

### Inputs
Global Carbon Budget 2025, *National Fossil Carbon Emissions v2025*, sheet **Territorial Emissions**, header at row index 11 (0-based). Wide layout: rows are years 1850–2024, columns are 232 countries.

### 6.1 Unit conversion

**GCB reports million tonnes of carbon, not CO₂.**

```
B_c [Mt CO₂] = B_c [Mt C] · 3.664
```

### 6.2 Bunkers are already excluded

National columns exclude bunker fuels; only the World total includes them. The denominator is therefore clean and there is no double-counting when shipping emissions are added.

A free cross-check comes with it: GCB carries an `International Shipping` column: 170.15 MtC for 2024, i.e. **623 Mt CO₂**: an independent estimate of the global total to sanity-check any fleet-scale result against.

### 6.3 Country alignment

Selin et al. merge overseas territories into parent countries and align to the UNFCCC party list, building an EU27 aggregate. GCB's `Regions` sheet supplies ready-made KP Annex B, OECD and EU27 groupings, which are the aggregations the paper reports.

### 6.4 The Hong Kong question: reported both ways

GCB carries Hong Kong separately from China:

| Entity | 2024 baseline |
|---|---|
| Hong Kong | 9.09 MtC = **33.3 Mt CO₂** |
| China | 3,354 MtC = **12,289 Mt CO₂** |

A factor of ~370 between them. Hong Kong is not a separate UNFCCC party: it is covered by China's ratification: so the paper's own alignment rule would fold it into China, and this vessel's flag-versus-owner divergence would vanish entirely.

Because the choice is decisive and not obviously correct either way, **both treatments are computed and reported as a sensitivity**, and the gap between them is presented as a methodological finding rather than buried in an assumption.

### Outputs
`baseline`: country × year × Mt CO₂ × HK-treatment.

***

# 7. Compute allocation impacts

### Purpose
Express allocated emissions as absolute and relative additions to national carbon budgets, and rank them.

Join each allocation option to the matching baseline and calculate absolute addition, percentage addition, ranks, and concentration shares. The SQL retains the full-fleet top-10/top-20 pathway; at two vessels, reported results instead focus on annual totals, allocation-rule contrasts, Hong Kong treatment, and scenario spread.

### 7.1 Equations

```
ΔE_c        = E_c                                    [Mt CO₂]
ΔE%_c       = 100 · ΔE_c / B_c                       [%]
rank_c      = RANK() OVER (PARTITION BY option, scenario ORDER BY ΔE_c DESC)
share_top20 = Σ_{rank ≤ 20} ΔE_c  /  Σ_c ΔE_c
```

Ranking, percent-of-total and concentration shares are window functions over the `allocation` table joined to `baseline`.

### 7.2 What n = 2 produces

Rankings and concentration shares are structurally meaningless at n = 2: the code path exists and is exercised, but the interpretable outputs at this scale are:

* annual and total CO₂, under each of the three power/speed estimates;
* the same figure attributed to Hong Kong versus China under flag and owner options;
* ΔE% against each candidate baseline, which is where the ~370× divergence becomes visible;
* the spread across scenarios as an explicit uncertainty band.

### 7.3 Cross-option comparison

Selin et al.'s equity evidence rests on owner–operator–manager co-location and on flag-versus-owner cross-tabulation. This vessel is a clean instance of the pattern: **all three commercial roles resolve to China while the flag is Hong Kong**, so it illustrates the mechanism the paper quantifies at fleet scale.

### Outputs
`impacts`: country × year × option × scenario × HK-treatment, with `ΔE`, `ΔE%`, `rank`; plus a scenario-spread summary.

***

# 8. Sensitivity and validation

### 8.1 Sensitivity: the two dominant drivers

1. **Installed power and design speed**: three estimates (Section 2.2), carried in parallel with no primary.
2. **Speed-smoothing window**: the v³ bias runs from 1.67× unsmoothed to 1.19× at a 3-hour window.

The scenario space is a SQL `CROSS JOIN` of these against the hour-level facts. Remaining uncertainties: coverage correction, displacement convention, TEU estimation, engine-type assignment: are documented qualitatively rather than propagated.

### 8.2 Validation

| Check | Basis |
|---|---|
| **THETIS-MRV** | The vessel makes 35 EU port calls, so its **verified** annual CO₂ is published. This is genuine external ground truth and the strongest check available. |
| Hour conservation | Observed hours vs elapsed time, per year. Measured 99.98% for 2024. |
| Leg-speed plausibility | Great-circle distance between port calls ÷ leg duration should give sensible average speeds. |
| Port-call/track agreement | Berth periods in the track must coincide with port-visit events. Confirmed on 2024-01-15: the vessel is stationary until 13:00 and the port visit ends 13:32. |
| Identity integrity | Exactly one distinct IMO in every presence pull. |
| Fleet-envelope check | Estimated design speed must fall within the observed modern container fleet range (6.0–24.5 kn). Estimate A fails this test at 28.92 kn. |

THETIS-MRV is used **only** to validate, never as an input: it is EU-scope, and this study allocates emissions globally.

***

## Open items

| # | Item | Blocks |
|---|---|---|
| 2 | Selin et al. supplementary Table 1 (territory merging, Hong Kong treatment) | Section 5.4, Section 6.3 |
| 3 | Coastline layer for distance-to-coast | Section 4.1 |
| 4 | Sourced installed power and service speed per hull | Section 2.2 estimate C |
| 5 | Smoothing window validated on more than one day | Section 1.6, Section 8.1 |
| 6 | Joint-regime / overlapping-claim EEZ rule | Section 5.4 |
| 7 | Deliverable format; fleet-readiness vs pilot legibility | architecture |

***

## Sources

* Selin, H., Zhang, Y., Dunn, R., Selin, N.E., Lau, A.K.H. (2021). Mitigation of CO₂ emissions from international shipping through national allocation. *Environmental Research Letters* 16, 045009. doi:10.1088/1748-9326/abec02
* IMO (2020). *Fourth IMO Greenhouse Gas Study 2020*: Tables 10, 16, 17, 19, 20, 21; equations 10 and 11.
* IMO (2021). *2021 Guidelines on the method of calculation of the attained Energy Efficiency Existing Ship Index (EEXI)*, Resolution MEPC.333(76), adopted 17 June 2021: paragraph 2.2.3.5 and Appendix, Tables of parameters for `V_ref,avg` and `MCR_avg`. Primary source; consulted directly. https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.333(76).pdf
* Sun, R., Abouarghoub, W., Demir, E., Potter, A. (2026). Impact of imputation methods for ship technical parameters on emission estimations in ports. *Maritime Policy & Management* 53(1), 70–92.
* Cepowski, T., Chorab, P. (2021). Determination of design formulas for container ships at the preliminary design stage. *Ocean Engineering* 238, 109727.
* Charchalis, A. (2014). Determination of main dimensions and estimation of propulsion power of a ship. *Journal of KONES* 21(2), 39–44.
* Global Fishing Watch APIs v3: presence, events, vessels, insights.
* Flanders Marine Institute: World EEZ v12, World High Seas v2, MARPOL Annex VI ECAs. CC-BY 4.0.
* Friedlingstein et al. (2025). *Global Carbon Budget 2025*, ESSD: National Fossil Carbon Emissions v2025.
* UNdata, UN Energy Statistics Database: international marine bunkers (fleet-scale use only).
* Equasis: flag, tonnage, ownership and management records.

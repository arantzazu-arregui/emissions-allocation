# Data sources, substitutions, and handling

This document records the data used by the open-data reconstruction described
in [the final paper](FinalPaper_ArantzazuArreguiGonzalez.pdf), especially the
source substitutions made for the commercial data used by Selin et al. (2021).
It applies to the two-vessel pilot for 2017-01-01 through 2024-12-31 and should
be updated whenever a source version, retrieval date, or configuration changes.

## Source strategy

Selin et al. (2021) combined commercial AIS activity data with the World
Register of Shipping / Sea-web and International Energy Agency bunker data. The
first two inputs are not openly reproducible at vessel level; the IEA bunker
series is not used for the two-vessel calculation. This project therefore uses
the public or freely accessible substitutes below, while preserving the loss of
coverage or precision as an explicit limitation.

| Need | Original Selin et al. input | This project’s input | Principal limitation |
|---|---|---|---|
| Vessel activity, position, and port calls | S&P Global / AISLive / Sea-web AIS | Global Fishing Watch (GFW) APIs | GFW returns hourly grid-cell presence rather than transmitted AIS positions, speed, or draught. |
| Vessel specifications and allocation roles | World Register of Shipping / Sea-web | Equasis and public ship-register information | No bulk/API access; installed power and reference speed are not reliably available; Equasis has no operator field. |
| Operating and emission-model constants | Fourth IMO GHG Study | Fourth IMO GHG Study 2020 and IMO EEXI guidelines | Published tables support the model but are not ship-level activity data. |
| Maritime spatial context | Marine Regions / VLIZ | Marine Regions / VLIZ | No substitution; layer version and geometry must be recorded. |
| Bunker-fuel country allocation | IEA energy and CO₂ data | UNdata Energy Statistics for a future fleet-scale calculation | National fuel deliveries cannot identify fuel taken by one vessel. |
| National carbon-budget baseline | Global Carbon Budget 2018 | Global Carbon Budget 2025 | Edition changes must be stated when comparing results. |
| Territory alignment | Selin et al. supplementary Table 1 | Selin et al. supplementary Table 1 | A fixed replication convention, not an independently estimated geography. |

## Active inputs

### Global Fishing Watch — activity, identity, and port visits

GFW is the primary open replacement for proprietary AIS activity data. A free
non-commercial `GFW_TOKEN` is required for live requests and is stored locally
in `.env`, never committed.

| Endpoint or product | Parameters retained | Use in the pipeline | Important limitation |
|---|---|---|---|
| 4Wings presence report, `public-global-presence` | Hourly latitude/longitude cell, credited hours, IMO, ship name, flag, and type | Creates the hourly activity spine; derives and smooths speed over ground; measures coverage and classifies gaps | Records are cell centroids at 0.01° resolution, not original AIS positions or transmitted SOG. |
| Events API, `public-global-port-visits-events` | Visit start/end, anchorage ID and coordinates, port ISO3, dock flag, confidence, and duration | Builds voyage legs; labels domestic, international, and EU-to-EU legs; supports at-berth and inactive-gap rules | Port visits are inferred events rather than raw-AIS detections. |
| Vessels identity endpoint | GFW `vesselId` values associated with an IMO and historical identity information | Retrieves port calls for associated IDs and reconstructs exact historical ship-name filters | The `vesselId` is provider-specific and is never the primary model key. |

Presence requests use a near-global polygon, high spatial resolution, hourly
temporal resolution, and vessel-ID grouping. Ship-name filters are exact and
case-sensitive; all known historical names are configured. Returned records are
retained only after their non-null IMO matches the configured valid seven-digit
IMO number. The IMO number—not name, MMSI, flag, or GFW identifier—is the
stable join key.

Raw GFW responses are cached under `data/raw/gfw_cache/`, keyed by request, so
partial runs can resume. Captured investigation responses in `data/sample/api/`
are tracked as API-behaviour evidence. GFW permits only one concurrent 4Wings
report; activity retrieval is therefore sequential.

Observe the [GFW API terms](https://globalfishingwatch.org/our-apis/documentation#terms-of-use)
when using or redistributing activity data.

### Equasis and public vessel-register information — specifications and roles

Equasis is the main open replacement for Sea-web / the World Register of
Shipping. It supplies vessel flag, gross and deadweight tonnage, ship type,
build year, registered owner, ISM manager, commercial manager, company
identifiers, and company-address information. Public vessel-register sources
may supplement dimensions needed by a power-estimation method. Information is
recorded per IMO in `config/vessel_specs.yaml`, with provenance.

Equasis has no operator field, so the commercial manager is used as a clearly
labelled operator proxy. It provides company addresses rather than a definitive
country of incorporation, so the address is used to derive the country key. It
does not provide a public bulk-download or programming interface, and it does
not consistently provide vessel-specific installed main-engine power or
reference speed. Those two missing fields are the dominant uncertainty in this
pilot and must be obtained from an authoritative source before fleet-scale use.

### IMO sources — physical-model and estimation parameters

The [Fourth IMO Greenhouse Gas Study 2020](https://www.imo.org/en/ourwork/environment/pages/fourth-imogreenhousegasstudy2020.aspx)
provides the published constants used by the emissions model. The repository
transcribes the relevant values to `config/emission_factors.yaml`:

- Table 16: operating-mode matrix;
- Table 17: auxiliary-engine and auxiliary-boiler power;
- Table 19: base specific fuel consumption;
- Table 20: low-load adjustment factors; and
- Table 21: fuel-based CO₂ emission factors.

Equations 10 and 11 of the study are also used. Tables and equations were
verified against rendered report pages because their text extraction is not
reliable; printed and PDF page numbers are retained in the configuration.

[Resolution MEPC.333(76)](https://www.imo.org/en/ourwork/environment/pages/technical-and-operational-measures.aspx),
the 2021 EEXI calculation guideline, supplies one documented method for
estimating reference speed and installed power when vessel-specific values are
not observed. It is an estimation method, not an activity dataset. The project
also records the external methods used for sensitivity estimates in vessel
parameter provenance.

### Marine Regions / VLIZ — spatial inputs

Marine Regions layers are open geospatial inputs. Layer paths and names are
explicit in `config/pilot.yaml` to prevent accidental substitution of a
boundary layer for a polygon layer.

| Layer | Version used | Pipeline use |
|---|---|---|
| World EEZ | v12 | Assigns sovereign EEZ context and supports the whole-vessel domestic diagnostic. Allocation itself is based on voyage legs, not EEZs. |
| World High Seas | v2 | Provides a not-in-any-EEZ reference; unmatched EEZ hours are treated as high seas. |
| MARPOL Annex VI Regulation 14 emission-control areas | Current project layer: six polygons | Assigns distillate or residual fuel by location. The Mediterranean SOx ECA, effective after the study period, is not part of this 2017–2024 treatment. |
| Marine and Land Zones, `EEZ_land_union` | v4 | Supplies the combined land-and-zone geometry from which the coastline proxy is derived for the operating-mode matrix. |

The World EEZ download includes polygon (`eez_v12.gpkg`) and boundary
(`eez_boundaries_v12.gpkg`) GeoPackages. Only the polygon layer is suitable for
point-in-polygon joins. Marine and Land Zones is not a coastline layer: it is
land merged with EEZs, so the implementation recovers land by differencing it
against World EEZ v12.

### Global Carbon Budget 2025 — national baselines

The project uses *National Fossil Carbon Emissions v2025*, `Territorial
Emissions` worksheet, with the header at row index 11. Values are in MtC and
are multiplied by 3.664 to obtain MtCO₂. National columns exclude bunker
emissions; only the World total includes them. The `Regions` worksheet provides
KP Annex B, OECD, and EU27 groupings and has no header row.

Selin et al. used the 2018 Global Carbon Budget edition. This pilot uses the
2025 edition to contextualise allocated shipping emissions against current
national territorial CO₂ baselines. A missing country baseline raises an error;
it is never treated as zero.

### Selin et al. (2021) supplementary Table 1 — territory alignment

`data/external/paper/erlabec02supp2.xls` is the paper’s published country-level
result table for all five allocation options. It defines the replication
territory convention applied in `config/pilot.yaml`: Hong Kong is aligned with
China because the table has no Hong Kong row; Chinese Taipei remains aligned
with Taiwan; and the Isle of Man is aligned with the United Kingdom. This is a
fixed replication rule, not a sensitivity axis.

### UNdata Energy Statistics — future bunker-fuel option

UNdata’s Energy Statistics database provides country-level international marine
bunker deliveries for fuel oil and gas oil/diesel oil. The reported unit is
`Metric tons, thousand`, so values must be multiplied by 1,000 before applying
fuel emission factors. The parser stops at the trailing footnote block after the
blank line.

These statistics are not part of the two-vessel calculation. The fifth Selin et
al. allocation option—country of bunker-fuel sale—is a fleet-scale construct:
national deliveries cannot identify where one vessel purchased fuel. A future
fleet calculation can derive country-level CO₂ from the UNdata fuel flows and
the Fourth IMO GHG Study emission factors.

## Data handling and reproducibility

- `data/external/` contains downloaded source inputs and is read-only; it is
  never regenerated by the pipeline.
- `data/raw/` contains API responses, `data/interim/` restartable checkpoints,
  and `data/out/` derived outputs. These are ignored by Git.
- `data/sample/` is retained in Git as investigation evidence, not as a
  production input.
- For every run, record API dataset version, retrieval date, date range,
  spatial and temporal resolution, vessel filters, region definition, input
  layer versions, configuration values, and code commit SHA.
- Retain the scenario identifier and validation output with every emissions,
  allocation, and impact result. Do not collapse uncertainty in installed
  power, reference speed, or speed smoothing to an unlabelled point estimate.

## References

- Equasis. (2026). *Equasis: Ship information database*. European Maritime
  Safety Agency. https://www.equasis.org/
- Flanders Marine Institute. (2020). *Emission control areas designated under
  Regulation 14 of MARPOL Annex VI*. https://doi.org/10.14284/397
- Flanders Marine Institute. (2023). *Maritime boundaries geodatabase:
  Maritime boundaries and exclusive economic zones (200NM), version 12*.
  https://doi.org/10.14284/632
- Flanders Marine Institute. (2024). *Union of the ESRI country shapefile and
  the exclusive economic zones, version 4*. https://doi.org/10.14284/698
- Global Carbon Project. (2025). *The latest GCB data (2025)*.
  https://globalcarbonbudget.org/datahub/the-latest-gcb-data-2025/
- Global Fishing Watch. (2025). *Global Fishing Watch APIs: 4Wings report and
  events endpoints*. https://globalfishingwatch.org/our-apis/
- International Maritime Organization. (2020). *Fourth IMO greenhouse gas
  study 2020: Full report*.
- International Maritime Organization. (2021). *2021 guidelines on the method
  of calculation of the attained Energy Efficiency Existing Ship Index (EEXI)*,
  Resolution MEPC.333(76).
- Selin, H., Zhang, Y., Dunn, R., Selin, N. E., & Lau, A. K. H. (2021).
  Mitigation of CO₂ emissions from international shipping through national
  allocation. *Environmental Research Letters, 16*(4), 045009.
  https://doi.org/10.1088/1748-9326/abec02
- United Nations Statistics Division. (2025). *UNdata: Energy statistics
  database*. https://data.un.org/

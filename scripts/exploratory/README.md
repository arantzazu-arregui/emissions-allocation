# Exploratory probes — provenance, not pipeline

Nothing in this directory is a pipeline step. These four scripts were run against the live Global
Fishing Watch API during investigation, and they produced the raw responses captured in
`data/sample/api/` (root, `round2/`, `round3/`, `round4/`).

They are retained because **the API behaviour asserted in `CLAUDE.md` is sourced from their output**.
Deleting them would leave those claims unsupported — several cost a round-trip to establish, and
several describe *silent* failure modes that are expensive to rediscover.

| Script | Output | Established |
|---|---|---|
| `dump_api_samples.py` | `data/sample/api/*.json` | Response shapes for vessel search, vessel detail, port-visit events, and hourly presence. First evidence that `filters[1]` is silently dropped. |
| `probe_vessel_scope.py` | `data/sample/api/round2/` | A world-extent polygon is accepted (14,489 records, 991 vessels for one day). `shipname` is the **only** identity field that can scope a presence report — `vessel_id`, `imo`, `ssvid` and `mmsi` all fail. The speed filter binds identically at DAILY and HOURLY. |
| `probe_vessel_groups.py` | `data/sample/api/round3/` | A free token **cannot** create vessel groups (403 on three body shapes). `shipname` matching is exact and case-sensitive — a wrong name returns HTTP 200 with a null dataset value and no error. There is no name-history array; it must be reconstructed from `registryInfo` + `selfReportedInfo`. |
| `probe_scale_and_coverage.py` | `data/sample/api/round4/` | A full year returns in ~44 s, 8,782 records, no pagination. 389 port calls 2017–2024, all confidence 4. The Insights coverage endpoint exists but only from 2020-01-01. |

## Two cautions if you re-run them

**They call the live API.** Quota is roughly 50,000 requests/day with **one** concurrent 4Wings
report; a second concurrent report returns 429. They need `GFW_TOKEN` in the environment.

**`round4/` files are not raw responses.** That script post-processed what it received:
`A_port_visits_2017_2024.json` is the concatenation of four pages with the envelope discarded,
`C_year_sample_records.json` is flattened, and the `D*.json` files wrap the real body under a
`{"status": …, "body": …}` key. The root, `round2/` and `round3/` files *are* verbatim bodies —
except `round2/A1_world_narrow_filter.json`, whose `entries` the script replaced with a placeholder
string because the payload exceeded its 400,000-character write limit.

Read `data/sample/api/*/\_summary.txt` alongside the JSON; the summaries record which request
produced which file and what the HTTP status was.

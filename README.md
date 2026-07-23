# emissions-allocation

Independent study replicating the methodology of [Selin et al. (2021)](https://doi.org/10.1088/1748-9326/abec02) on allocating international shipping CO2 emissions to countries. Compares allocation options (flag state, EEZ/territory, port state, and others) and their implications for UNFCCC vs IMO governance.

## Data sources

- **AIS vessel activity:** [Global Fishing Watch APIs](https://globalfishingwatch.org/our-apis/) — `public-global-presence:latest` dataset (all vessel types, gridded presence hours). Requires a free non-commercial API token.
- **Ship-level CO2 (planned):** [EU THETIS-MRV](https://mrv.emsa.europa.eu/) verified annual emissions reports.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set your Global Fishing Watch API token (get one at [gateway.api.globalfishingwatch.org](https://globalfishingwatch.org/our-apis/)):

```powershell
# current session only
$env:GFW_TOKEN = "your-token"

# or permanently (new terminals)
[Environment]::SetEnvironmentVariable("GFW_TOKEN", "your-token", "User")
```

The token is read from the `GFW_TOKEN` environment variable only — never commit it.

## Usage

The pipeline has two steps — fetch (REST API → Parquet) and analyze (SQL via DuckDB):

```powershell
python fetch_presence.py      # pulls grid cells per region & vessel class -> data/raw/*.parquet
python analyze_presence.py    # runs SQL queries -> data/out/*.csv + printed tables
```

Configure regions, date range, and vessel classes at the top of `fetch_presence.py`. Analysis queries live as plain SQL in `analyze_presence.py` — add new ones to the `QUERIES` dict.

Current queries: flag-state shares of presence hours, vessel class composition, flag × class matrix, and region totals (baseline for EEZ-based allocation comparisons).

## Attribution

AIS-derived data provided by [Global Fishing Watch](https://globalfishingwatch.org/), used under their [API Terms of Use](https://globalfishingwatch.org/our-apis/documentation#terms-of-use).

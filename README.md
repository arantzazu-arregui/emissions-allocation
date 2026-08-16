# Emissions allocation

An independent study replicating [Selin et al. (2021)](https://doi.org/10.1088/1748-9326/abec02) on allocating international-shipping CO2 emissions to countries. It compares flag-state, EEZ/territorial, port-state, and related allocation approaches.

## Repository layout

```text
config/       Versioned configuration notes and environment template
data/         Local data (raw, interim, processed, outputs); ignored by Git
  sample/     Shareable, tracked example input
docs/         Methodology and data documentation
notebooks/    Exploratory and presentation-ready analyses
reports/      Generated figures and written results
scripts/      Pipeline entry points
src/          Reusable Python modules
tests/        Automated checks
```

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
$env:GFW_TOKEN = "your-token"
```

Get a free non-commercial token from the [Global Fishing Watch API](https://globalfishingwatch.org/our-apis/). Do not commit it.

## Run the pipeline

Configure regions, dates, and vessel classes near the top of `scripts/fetch_presence.py`, then run:

```powershell
python scripts/fetch_presence.py
python scripts/analyze_presence.py
```

These commands write API inputs to `data/raw/` and derived tables to `data/out/`. Regenerate the shareable example workbook with:

```powershell
python scripts/make_sample.py
```

## Data sources

- AIS activity: [Global Fishing Watch APIs](https://globalfishingwatch.org/our-apis/), `public-global-presence:latest`.
- Ship-level CO2 (planned): [EU THETIS-MRV](https://mrv.emsa.europa.eu/) verified annual emissions reports.

See [docs/data_sources.md](docs/data_sources.md) for handling and reproducibility expectations.

## Development checks

```powershell
python -m compileall scripts
pytest
```

## Attribution

AIS-derived data is provided by [Global Fishing Watch](https://globalfishingwatch.org/) and used under its [API Terms of Use](https://globalfishingwatch.org/our-apis/documentation#terms-of-use).

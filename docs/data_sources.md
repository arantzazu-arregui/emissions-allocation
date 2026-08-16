# Data sources and handling

## Global Fishing Watch AIS presence

The fetch pipeline requests gridded presence hours from the Global Fishing Watch 4Wings API. Responses are saved as Parquet under `data/raw/` and excluded from version control because they are reproducible API outputs and may be large.

The tracked `data/sample/gfw_data_sample.xlsx` workbook is a small, shareable example generated from the same source. Observe the Global Fishing Watch API terms when redistributing or extending data products.

## EU THETIS-MRV

Verified ship-level annual CO2 reports are a planned input for converting activity metrics into emissions allocations. Document the exact download date, filters, and transformations when this source is introduced.

## Reproducibility record

For every analysis run, record the API dataset version, date range, spatial and temporal resolution, vessel filters, region definition, retrieval date, and code commit SHA in the relevant notebook or report.

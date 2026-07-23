"""
Starter script: pull a sample of AIS vessel presence data from Global Fishing Watch.

Test region: English Channel / southern North Sea (busy shipping lane).
Output: vessel presence hours grouped by flag state, saved to CSV.

Setup:
    pip install requests pandas
    set GFW_TOKEN environment variable with your API token:
        PowerShell:  $env:GFW_TOKEN = "eyJhbGci..."
        or permanently: [Environment]::SetEnvironmentVariable("GFW_TOKEN", "eyJhbGci...", "User")

Run:
    python gfw_presence_sample.py

API reference: https://globalfishingwatch.org/our-apis/documentation
Dataset: public-global-presence:latest (AIS vessel presence, all vessel types)
"""

import os
import sys

import pandas as pd
import requests

API_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"

# ---- Configuration -------------------------------------------------------

DATE_RANGE = "2024-01-01,2024-01-31"  # one month sample

# Test region: English Channel / southern North Sea bounding box
TEST_REGION_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[
        [-2.0, 49.0],
        [5.0, 49.0],
        [5.0, 52.5],
        [-2.0, 52.5],
        [-2.0, 49.0],
    ]],
}

# Restrict to commercial shipping (per GFW docs example). Run query 1 first to
# see all vessel_type values present in the region, then adjust as needed.
VESSEL_TYPE_FILTER = 'vessel_type in ("cargo","carrier")'

OUTPUT_CSV = "presence_hours_by_flag.csv"

# --------------------------------------------------------------------------


def get_token() -> str:
    token = os.environ.get("GFW_TOKEN")
    if not token:
        sys.exit(
            "GFW_TOKEN environment variable not set.\n"
            'PowerShell: $env:GFW_TOKEN = "your-token-here"'
        )
    return token


def run_report(token: str, group_by: str, filters: str | None = None) -> list[dict]:
    """POST a 4Wings report request and return the list of grid-cell records."""
    params = {
        "spatial-resolution": "LOW",       # 0.1 degree grid
        "temporal-resolution": "ENTIRE",   # aggregate over the whole date range
        "group-by": group_by,              # FLAG, VESSEL_TYPE, MMSI, ...
        "datasets[0]": "public-global-presence:latest",
        "date-range": DATE_RANGE,
        "format": "JSON",
    }
    if filters:
        params["filters[0]"] = filters

    resp = requests.post(
        API_URL,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        json={"geojson": TEST_REGION_GEOJSON},
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()

    # Response: {"entries": [{"public-global-presence:vX.Y": [ {cell}, ... ]}], ...}
    records = []
    for entry in payload.get("entries", []):
        for dataset_key, cells in entry.items():
            if cells:
                records.extend(cells)
    return records


def main() -> None:
    token = get_token()

    # -- Query 1: hours by vessel type (no filter) -- shows what's in the region
    print(f"Querying vessel presence by VESSEL_TYPE, {DATE_RANGE} ...")
    by_type = run_report(token, group_by="VESSEL_TYPE")
    if by_type:
        df_type = (
            pd.DataFrame(by_type)
            .groupby("vessel_type", dropna=False)["hours"]
            .sum()
            .sort_values(ascending=False)
        )
        print("\nPresence hours by vessel type (test region):")
        print(df_type.to_string())
    else:
        print("No records returned for vessel type query.")

    # -- Query 2: hours by flag state, commercial shipping only
    print(f"\nQuerying vessel presence by FLAG ({VESSEL_TYPE_FILTER}) ...")
    by_flag = run_report(token, group_by="FLAG", filters=VESSEL_TYPE_FILTER)
    if not by_flag:
        sys.exit("No records returned for flag query.")

    df = pd.DataFrame(by_flag)
    summary = (
        df.groupby("flag", dropna=False)
        .agg(hours=("hours", "sum"), grid_cells=("hours", "size"))
        .sort_values("hours", ascending=False)
        .reset_index()
    )
    summary["share_pct"] = 100 * summary["hours"] / summary["hours"].sum()

    print(f"\nTop 15 flag states by presence hours ({DATE_RANGE}, test region):")
    print(summary.head(15).to_string(index=False))

    summary.to_csv(OUTPUT_CSV, index=False)
    print(f"\nFull table saved to {OUTPUT_CSV} ({len(summary)} flags).")

    # The raw per-grid-cell data (df) has lat/lon per record -- this is what
    # you will later intersect with EEZ boundaries for spatial allocation.


if __name__ == "__main__":
    main()

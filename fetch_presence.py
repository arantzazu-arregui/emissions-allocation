"""
Fetch step: pull AIS vessel presence grid cells from the Global Fishing Watch
4Wings API and save them as Parquet for SQL analysis with DuckDB.

Each run fetches presence hours grouped by FLAG, once per vessel class in
VESSEL_CLASSES plus one unfiltered pull ("all"), for every region in REGIONS.
Raw grid-cell records are appended with tag columns (vessel_class, region,
date_range) so all analysis dimensions live in one table.

Usage:
    python fetch_presence.py

Output:
    data/raw/presence_<region>_<daterange>.parquet

API reference: https://globalfishingwatch.org/our-apis/documentation
Dataset: public-global-presence:latest
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
RAW_DIR = Path("data/raw")

# ---- Configuration -------------------------------------------------------

DATE_RANGE = "2024-01-01,2024-01-31"

# Vessel classes to fetch separately (tagged in the output). "all" (no filter)
# is always fetched too. If a class name is invalid the API rejects it and the
# script warns and moves on -- so it's safe to experiment.
VESSEL_CLASSES = ["cargo", "carrier", "tanker", "passenger"]

# Regions to fetch. Two kinds of entries:
#   {"geojson": <GeoJSON polygon>}                    -- custom bounding box
#   {"eez": <region id>}                              -- official EEZ polygon
# EEZ region ids: see "Reference data" in the GFW API docs
# (https://globalfishingwatch.org/our-apis/documentation#reference-data)
REGIONS = {
    "english_channel": {
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [-2.0, 49.0],
                [5.0, 49.0],
                [5.0, 52.5],
                [-2.0, 52.5],
                [-2.0, 49.0],
            ]],
        }
    },
    # "nld_eez": {"eez": 5696},   # example: enable after looking up the id
}

# --------------------------------------------------------------------------


def get_token() -> str:
    token = os.environ.get("GFW_TOKEN")
    if not token:
        sys.exit(
            "GFW_TOKEN environment variable not set.\n"
            'PowerShell: $env:GFW_TOKEN = "your-token-here"'
        )
    return token


def fetch_cells(
    token: str, region_spec: dict, vessel_class: str | None
) -> list[dict]:
    """One 4Wings report request; returns raw grid-cell records."""
    params = {
        "spatial-resolution": "LOW",       # 0.1 degree grid
        "temporal-resolution": "ENTIRE",   # aggregate over the whole date range
        "group-by": "FLAG",                # presence supports MMSI/VESSEL_ID/FLAG
        "datasets[0]": "public-global-presence:latest",
        "date-range": DATE_RANGE,
        "format": "JSON",
    }
    if vessel_class:
        params["filters[0]"] = f'vessel_type in ("{vessel_class}")'

    if "geojson" in region_spec:
        body = {"geojson": region_spec["geojson"]}
    else:
        body = {"region": {"dataset": "public-eez-areas", "id": region_spec["eez"]}}

    resp = requests.post(
        API_URL,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=300,
    )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    records = []
    for entry in resp.json().get("entries", []):
        for _dataset_key, cells in entry.items():
            if cells:
                records.extend(cells)
    return records


def main() -> None:
    token = get_token()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for region_name, region_spec in REGIONS.items():
        frames = []
        for vessel_class in [None] + VESSEL_CLASSES:
            label = vessel_class or "all"
            print(f"Fetching {region_name} / {label} / {DATE_RANGE} ...", end=" ")
            try:
                cells = fetch_cells(token, region_spec, vessel_class)
            except RuntimeError as exc:
                print(f"SKIPPED ({exc})")
                continue
            print(f"{len(cells)} cells")
            if not cells:
                continue
            df = pd.DataFrame(cells)
            df["vessel_class"] = label
            df["region"] = region_name
            df["date_range"] = DATE_RANGE
            df["fetched_at"] = fetched_at
            frames.append(df)

        if not frames:
            print(f"No data for region {region_name}, nothing written.")
            continue

        out = RAW_DIR / f"presence_{region_name}_{DATE_RANGE.replace(',', '_')}.parquet"
        pd.concat(frames, ignore_index=True).to_parquet(out, index=False)
        print(f"Wrote {out}\n")

    print("Done. Next: python analyze_presence.py")


if __name__ == "__main__":
    main()

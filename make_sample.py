"""
Generate a small, shareable sample of the GFW data available to this project:

1. Presence dataset (public-global-presence via 4Wings API):
   1 position/vessel/hour, all vessel types, global coverage, speed bins.
   Sampled here as per-vessel daily grid cells with hours per speed bin.
2. Vessels API: identity (MMSI, name, call sign, type, IMO where matched
   to registries) for the most active vessels in the sample.

Output: data/sample/gfw_data_sample.xlsx  (Overview, SpeedProfiles,
        VesselIdentity, TrackCellsSample sheets)

Usage:
    python make_sample.py
"""

from pathlib import Path

import pandas as pd
import requests

from fetch_presence import TRACK_REGIONS, TRACK_VESSEL_FILTER, _report_request, get_token

SAMPLE_DIR = Path("data/sample")
SAMPLE_XLSX = SAMPLE_DIR / "gfw_data_sample.xlsx"

# Small sample: one region, one week
SAMPLE_REGION_NAME = "us_socal"
SAMPLE_DATE_RANGE = "2024-01-01,2024-01-07"
SPEED_BINS = ["<2", "2-4", "4-6", "6-10", "10-15", "15-25", ">25"]
N_VESSELS_IDENTITY = 10

VESSEL_SEARCH_URL = "https://gateway.api.globalfishingwatch.org/v3/vessels/search"


def fetch_bin(token: str, region_spec: dict, speed_bin: str | None) -> list[dict]:
    params = {
        "spatial-resolution": "HIGH",
        "temporal-resolution": "DAILY",
        "group-by": "VESSEL_ID",
        "datasets[0]": "public-global-presence:latest",
        "date-range": SAMPLE_DATE_RANGE,
        "format": "JSON",
        "filters[0]": TRACK_VESSEL_FILTER,
    }
    if speed_bin:
        params["filters[1]"] = f'speed in ("{speed_bin}")'
    return _report_request(token, params, region_spec)


def fetch_identity(token: str, mmsi: str) -> dict:
    """Look up one vessel by MMSI in the Vessels API; flatten key fields."""
    resp = requests.get(
        VESSEL_SEARCH_URL,
        params={
            "query": mmsi,
            "datasets[0]": "public-global-vessel-identity:latest",
            "limit": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    row = {"mmsi": mmsi}
    if not resp.ok:
        row["lookup_error"] = f"HTTP {resp.status_code}"
        return row
    entries = resp.json().get("entries", [])
    if not entries:
        row["lookup_error"] = "not found"
        return row
    e = entries[0]
    self_rep = (e.get("selfReportedInfo") or [{}])[0]
    registry = (e.get("registryInfo") or [{}])[0]
    row.update(
        ship_name=self_rep.get("shipname") or registry.get("shipname"),
        callsign=self_rep.get("callsign") or registry.get("callsign"),
        flag=self_rep.get("flag") or registry.get("flag"),
        imo=self_rep.get("imo") or registry.get("imo"),
        vessel_type=registry.get("vesselType") or self_rep.get("shiptype"),
        registry_records=e.get("registryInfoTotalRecords", 0),
        gfw_vessel_id=self_rep.get("id"),
    )
    return row


def main() -> None:
    token = get_token()
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    region_spec = TRACK_REGIONS[SAMPLE_REGION_NAME]

    # -- 1. Presence sample, split by speed bin ---------------------------
    frames = []
    for speed_bin in SPEED_BINS:
        print(f"Fetching presence cells, speed {speed_bin} kn ...", end=" ")
        try:
            cells = fetch_bin(token, region_spec, speed_bin)
        except RuntimeError as exc:
            print(f"SKIPPED ({exc})")
            continue
        print(f"{len(cells)} records")
        if cells:
            df = pd.DataFrame(cells)
            df["speed_bin"] = speed_bin
            frames.append(df)

    if not frames:
        print("All speed-bin queries failed; falling back to unbinned fetch.")
        cells = fetch_bin(token, region_spec, None)
        df = pd.DataFrame(cells)
        df["speed_bin"] = "unbinned"
        frames.append(df)

    cells_df = pd.concat(frames, ignore_index=True)
    cells_df["region"] = SAMPLE_REGION_NAME
    cells_df["date_range"] = SAMPLE_DATE_RANGE

    # -- 2. Speed profile per vessel (input table for the power law) ------
    profile = (
        cells_df.groupby(
            ["vesselId", "shipName", "imo", "mmsi", "flag", "speed_bin"],
            dropna=False,
        )["hours"]
        .sum()
        .unstack("speed_bin", fill_value=0)
        .reset_index()
    )
    bin_cols = [c for c in profile.columns if c in SPEED_BINS + ["unbinned"]]
    profile["total_hours"] = profile[bin_cols].sum(axis=1)
    profile = profile.sort_values("total_hours", ascending=False)

    # -- 3. Vessels API identity for the most active vessels --------------
    top_mmsi = (
        profile.dropna(subset=["mmsi"]).head(N_VESSELS_IDENTITY)["mmsi"].tolist()
    )
    print(f"\nLooking up {len(top_mmsi)} vessels in the Vessels API ...")
    identity_df = pd.DataFrame([fetch_identity(token, m) for m in top_mmsi])

    # -- 4. Write the sample workbook -------------------------------------
    overview = pd.DataFrame(
        {
            "item": [
                "Project",
                "Data source 1",
                "  granularity",
                "  coverage",
                "  speed",
                "Data source 2",
                "  fields",
                "Sample region",
                "Sample period",
                "Sheets",
                "Note",
            ],
            "description": [
                "National allocation of international shipping CO2 emissions "
                "(replication of Selin et al. 2021)",
                "GFW presence dataset (public-global-presence, 4Wings API)",
                "1 position/vessel/hour, aggregated to daily 0.01-deg grid cells "
                "per vessel",
                "Global, all vessel types (sample filtered to cargo/carrier/"
                "tanker/passenger)",
                "Hours per speed bin (<2, 2-4, 4-6, 6-10, 10-15, 15-25, >25 kn) "
                "-> input to power-law emissions calc",
                "GFW Vessels API (public-global-vessel-identity)",
                "MMSI, name, call sign, flag, type, IMO where matched to "
                "registries",
                f"{SAMPLE_REGION_NAME} (LA/Long Beach approaches; dataset itself "
                "is global)",
                SAMPLE_DATE_RANGE,
                "SpeedProfiles = hours by speed bin per vessel; VesselIdentity = "
                "registry lookup of top vessels; TrackCellsSample = raw records",
                "Free, non-commercial API. Attribution: Global Fishing Watch.",
            ],
        }
    )

    with pd.ExcelWriter(SAMPLE_XLSX, engine="openpyxl") as xl:
        overview.to_excel(xl, sheet_name="Overview", index=False)
        profile.head(50).to_excel(xl, sheet_name="SpeedProfiles", index=False)
        identity_df.to_excel(xl, sheet_name="VesselIdentity", index=False)
        cells_df.head(500).to_excel(xl, sheet_name="TrackCellsSample", index=False)

    print(f"\nSample written to {SAMPLE_XLSX}")
    print(f"  {len(profile)} vessels, {len(cells_df)} cell records, "
          f"{len(identity_df)} identity lookups")


if __name__ == "__main__":
    main()

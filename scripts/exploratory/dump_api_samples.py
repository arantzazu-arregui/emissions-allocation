"""
Dump raw, unmodified Global Fishing Watch API responses so the pipeline can be
coded against real schemas instead of guessed ones.

Nothing here is part of the pipeline. It makes a handful of small calls and
writes whatever comes back -- including error bodies, which are often more
informative than successes because GFW's 400 responses usually name the valid
field values.

Usage (PowerShell, from the repo root):
    $env:GFW_TOKEN = "your-token"
    python scripts/dump_api_samples.py

Output: data/sample/api/*.json  plus  data/sample/api/_summary.txt
Runtime: about a minute. Well under any quota.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://gateway.api.globalfishingwatch.org/v3"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "data" / "sample" / "api"

# A large container ship with a valid IMO, confirmed present in GFW data.
# Change if you prefer a different test subject -- any IMO-registered cargo
# vessel works.
TEST_IMO = "9516454"          # COSCO ITALY
TEST_START = "2024-01-01"
TEST_END = "2024-02-01"

# Small box off Los Angeles / Long Beach: dense traffic, small response.
TEST_BOX = {
    "type": "Polygon",
    "coordinates": [[
        [-118.6, 33.4], [-118.0, 33.4], [-118.0, 33.9], [-118.6, 33.9], [-118.6, 33.4],
    ]],
}

# The port-visit dataset name is not documented publicly. Try each until one
# returns 200; the summary records which.
PORT_VISIT_DATASETS = [
    "public-global-port-visits-events:latest",
    "public-global-port-visits-c2-events:latest",
    "public-global-port-visits:latest",
]

# Three candidate syntaxes for combining a vessel-type and a speed filter.
# Exactly one of these (or none) actually binds -- that is what we are testing.
SPEED_FILTER_VARIANTS = {
    "a_speed_only": {"filters[0]": 'speed in ("10-15")'},
    "b_combined_and": {"filters[0]": 'vessel_type in ("cargo") AND speed in ("10-15")'},
    "c_separate_params": {
        "filters[0]": 'vessel_type in ("cargo")',
        "filters[1]": 'speed in ("10-15")',
    },
}

results: list[str] = []


def token() -> str:
    t = os.environ.get("GFW_TOKEN")
    if not t:
        sys.exit('GFW_TOKEN not set.  PowerShell:  $env:GFW_TOKEN = "your-token"')
    return t


def dump(name: str, resp: requests.Response, note: str = "") -> dict | None:
    """Write the response body verbatim; return parsed JSON on success."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    try:
        body = resp.json()
        path.write_text(json.dumps(body, indent=2)[:2_000_000], encoding="utf-8")
    except ValueError:
        body = None
        path.write_text(resp.text[:200_000], encoding="utf-8")

    ok = "OK " if resp.ok else "ERR"
    line = f"[{ok}] {resp.status_code}  {name}.json  {note}".rstrip()
    print(line)
    results.append(line)
    return body if resp.ok else None


def get(path: str, params: dict, tok: str) -> requests.Response:
    return requests.get(
        f"{BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {tok}"},
        timeout=120,
    )


def report(params: dict, tok: str) -> requests.Response:
    return requests.post(
        f"{BASE}/4wings/report",
        params=params,
        headers={"Authorization": f"Bearer {tok}"},
        json={"geojson": TEST_BOX},
        timeout=300,
    )


def main() -> None:
    tok = token()
    print(f"Writing to {OUT_DIR}\n")

    # -- 1. Find the test vessel -----------------------------------------
    body = dump(
        "01_vessel_search",
        get(
            "/vessels/search",
            {
                "query": TEST_IMO,
                "datasets[0]": "public-global-vessel-identity:latest",
                "includes[0]": "OWNERSHIP",
                "limit": 5,
            },
            tok,
        ),
        f"search IMO {TEST_IMO}",
    )

    vessel_id = None
    if body:
        for entry in body.get("entries", []):
            vessel_id = entry.get("selfReportedInfo", [{}])[0].get("id") or entry.get("id")
            if vessel_id:
                break
    print(f"    vessel_id = {vessel_id}\n")
    results.append(f"    vessel_id = {vessel_id}")

    # -- 2. Full identity record -----------------------------------------
    if vessel_id:
        dump(
            "02_vessel_detail",
            get(
                f"/vessels/{vessel_id}",
                {
                    "dataset": "public-global-vessel-identity:latest",
                    "includes[0]": "OWNERSHIP",
                    "includes[1]": "AUTHORIZATIONS",
                },
                tok,
            ),
            "registryInfo / selfReportedInfo / combinedSourcesInfo shape",
        )

    # -- 3. Port visit events (the field names I actually need) ----------
    if vessel_id:
        for ds in PORT_VISIT_DATASETS:
            slug = ds.split(":")[0].replace("public-global-", "")
            r = get(
                "/events",
                {
                    "vessels[0]": vessel_id,
                    "datasets[0]": ds,
                    "start-date": TEST_START,
                    "end-date": TEST_END,
                    "confidences[0]": 3,
                    "confidences[1]": 4,
                    "limit": 20,
                    "offset": 0,
                },
                tok,
            )
            if dump(f"03_events_{slug}", r, ds) is not None:
                break

    # -- 4. Hourly presence, one day, no speed filter --------------------
    base_params = {
        "spatial-resolution": "HIGH",
        "temporal-resolution": "HOURLY",
        "group-by": "VESSEL_ID",
        "datasets[0]": "public-global-presence:latest",
        "date-range": "2024-01-15,2024-01-16",
        "format": "JSON",
    }
    dump("04_presence_hourly", report(dict(base_params), tok), "baseline, unfiltered")

    # -- 5. Speed filter: which syntax binds? ----------------------------
    for label, extra in SPEED_FILTER_VARIANTS.items():
        dump(
            f"05_speed_{label}",
            report({**base_params, **extra}, tok),
            " | ".join(f"{k}={v}" for k, v in extra.items()),
        )

    # -- 6. Can presence be filtered to one vessel? ----------------------
    if vessel_id:
        dump(
            "06_presence_vessel_filter",
            report(
                {**base_params, "filters[0]": f"vessel_id in ('{vessel_id}')"},
                tok,
            ),
            "vessel_id filter on presence -- decides the fetch architecture",
        )

    # -- 7. World-extent polygon, vessel-filtered ------------------------
    if vessel_id:
        world = {
            "type": "Polygon",
            "coordinates": [[
                [-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85],
            ]],
        }
        r = requests.post(
            f"{BASE}/4wings/report",
            params={**base_params, "filters[0]": f"vessel_id in ('{vessel_id}')"},
            headers={"Authorization": f"Bearer {tok}"},
            json={"geojson": world},
            timeout=300,
        )
        dump("07_presence_world_extent", r, "world polygon -- 56-request path or not")

    summary = OUT_DIR / "_summary.txt"
    summary.write_text(
        f"GFW API sample dump  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"test IMO {TEST_IMO}, vessel_id {vessel_id}\n\n" + "\n".join(results) + "\n",
        encoding="utf-8",
    )
    print(f"\nSummary: {summary}")
    print("Send me the whole data/sample/api/ folder -- error bodies included.")


if __name__ == "__main__":
    main()

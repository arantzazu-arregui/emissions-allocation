"""
Round 2 probe: how do we restrict a presence report to ONE vessel, globally?

Round 1 settled the speed filter and the port-visit schema, but left the fetch
architecture open. Tests 06 and 07 both carried `vessel_id in (...)`, which the
backend rejects with a 503 (`Function with name VESSEL_UPPER does not exist`),
so the world-extent polygon was never actually tested on its own merits.

This probes four things, seven small calls, about a minute:

    A  Does a world-extent polygon work at all, with a filter GFW accepts?
    B  Is there a vessel-groups endpoint, and can a group filter a report?
    C  Does any other vessel_id syntax bind?
    D  Does the speed filter behave differently at DAILY vs HOURLY?
       (Round 1 worked at HOURLY; the old broken sample used DAILY.)

Usage:
    $env:GFW_TOKEN = "your-token"
    python scripts/probe_vessel_scope.py

Output: data/sample/api/round2/*.json + _summary.txt
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://gateway.api.globalfishingwatch.org/v3"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sample" / "api" / "round2"

VESSEL_ID = "103dbbea9-9221-5f81-a00f-657a515745bb"   # COSCO ITALY
TEST_IMO = "9516454"
TEST_MMSI = "477845600"
TEST_SHIPNAME = "COSCO ITALY"
VESSEL_FLAG = "HKG"
DAY = "2024-01-15,2024-01-16"

# NOTE: COSCO ITALY was in the SoCal box on 2024-01-15 (it was on a port visit
# to Long Beach 01-10 to 01-15), so a working identity filter should return a
# non-zero record count here, not an empty result. An OK response with recs=0
# means the filter parsed but matched nothing -- check the vessel's position
# before concluding the field works.

SOCAL_BOX = {
    "type": "Polygon",
    "coordinates": [[
        [-118.6, 33.4], [-118.0, 33.4], [-118.0, 33.9], [-118.6, 33.9], [-118.6, 33.4],
    ]],
}
WORLD_BOX = {
    "type": "Polygon",
    "coordinates": [[
        [-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85],
    ]],
}

BASE_PARAMS = {
    "spatial-resolution": "HIGH",
    "temporal-resolution": "HOURLY",
    "group-by": "VESSEL_ID",
    "datasets[0]": "public-global-presence:latest",
    "date-range": DAY,
    "format": "JSON",
}

log: list[str] = []


def tok() -> str:
    t = os.environ.get("GFW_TOKEN")
    if not t:
        sys.exit('GFW_TOKEN not set.  $env:GFW_TOKEN = "your-token"')
    return t


def dump(name: str, resp: requests.Response, note: str = "") -> dict | None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        body = resp.json()
        text = json.dumps(body, indent=2)
    except ValueError:
        body, text = None, resp.text

    # Truncate on a record boundary, never mid-JSON.
    if len(text) > 400_000 and isinstance(body, dict):
        trimmed = dict(body)
        trimmed["entries"] = "<<truncated for review; see counts below>>"
        text = json.dumps(trimmed, indent=2)
    (OUT_DIR / f"{name}.json").write_text(text[:400_000], encoding="utf-8")

    n = hours = vessels = 0
    if resp.ok and isinstance(body, dict):
        recs = [r for e in body.get("entries", []) or [] if isinstance(e, dict)
                for v in e.values() if v for r in v]
        n = len(recs)
        hours = sum(r.get("hours", 0) for r in recs)
        vessels = len({r.get("vesselId") for r in recs})

    detail = ""
    if not resp.ok and isinstance(body, dict):
        msgs = body.get("messages") or []
        detail = "; ".join(m.get("detail", "") for m in msgs)[:160]

    line = (f"[{'OK ' if resp.ok else 'ERR'}] {resp.status_code}  {name:<34} "
            f"recs={n:<6} hours={hours:<7} vessels={vessels:<5} {note} {detail}").rstrip()
    print(line)
    log.append(line)
    return body if resp.ok else None


def report(params: dict, geo: dict, t: str) -> requests.Response:
    return requests.post(
        f"{BASE}/4wings/report", params=params,
        headers={"Authorization": f"Bearer {t}"}, json={"geojson": geo}, timeout=600,
    )


def main() -> None:
    t = tok()
    print(f"Writing to {OUT_DIR}\n")

    # -- A. World-extent polygon, with a filter GFW definitely accepts ----
    # Narrow enough to stay small: one flag, one type, one speed bin, one day.
    narrow = f'flag in ("{VESSEL_FLAG}") AND vessel_type in ("cargo") AND speed in ("10-15")'
    dump(
        "A1_world_narrow_filter",
        report({**BASE_PARAMS, "filters[0]": narrow}, WORLD_BOX, t),
        "world polygon accepted?",
    )
    dump(
        "A2_socal_same_filter",
        report({**BASE_PARAMS, "filters[0]": narrow}, SOCAL_BOX, t),
        "same filter, small box (control)",
    )

    # -- B. Vessel groups -------------------------------------------------
    r = requests.get(f"{BASE}/vessel-groups", params={"limit": 5},
                     headers={"Authorization": f"Bearer {t}"}, timeout=60)
    body = dump("B1_vessel_groups_list", r, "does the endpoint exist?")

    group_id = None
    if r.status_code in (200, 401, 403):
        r = requests.post(
            f"{BASE}/vessel-groups",
            headers={"Authorization": f"Bearer {t}"},
            json={
                "name": f"emissions-allocation-pilot-{datetime.now():%Y%m%d%H%M%S}",
                "vessels": [{"vesselId": VESSEL_ID,
                             "dataset": "public-global-vessel-identity:latest"}],
            },
            timeout=120,
        )
        body = dump("B2_vessel_group_create", r, "create a 1-vessel group")
        if body:
            group_id = body.get("id") or (body.get("entries") or [{}])[0].get("id")
    print(f"    group_id = {group_id}\n")
    log.append(f"    group_id = {group_id}")

    if group_id:
        for label, expr in {
            "B3_group_filter": f'vessel-groups in ("{group_id}")',
            "B4_group_filter_alt": f"vessel_groups in ('{group_id}')",
        }.items():
            dump(label, report({**BASE_PARAMS, "filters[0]": expr}, WORLD_BOX, t), expr)

    # -- C. Which identity field, if any, can scope a presence report? ----
    # Hypothesis: flag / vessel_type / speed are denormalised onto the
    # presence rows and work, while anything needing the `vessels` join
    # (vessel_id, imo, shipname) hits the VESSEL_UPPER bug. If `imo` works
    # it is the best key of all -- stable across GFW identity versions,
    # unlike vesselId.
    identity_filters = {
        "C1_vesselid_eq_single": f"vessel_id = '{VESSEL_ID}'",
        "C2_vesselid_eq_double": f'vessel_id = "{VESSEL_ID}"',
        "C3_vesselid_in_double": f'vessel_id in ("{VESSEL_ID}")',
        "C4_imo_eq": f'imo = "{TEST_IMO}"',
        "C5_imo_in": f'imo in ("{TEST_IMO}")',
        "C6_ssvid_in": f'ssvid in ("{TEST_MMSI}")',
        "C7_mmsi_in": f'mmsi in ("{TEST_MMSI}")',
        "C8_shipname_in": f'shipname in ("{TEST_SHIPNAME}")',
    }
    for label, expr in identity_filters.items():
        dump(label, report({**BASE_PARAMS, "filters[0]": expr}, SOCAL_BOX, t), expr)

    # If any identity field binds, confirm it also works world-extent --
    # that is the whole ballgame: one request per speed bin per year.
    for label, expr in identity_filters.items():
        if any(f"[OK ]" in line and label in line for line in log):
            dump(f"C9_world_{label}", report({**BASE_PARAMS, "filters[0]": expr},
                                             WORLD_BOX, t), f"world extent + {expr}")
            break

    # -- D. Does temporal resolution change speed-filter behaviour? -------
    speed_expr = 'vessel_type in ("cargo") AND speed in ("10-15")'
    for res in ("HOURLY", "DAILY"):
        dump(
            f"D_{res.lower()}_speedfilter",
            report({**BASE_PARAMS, "temporal-resolution": res,
                    "filters[0]": speed_expr}, SOCAL_BOX, t),
            f"{res}: does the speed condition still bind?",
        )
        dump(
            f"D_{res.lower()}_nospeedfilter",
            report({**BASE_PARAMS, "temporal-resolution": res,
                    "filters[0]": 'vessel_type in ("cargo")'}, SOCAL_BOX, t),
            f"{res}: control, no speed condition",
        )

    (OUT_DIR / "_summary.txt").write_text(
        f"round 2 probe  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"vessel {VESSEL_ID} ({VESSEL_FLAG})\n\n" + "\n".join(log) + "\n",
        encoding="utf-8",
    )
    print(f"\nSummary: {OUT_DIR / '_summary.txt'}")
    print("Read it like this:")
    print("  A1 OK and A2 <= A1         -> world polygon works, cheap path is live")
    print("  B3 or B4 OK, vessels=1     -> vessel groups work")
    print("  C4/C5 OK, vessels=1        -> IMO filter works: best key of all, use it")
    print("  any C OK, vessels=1        -> some identity field binds; C9 confirms world extent")
    print("  all C 503 VESSEL_UPPER     -> identity join is broken, use event-guided pulls")
    print("  D_daily_* identical counts -> DAILY drops the speed condition")


if __name__ == "__main__":
    main()

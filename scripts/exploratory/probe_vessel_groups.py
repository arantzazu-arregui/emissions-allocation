"""
Round 3 probe: can a vessel group give us a STABLE, IMO-anchored key for
scoping presence reports -- instead of `shipname`, which round 2 showed works
but is neither unique nor stable across renames?

Also probes the fallback: does the Vessels API expose a vessel's NAME HISTORY
with date ranges? If it does, the shipname approach is rescuable -- query each
former name over its own window and stitch the series on IMO.

Six blocks, ~15 small calls, about two minutes.

    A  List vessel groups (round 2 failed only because `limit` needs `offset`)
    B  Create a one-vessel group -- three candidate body shapes
    C  Filter a presence report by the group -- filter expr and query param
    D  Same at world extent (the whole point)
    E  Vessel name history from /v3/vessels/{id} (the shipname fallback)
    F  How does the `shipname` filter match? exact / case / partial

If a group is created it persists in your GFW account. Set CLEANUP = True to
delete it again at the end.

Usage:
    $env:GFW_TOKEN = "your-token"
    python scripts/probe_vessel_groups.py

Output: data/sample/api/round3/*.json + _summary.txt
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://gateway.api.globalfishingwatch.org/v3"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sample" / "api" / "round3"

VESSEL_ID = "103dbbea9-9221-5f81-a00f-657a515745bb"   # COSCO ITALY
TEST_IMO = "9516454"
TEST_SHIPNAME = "COSCO ITALY"
IDENTITY_DS = "public-global-vessel-identity:latest"
DAY = "2024-01-15,2024-01-16"

CLEANUP = True   # delete the test group when done

SOCAL_BOX = {"type": "Polygon", "coordinates": [[
    [-118.6, 33.4], [-118.0, 33.4], [-118.0, 33.9], [-118.6, 33.9], [-118.6, 33.4]]]}
WORLD_BOX = {"type": "Polygon", "coordinates": [[
    [-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]]}

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
    if len(text) > 400_000 and isinstance(body, dict):
        t2 = dict(body)
        t2["entries"] = "<<truncated; see counts below>>"
        text = json.dumps(t2, indent=2)
    (OUT_DIR / f"{name}.json").write_text(text[:400_000], encoding="utf-8")

    n = hours = vessels = 0
    if resp.ok and isinstance(body, dict):
        recs = [r for e in body.get("entries", []) or [] if isinstance(e, dict)
                for v in e.values() if isinstance(v, list) for r in v]
        n = len(recs)
        hours = sum(r.get("hours", 0) for r in recs if isinstance(r, dict))
        vessels = len({r.get("vesselId") for r in recs if isinstance(r, dict)})

    detail = ""
    if not resp.ok and isinstance(body, dict):
        detail = "; ".join(m.get("detail", "") for m in body.get("messages") or [])[:170]

    line = (f"[{'OK ' if resp.ok else 'ERR'}] {resp.status_code}  {name:<32} "
            f"recs={n:<5} hours={hours:<6} vessels={vessels:<4} {note} {detail}").rstrip()
    print(line)
    log.append(line)
    return body if resp.ok else None


def report(params: dict, geo: dict, t: str) -> requests.Response:
    return requests.post(f"{BASE}/4wings/report", params=params,
                         headers={"Authorization": f"Bearer {t}"},
                         json={"geojson": geo}, timeout=600)


def main() -> None:
    t = tok()
    H = {"Authorization": f"Bearer {t}"}
    print(f"Writing to {OUT_DIR}\n")

    # -- A. List groups (offset is required alongside limit) --------------
    dump("A1_groups_list", requests.get(f"{BASE}/vessel-groups",
         params={"limit": 5, "offset": 0}, headers=H, timeout=60),
         "list groups, offset supplied")

    # -- B. Create a one-vessel group -- three candidate body shapes ------
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    bodies = {
        "B1_create_vesselId": {
            "name": f"ea-pilot-{stamp}-1",
            "vessels": [{"vesselId": VESSEL_ID, "dataset": IDENTITY_DS}],
        },
        "B2_create_id": {
            "name": f"ea-pilot-{stamp}-2",
            "vessels": [{"id": VESSEL_ID, "dataset": IDENTITY_DS}],
        },
        "B3_create_searchparams": {
            "name": f"ea-pilot-{stamp}-3",
            "searchParams": {"ids": [VESSEL_ID], "datasets": [IDENTITY_DS]},
        },
    }
    group_id = None
    for label, body in bodies.items():
        r = requests.post(f"{BASE}/vessel-groups", headers=H, json=body, timeout=120)
        got = dump(label, r, json.dumps(body)[:90])
        if got and not group_id:
            group_id = got.get("id") or got.get("vesselGroupId") \
                or (got.get("entries") or [{}])[0].get("id")
    print(f"    group_id = {group_id}\n")
    log.append(f"    group_id = {group_id}")

    # -- C / D. Scope a report by the group -------------------------------
    if group_id:
        variants = {
            "C1_filter_hyphen": ({**BASE_PARAMS,
                                  "filters[0]": f'vessel-groups in ("{group_id}")'}, SOCAL_BOX),
            "C2_filter_underscore": ({**BASE_PARAMS,
                                      "filters[0]": f'vessel_groups in ("{group_id}")'}, SOCAL_BOX),
            "C3_query_param": ({**BASE_PARAMS, "vessel-groups[0]": group_id}, SOCAL_BOX),
            "C4_query_param_alt": ({**BASE_PARAMS, "vessel-groups": group_id}, SOCAL_BOX),
        }
        working = None
        for label, (params, geo) in variants.items():
            body = dump(label, report(params, geo, t), label)
            if body and not working:
                working = (label, params)
        if working:
            label, params = working
            dump("D1_world_group", report(params, WORLD_BOX, t),
                 f"world extent using {label} -- THE ANSWER")
    else:
        log.append("    -- C/D skipped: no group created")
        print("    -- C/D skipped: no group created")

    # -- E. Name history: the shipname fallback ---------------------------
    body = dump("E1_vessel_detail", requests.get(f"{BASE}/vessels/{VESSEL_ID}",
                params={"dataset": IDENTITY_DS}, headers=H, timeout=60),
                "no includes -- does selfReportedInfo carry name history?")
    if body:
        names = set()
        for block in ("selfReportedInfo", "registryInfo", "combinedSourcesInfo"):
            for e in body.get(block) or []:
                if isinstance(e, dict):
                    for k in ("shipname", "nShipname", "name"):
                        if e.get(k):
                            names.add((block, e[k],
                                       e.get("transmissionDateFrom") or e.get("dateFrom"),
                                       e.get("transmissionDateTo") or e.get("dateTo")))
        print(f"    names found: {sorted(names)}")
        log.append(f"    names found: {sorted(names)}")

    dump("E2_search_by_imo", requests.get(f"{BASE}/vessels/search",
         params={"query": TEST_IMO, "datasets[0]": IDENTITY_DS, "limit": 10, "offset": 0},
         headers=H, timeout=60), "how many identity records share this IMO?")

    # -- F. shipname matching semantics -----------------------------------
    for label, expr in {
        "F1_exact": f'shipname in ("{TEST_SHIPNAME}")',
        "F2_lowercase": f'shipname in ("{TEST_SHIPNAME.lower()}")',
        "F3_partial": 'shipname in ("COSCO")',
        "F4_two_names": f'shipname in ("{TEST_SHIPNAME}","EVER GIVEN")',
    }.items():
        dump(label, report({**BASE_PARAMS, "filters[0]": expr}, SOCAL_BOX, t), expr)

    # -- cleanup -----------------------------------------------------------
    if group_id and CLEANUP:
        dump("Z_delete_group",
             requests.delete(f"{BASE}/vessel-groups/{group_id}", headers=H, timeout=60),
             f"cleanup {group_id}")

    (OUT_DIR / "_summary.txt").write_text(
        f"round 3 probe  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"vessel {VESSEL_ID} / IMO {TEST_IMO}\n\n" + "\n".join(log) + "\n",
        encoding="utf-8")
    print(f"\nSummary: {OUT_DIR / '_summary.txt'}")
    print("Read it like this:")
    print("  D1 OK with vessels=1   -> groups work at world extent: stable IMO-anchored key, use it")
    print("  all B ERR              -> groups not creatable on a free token; fall back to shipname")
    print("  E1 shows >1 name+dates -> rename risk is real AND solvable; query each name window")
    print("  F3 OK with recs>0      -> shipname matches partially: filter is looser than it looks")
    print("  F4 OK                  -> multiple names per filter: one request covers a rename history")


if __name__ == "__main__":
    main()

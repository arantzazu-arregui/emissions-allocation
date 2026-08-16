"""
Round 4 probe: the four questions that survive into the production pull.

    A  Full port-visit history 2017-2024, paginated.
       -> Does this vessel call at EU ports? (decides whether THETIS-MRV
          validation exists at all)
       -> How does /v3/events pagination actually behave?

    B  Does `shipname in ("COSCO ITALY")` return MORE THAN ONE IMO at world
       extent? There is a second container ship of the same name.

    C  A full year of hourly presence in one request. Does it complete, how
       long does it take, does pagination engage, and how many observed hours
       does it return against 8,784 elapsed?

    D  Does a coverage / insights endpoint exist on v3 at all?

Each block is independent. A failure in one does not invalidate another --
that was the flaw in round 2, where a broken vessel filter took the
world-extent test down with it and I misread the result.

Usage:
    $env:GFW_TOKEN = "your-token"
    python scripts/probe_scale_and_coverage.py

Output: data/sample/api/round4/*.json + _summary.txt
Runtime: a few minutes. Block C is the slow one.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://gateway.api.globalfishingwatch.org/v3"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sample" / "api" / "round4"

VESSEL_ID = "103dbbea9-9221-5f81-a00f-657a515745bb"
TEST_IMO = "9516454"
SHIPNAME = "COSCO ITALY"
IDENTITY_DS = "public-global-vessel-identity:latest"
PORT_VISIT_DS = "public-global-port-visits-events:latest"

PROBE_YEAR = 2024          # block C
WORLD = {"type": "Polygon", "coordinates": [[
    [-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]]}

EU27 = {
    "AUT", "BEL", "BGR", "HRV", "CYP", "CZE", "DNK", "EST", "FIN", "FRA",
    "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD",
    "POL", "PRT", "ROU", "SVK", "SVN", "ESP", "SWE",
}

log: list[str] = []


def tok() -> str:
    t = os.environ.get("GFW_TOKEN")
    if not t:
        sys.exit('GFW_TOKEN not set.  $env:GFW_TOKEN = "your-token"')
    return t


def say(line: str) -> None:
    print(line)
    log.append(line)


def save(name: str, obj) -> None:
    """Write JSON safely -- never truncate mid-structure."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(
        json.dumps(obj, indent=2, default=str), encoding="utf-8"
    )


def err_detail(resp: requests.Response) -> str:
    try:
        b = resp.json()
    except ValueError:
        return resp.text[:150]
    if isinstance(b, dict):
        msgs = b.get("messages") or []
        return "; ".join(m.get("detail", "") for m in msgs)[:200] or str(b)[:200]
    return str(b)[:200]


def recs_of(body: dict) -> list[dict]:
    out = []
    for e in body.get("entries") or []:
        if isinstance(e, dict):
            for v in e.values():
                if isinstance(v, list):
                    out.extend(r for r in v if isinstance(r, dict))
    return out


# ---------------------------------------------------------------- A ------
def block_a(t: str) -> None:
    say("\n=== A. Port-visit history 2017-2024, paginated ===")
    H = {"Authorization": f"Bearer {t}"}
    events, offset, pages = [], 0, 0
    while True:
        r = requests.get(
            f"{BASE}/events",
            params={
                "vessels[0]": VESSEL_ID,
                "datasets[0]": PORT_VISIT_DS,
                "start-date": "2017-01-01",
                "end-date": "2025-01-01",
                "confidences[0]": 3,
                "confidences[1]": 4,
                "limit": 100,
                "offset": offset,
            },
            headers=H, timeout=180,
        )
        if not r.ok:
            say(f"  [ERR] {r.status_code} at offset {offset}: {err_detail(r)}")
            break
        body = r.json()
        batch = body.get("entries") or []
        pages += 1
        events.extend(batch)
        nxt = body.get("nextOffset")
        say(f"  page {pages}: {len(batch)} events, total={body.get('total')}, "
            f"nextOffset={nxt}")
        if not nxt or not batch:
            break
        offset = nxt
        if pages > 40:
            say("  [stopping at 40 pages -- pagination may not terminate]")
            break

    save("A_port_visits_2017_2024", events)
    if not events:
        say("  no events returned")
        return

    countries, per_year = {}, {}
    for e in events:
        pv = e.get("port_visit") or {}
        anch = pv.get("startAnchorage") or pv.get("endAnchorage") or {}
        c = anch.get("flag")
        if c:
            countries[c] = countries.get(c, 0) + 1
        yr = (e.get("start") or "")[:4]
        per_year[yr] = per_year.get(yr, 0) + 1

    eu_hits = {c: n for c, n in countries.items() if c in EU27}
    say(f"  {len(events)} port calls over {pages} page(s)")
    say(f"  calls per year: {dict(sorted(per_year.items()))}")
    say(f"  port countries: {dict(sorted(countries.items(), key=lambda x: -x[1]))}")
    say(f"  >>> EU port calls: {eu_hits if eu_hits else 'NONE'}")
    say("  >>> THETIS-MRV validation is "
        + ("AVAILABLE" if eu_hits else "NOT available for this vessel"))


# ---------------------------------------------------------------- B ------
def block_b(t: str) -> None:
    say("\n=== B. Does the shipname filter return more than one IMO? ===")
    params = {
        "spatial-resolution": "HIGH", "temporal-resolution": "HOURLY",
        "group-by": "VESSEL_ID", "datasets[0]": "public-global-presence:latest",
        "date-range": "2024-06-01,2024-07-01", "format": "JSON",
        "filters[0]": f'shipname in ("{SHIPNAME}")',
    }
    r = requests.post(f"{BASE}/4wings/report", params=params,
                      headers={"Authorization": f"Bearer {t}"},
                      json={"geojson": WORLD}, timeout=900)
    if not r.ok:
        say(f"  [ERR] {r.status_code}: {err_detail(r)}")
        return
    recs = recs_of(r.json())
    imos = {}
    for x in recs:
        k = (x.get("imo") or "(blank)", x.get("shipName"), x.get("flag"))
        imos[k] = imos.get(k, 0) + 1
    save("B_shipname_world_month", {"n_records": len(recs),
                                    "by_identity": {str(k): v for k, v in imos.items()}})
    say(f"  {len(recs)} records, one month, world extent")
    for k, v in sorted(imos.items(), key=lambda x: -x[1]):
        say(f"    imo={k[0]:<10} name={k[1]!r:<16} flag={k[2]}  records={v}")
    say(f"  >>> distinct IMOs returned: {len(imos)}"
        + ("  -- IMO post-filter is REQUIRED" if len(imos) > 1
           else "  -- single hull, but keep the post-filter anyway"))


# ---------------------------------------------------------------- C ------
def block_c(t: str) -> None:
    say(f"\n=== C. One full year ({PROBE_YEAR}) of hourly presence ===")
    params = {
        "spatial-resolution": "HIGH", "temporal-resolution": "HOURLY",
        "group-by": "VESSEL_ID", "datasets[0]": "public-global-presence:latest",
        "date-range": f"{PROBE_YEAR}-01-01,{PROBE_YEAR+1}-01-01", "format": "JSON",
        "filters[0]": f'shipname in ("{SHIPNAME}")',
    }
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/4wings/report", params=params,
                          headers={"Authorization": f"Bearer {t}"},
                          json={"geojson": WORLD}, timeout=1800)
    except requests.exceptions.RequestException as exc:
        say(f"  [ERR] request failed after {time.time()-t0:.0f}s: {exc}")
        say("  >>> a one-year request is NOT viable; chunk monthly")
        return
    dt = time.time() - t0
    if not r.ok:
        say(f"  [ERR] {r.status_code} after {dt:.0f}s: {err_detail(r)}")
        say("  >>> chunk the pull monthly or quarterly")
        return

    body = r.json()
    recs = recs_of(body)
    hours = sum(x.get("hours", 0) for x in recs)
    ours = [x for x in recs if x.get("imo") == TEST_IMO]
    elapsed = 8784 if PROBE_YEAR % 4 == 0 else 8760
    save("C_year_meta", {
        "seconds": round(dt, 1), "n_records": len(recs),
        "total_field": body.get("total"), "limit": body.get("limit"),
        "offset": body.get("offset"), "nextOffset": body.get("nextOffset"),
        "observed_hours_all": hours,
        "observed_hours_our_imo": sum(x.get("hours", 0) for x in ours),
        "distinct_imos": sorted({x.get("imo") or "(blank)" for x in recs}),
    })
    save("C_year_sample_records", recs[:200])
    say(f"  completed in {dt:.0f}s, {len(recs)} records")
    say(f"  envelope: total={body.get('total')} limit={body.get('limit')} "
        f"offset={body.get('offset')} nextOffset={body.get('nextOffset')}")
    say(f"  observed hours (all identities): {hours:,} of {elapsed:,} elapsed "
        f"= {100*hours/elapsed:.1f}%")
    say(f"  observed hours (IMO {TEST_IMO} only): "
        f"{sum(x.get('hours',0) for x in ours):,}")
    say(f"  >>> {'pagination ENGAGED' if body.get('nextOffset') else 'no pagination'} "
        f"-- 8-year pull is ~{dt*8/60:.0f} min at this rate")


# ---------------------------------------------------------------- D ------
def block_d(t: str) -> None:
    say("\n=== D. Does a coverage / insights endpoint exist? ===")
    H = {"Authorization": f"Bearer {t}"}
    attempts = [
        ("D1_insights_get", "GET", "/insights/vessels", {"limit": 1, "offset": 0}, None),
        ("D2_insights_post", "POST", "/insights/vessels", None, {
            "includes": ["COVERAGE"],
            "startDate": "2024-01-01", "endDate": "2024-12-31",
            "vessels": [{"vesselId": VESSEL_ID, "datasetId": IDENTITY_DS}],
        }),
        ("D3_insights_root", "GET", "/insights", {}, None),
        ("D4_vessel_coverage", "GET", f"/vessels/{VESSEL_ID}/coverage",
         {"dataset": IDENTITY_DS}, None),
    ]
    found = None
    for name, method, path, params, body in attempts:
        try:
            if method == "GET":
                r = requests.get(f"{BASE}{path}", params=params, headers=H, timeout=120)
            else:
                r = requests.post(f"{BASE}{path}", headers=H, json=body, timeout=120)
        except requests.exceptions.RequestException as exc:
            say(f"  [ERR] {name}: {exc}")
            continue
        try:
            payload = r.json()
        except ValueError:
            payload = {"raw": r.text[:2000]}
        save(name, {"status": r.status_code, "body": payload})
        say(f"  [{'OK ' if r.ok else 'ERR'}] {r.status_code}  {name}  "
            f"{'' if r.ok else err_detail(r)}")
        if r.ok and not found:
            found = name
    say(f"  >>> coverage metric: {'AVAILABLE via ' + found if found else 'NOT FOUND on v3'}")
    if not found:
        say("      -> pipeline reports uncorrected observed hours, flagged")


def main() -> None:
    t = tok()
    print(f"Writing to {OUT_DIR}\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fn in (block_a, block_b, block_c, block_d):
        try:
            fn(t)
        except Exception as exc:                      # noqa: BLE001
            say(f"  [BLOCK FAILED] {fn.__name__}: {exc!r}")
    (OUT_DIR / "_summary.txt").write_text(
        f"round 4 probe  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"{SHIPNAME} / IMO {TEST_IMO}\n" + "\n".join(log) + "\n", encoding="utf-8")
    print(f"\nSummary: {OUT_DIR / '_summary.txt'}")


if __name__ == "__main__":
    main()

"""
Analysis step: SQL queries over the Parquet grid cells fetched by
fetch_presence.py, using DuckDB.

All raw files in data/raw/ are read as one table. The "all" vessel_class rows
are the unfiltered baseline; specific classes (cargo, tanker, ...) are
separate pulls of the same period, so never SUM across "all" AND a class.

Usage:
    python analyze_presence.py

Output: printed tables + CSVs in data/out/
"""

from pathlib import Path

import duckdb

RAW_GLOB = "data/raw/presence_*.parquet"
TRACKS_GLOB = "data/raw/tracks_*.parquet"
OUT_DIR = Path("data/out")

QUERIES = {
    # 1. Flag-state allocation: share of presence hours by flag (commercial
    #    shipping = every fetched class except the unfiltered baseline).
    "flag_shares": """
        SELECT
            flag,
            region,
            SUM(hours)                                        AS hours,
            ROUND(100 * SUM(hours)
                  / SUM(SUM(hours)) OVER (PARTITION BY region), 2) AS share_pct
        FROM cells
        WHERE vessel_class <> 'all'
        GROUP BY flag, region
        ORDER BY region, hours DESC
    """,

    # 2. Vessel class composition per region.
    "class_composition": """
        SELECT
            region,
            vessel_class,
            SUM(hours)                          AS hours,
            COUNT(*)                            AS grid_cells,
            ROUND(100 * SUM(hours)
                  / SUM(SUM(hours)) OVER (PARTITION BY region), 2) AS share_pct
        FROM cells
        WHERE vessel_class <> 'all'
        GROUP BY region, vessel_class
        ORDER BY region, hours DESC
    """,

    # 3. Flag x vessel class matrix (top flags): shows how open registries
    #    (PAN, LBR, MHL, MLT, ...) dominate specific classes.
    "flag_by_class": """
        SELECT
            flag,
            SUM(hours) FILTER (vessel_class = 'cargo')     AS cargo_h,
            SUM(hours) FILTER (vessel_class = 'carrier')   AS carrier_h,
            SUM(hours) FILTER (vessel_class = 'tanker')    AS tanker_h,
            SUM(hours) FILTER (vessel_class = 'passenger') AS passenger_h,
            SUM(hours) FILTER (vessel_class <> 'all')      AS total_h
        FROM cells
        GROUP BY flag
        ORDER BY total_h DESC NULLS LAST
        LIMIT 20
    """,

    # 4. Spatial (EEZ-style) allocation vs flag allocation, on the grid.
    #    Grid cells carry lat/lon, so once you fetch per-EEZ regions (or join
    #    cell centroids to EEZ polygons), compare: hours emitted IN a
    #    country's waters vs hours by ships FLYING its flag.
    #    With bbox regions this is a placeholder comparing region totals.
    "region_totals": """
        SELECT
            region,
            date_range,
            SUM(hours) FILTER (vessel_class = 'all') AS all_vessels_h,
            SUM(hours) FILTER (vessel_class <> 'all') AS commercial_h
        FROM cells
        GROUP BY region, date_range
        ORDER BY region
    """,
}


# Queries over per-vessel pseudo-track pulls (tracks_*.parquet).
# Schema: vessel_id, date (daily), lat/lon (0.01 deg cell), hours + tags.
TRACK_QUERIES = {
    # Coverage summary: how many vessels and vessel-days per region.
    "track_summary": """
        SELECT
            region,
            date_range,
            COUNT(DISTINCT vessel_id)                AS vessels,
            COUNT(DISTINCT (vessel_id, date))        AS vessel_days,
            SUM(hours)                               AS hours,
            COUNT(*)                                 AS records
        FROM track_cells
        GROUP BY region, date_range
        ORDER BY region
    """,

    # Most active vessels -- candidates to inspect via the Vessel API
    # (flag, type, size) and link to emissions estimates.
    "top_vessels": """
        SELECT
            vessel_id,
            region,
            COUNT(DISTINCT date)  AS active_days,
            SUM(hours)            AS hours,
            COUNT(*)              AS cells_visited
        FROM track_cells
        GROUP BY vessel_id, region
        ORDER BY hours DESC
        LIMIT 20
    """,

    # Daily activity profile of the region.
    "daily_activity": """
        SELECT
            region,
            date,
            COUNT(DISTINCT vessel_id) AS vessels,
            SUM(hours)                AS hours
        FROM track_cells
        GROUP BY region, date
        ORDER BY region, date
    """,
}


def run_queries(con, queries: dict[str, str]) -> None:
    for name, sql in queries.items():
        df = con.execute(sql).df()
        out = OUT_DIR / f"{name}.csv"
        df.to_csv(out, index=False)
        print(f"=== {name} -> {out} ===")
        print(df.head(20).to_string(index=False))
        print()


def main() -> None:
    con = duckdb.connect()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    con.execute(
        f"CREATE VIEW cells AS SELECT * FROM read_parquet('{RAW_GLOB}')"
    )
    n = con.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
    if n == 0:
        raise SystemExit("No data found. Run fetch_presence.py first.")
    print(f"Loaded {n:,} grid-cell records from {RAW_GLOB}\n")
    run_queries(con, QUERIES)

    if list(Path().glob(TRACKS_GLOB)):
        con.execute(
            f"CREATE VIEW track_cells AS SELECT * FROM read_parquet('{TRACKS_GLOB}')"
        )
        nt = con.execute("SELECT COUNT(*) FROM track_cells").fetchone()[0]
        print(f"Loaded {nt:,} vessel-day-cell records from {TRACKS_GLOB}\n")
        run_queries(con, TRACK_QUERIES)
    else:
        print(f"No track files ({TRACKS_GLOB}) -- skipping track queries.")


if __name__ == "__main__":
    main()

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

RAW_GLOB = "data/raw/*.parquet"
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


def main() -> None:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW cells AS SELECT * FROM read_parquet('{RAW_GLOB}')"
    )

    n = con.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
    if n == 0:
        raise SystemExit("No data found. Run fetch_presence.py first.")
    print(f"Loaded {n:,} grid-cell records from {RAW_GLOB}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, sql in QUERIES.items():
        df = con.execute(sql).df()
        out = OUT_DIR / f"{name}.csv"
        df.to_csv(out, index=False)
        print(f"=== {name} -> {out} ===")
        print(df.head(20).to_string(index=False))
        print()


if __name__ == "__main__":
    main()

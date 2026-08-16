"""DuckDB connection, spatial extension, view registration and SQL loading.

The architectural split, decided before implementation: *lookups, joins, windows and
aggregations are SQL; physical formulas are Python.* This module owns the SQL side.

SQL lives in ``sql/*.sql`` and is loaded by name, never embedded in Python string
literals. The SQL is a deliverable in its own right -- a researcher should be able
to read the allocation logic without reading any Python.

Spatial work is DuckDB's ``spatial`` extension rather than geopandas. It reads
GeoPackage through GDAL, including from inside a zip via ``/vsizip/``, which keeps
every point-in-polygon and distance operation in the ``.sql`` files where the
architecture decision puts them, and keeps three dependencies out of the project.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

import duckdb

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parent / "sql"


class SQLNotFound(FileNotFoundError):
    pass


def load_sql(name: str) -> str:
    """Read one ``.sql`` file by name (with or without the extension)."""
    filename = name if name.endswith(".sql") else f"{name}.sql"
    path = SQL_DIR / filename
    if not path.exists():
        available = sorted(p.name for p in SQL_DIR.glob("*.sql"))
        raise SQLNotFound(f"no SQL file {filename!r} in {SQL_DIR}. Available: {available}")
    return path.read_text(encoding="utf-8")


def vsizip(archive: Path, inner: str | None = None) -> str:
    """GDAL virtual path into a zipped layer, for ``ST_Read``.

    Marine Regions ships GeoPackages inside zips. GDAL reads them in place, so
    nothing needs unpacking into the working tree.
    """
    archive_path = str(archive).replace("\\", "/")
    return f"/vsizip/{archive_path}/{inner}" if inner else f"/vsizip/{archive_path}"


class Database:
    """A DuckDB connection with the spatial extension and the project's views."""

    def __init__(self, path: str | Path = ":memory:", *, spatial: bool = True) -> None:
        self.path = str(path)
        self.con = duckdb.connect(self.path)
        self._spatial_ready = False
        if spatial:
            self.ensure_spatial()

    # -- extension ----------------------------------------------------------

    def ensure_spatial(self) -> None:
        """Install and load ``spatial``. Downloads once, then works offline."""
        if self._spatial_ready:
            return
        try:
            self.con.execute("INSTALL spatial;")
        except duckdb.Error as exc:  # already installed, or offline with it cached
            log.debug("INSTALL spatial: %s", exc)
        try:
            self.con.execute("LOAD spatial;")
        except duckdb.Error as exc:
            raise RuntimeError(
                "DuckDB's spatial extension could not be loaded, so no spatial join "
                "can run.\n"
                "  It is downloaded once on first use and needs network access for "
                "that.\n"
                f"  Underlying error: {exc}"
            ) from exc
        self._spatial_ready = True

    # -- execution ----------------------------------------------------------

    def sql(self, name: str, /, **params: Any) -> duckdb.DuckDBPyRelation:
        """Run a named SQL file with ``$param`` substitution, returning a relation."""
        return self.con.sql(load_sql(name), params=params or None)

    def execute(self, name: str, /, **params: Any) -> None:
        """Run a named SQL file for its side effects (CREATE, INSERT)."""
        self.con.execute(load_sql(name), parameters=params or None)

    def query(self, sql: str, /, **params: Any) -> duckdb.DuckDBPyRelation:
        """Run inline SQL. For ad-hoc inspection only -- pipeline SQL lives in files."""
        return self.con.sql(sql, params=params or None)

    def df(self, name: str, /, **params: Any):
        return self.sql(name, **params).df()

    # -- registration -------------------------------------------------------

    def register_parquet(self, view: str, path: Path | str, *, glob: bool = False) -> None:
        """Register a Parquet file (or glob) as a view."""
        target = f"{path}/*.parquet" if glob else str(path)
        self.con.execute(
            f"CREATE OR REPLACE VIEW {view} AS "
            f"SELECT * FROM read_parquet(?)", [str(target).replace("\\", "/")]
        )
        log.info("registered view %s -> %s", view, target)

    def register_spatial_layer(
        self, view: str, archive: Path, *, layer: str | None = None, inner: str | None = None
    ) -> None:
        """Register a GeoPackage layer from inside a zip as a view.

        Args:
            view: View name to create.
            archive: Path to the ``.zip``.
            layer: Layer name within the GeoPackage (e.g. ``eez_v12``).
            inner: Path to the ``.gpkg`` inside the zip. Discovered if omitted.
        """
        self.ensure_spatial()
        source = vsizip(archive, inner or _find_gpkg(archive))
        options = f", layer='{layer}'" if layer else ""
        self.con.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM ST_Read('{source}'{options});"
        )
        log.info("registered spatial view %s -> %s (layer=%s)", view, source, layer)

    def register_frame(self, view: str, frame: Any) -> None:
        """Register a pandas DataFrame as a view.

        This is the seam between the two halves of the architecture: Python computes
        the physical model, hands the result over here, and SQL does the joins and
        aggregation from there on.
        """
        self.con.register(view, frame)

    def table_from(self, table: str, name: str, /, **params: Any) -> None:
        """Materialise a named SQL file into a table."""
        self.con.execute(
            f"CREATE OR REPLACE TABLE {table} AS {load_sql(name)}", parameters=params or None
        )

    def register_config_tables(self, factors: Mapping[str, Any]) -> None:
        """Materialise the IMO lookup tables so SQL can join to them.

        Table 17 becomes a range-joinable table of
        ``(ship_type, size_min, size_max, mode, boiler_kw, auxiliary_kw)`` -- the
        shape §4.3's range join needs.
        """
        rows: list[tuple[Any, ...]] = []
        block = factors.get("auxiliary_boiler_power") or {}
        modes = block.get("modes") or []
        for ship_type, spec in (block.get("ship_types") or {}).items():
            unit = spec.get("size_unit")
            for band in spec.get("bands") or []:
                for i, mode in enumerate(modes):
                    rows.append((
                        ship_type, unit, band["min"], band.get("max"),
                        mode, band["boiler"][i], band["auxiliary"][i],
                    ))

        self.con.execute("""
            CREATE OR REPLACE TABLE imo_table17 (
                ship_type     VARCHAR,
                size_unit     VARCHAR,
                size_min      DOUBLE,
                size_max      DOUBLE,
                mode          VARCHAR,
                boiler_kw     DOUBLE,
                auxiliary_kw  DOUBLE
            );
        """)
        if rows:
            self.con.executemany(
                "INSERT INTO imo_table17 VALUES (?, ?, ?, ?, ?, ?, ?)", rows
            )

        ef_rows = [
            (fuel, spec["carbon_content"], spec["ef_f"])
            for fuel, spec in ((factors.get("emission_factors") or {}).get("fuels") or {}).items()
        ]
        self.con.execute("""
            CREATE OR REPLACE TABLE imo_table21 (
                fuel            VARCHAR,
                carbon_content  DOUBLE,
                ef_f            DOUBLE
            );
        """)
        if ef_rows:
            self.con.executemany("INSERT INTO imo_table21 VALUES (?, ?, ?)", ef_rows)

        sfc_rows = [
            (engine, fuel, value)
            for engine, fuels in ((factors.get("sfc_base") or {}).get("engines") or {}).items()
            for fuel, value in fuels.items()
        ]
        self.con.execute("""
            CREATE OR REPLACE TABLE imo_table19 (
                engine     VARCHAR,
                fuel       VARCHAR,
                sfc_g_kwh  DOUBLE
            );
        """)
        if sfc_rows:
            self.con.executemany("INSERT INTO imo_table19 VALUES (?, ?, ?)", sfc_rows)

        log.info(
            "registered IMO lookup tables: table17=%d rows, table19=%d, table21=%d",
            len(rows), len(sfc_rows), len(ef_rows),
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _find_gpkg(archive: Path) -> str:
    """Locate the ``.gpkg`` inside a zip without extracting it."""
    import zipfile

    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".gpkg")]
    if not names:
        raise FileNotFoundError(f"no .gpkg inside {archive}")
    if len(names) > 1:
        log.warning("%s holds %d GeoPackages; using %s", archive.name, len(names), names[0])
    return names[0]

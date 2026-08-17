"""DuckDB setup, SQL loading and the IMO lookup tables.

The scenario cross-join test exists because of a real bug: three ``UNNEST`` calls in
one ``SELECT`` list zip positionally in DuckDB rather than producing a cross product,
so a 2 x 2 x 4 scenario space silently became 4 rows instead of 16. Nothing would
have failed downstream -- every aggregate would simply have covered a quarter of the
intended space.
"""

from __future__ import annotations

import pytest

from emissions_allocation.config import load_config
from emissions_allocation.db import Database, SQLNotFound, load_sql, vsizip


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def db(cfg):
    database = Database()
    database.register_config_tables(cfg.factors)
    database.execute(
        "00_register_views",
        power_estimates=cfg.run["power_estimates"],
        hk_treatments=cfg.run["hk_treatments"],
        smoothing_windows=cfg.run["smoothing_windows"],
    )
    yield database
    database.close()


# ---------------------------------------------------------------------------
# SQL loading
# ---------------------------------------------------------------------------


def test_sql_loads_by_name() -> None:
    assert "CREATE OR REPLACE TABLE scenario" in load_sql("00_register_views")


def test_sql_extension_is_optional() -> None:
    assert load_sql("00_register_views") == load_sql("00_register_views.sql")


def test_missing_sql_lists_what_is_available() -> None:
    with pytest.raises(SQLNotFound, match="Available"):
        load_sql("99_does_not_exist")


# ---------------------------------------------------------------------------
# Spatial extension
# ---------------------------------------------------------------------------


def test_spatial_extension_loads(db) -> None:
    """Confirms the geopandas-free approach works: GEOS is available in SQL."""
    assert db.query("SELECT ST_AsText(ST_Point(1, 2)) AS p").fetchone()[0] == "POINT (1 2)"


def test_spheroid_distance_is_available(db) -> None:
    """§4.1 needs metre distances for the 1 nm and 5 nm thresholds."""
    metres = db.query(
        "SELECT ST_Distance_Spheroid(ST_Point(0, 0), ST_Point(0, 1)) AS d"
    ).fetchone()[0]
    assert 110_000 < metres < 111_500  # ~1 degree of latitude


def test_vsizip_builds_a_gdal_virtual_path(cfg) -> None:
    path = vsizip(cfg.spatial_layer("eez"), "inner.gpkg")
    assert path.startswith("/vsizip/")
    assert path.endswith("/inner.gpkg")
    assert "\\" not in path


# ---------------------------------------------------------------------------
# Scenario cross join -- §8.1
# ---------------------------------------------------------------------------


def test_scenario_table_is_a_full_cross_join(db, cfg) -> None:
    """Regression: three UNNESTs in one SELECT list zip instead of cross-joining."""
    rows = db.query("SELECT count(*) AS n FROM scenario").fetchone()[0]
    expected = (
        len(cfg.run["power_estimates"])
        * len(cfg.run["hk_treatments"])
        * len(cfg.run["smoothing_windows"])
    )
    assert rows == expected


def test_sql_and_python_agree_on_the_scenario_space(db, cfg) -> None:
    sql_ids = {r[0] for r in db.query("SELECT scenario_id FROM scenario").fetchall()}
    assert sql_ids == {s["scenario_id"] for s in cfg.scenarios()}


def test_every_axis_value_appears(db, cfg) -> None:
    for column, key in (
        ("power_estimate", "power_estimates"),
        ("hk_treatment", "hk_treatments"),
        ("smoothing_window", "smoothing_windows"),
    ):
        seen = {r[0] for r in db.query(f"SELECT DISTINCT {column} FROM scenario").fetchall()}
        assert seen == set(cfg.run[key])


def test_scenario_ids_are_unique(db) -> None:
    total, distinct = db.query(
        "SELECT count(*), count(DISTINCT scenario_id) FROM scenario"
    ).fetchone()
    assert total == distinct


# ---------------------------------------------------------------------------
# IMO lookup tables
# ---------------------------------------------------------------------------


def test_table17_is_range_joinable(db) -> None:
    """§4.3 joins on the TEU band, so bands must be queryable as ranges."""
    row = db.query("""
        SELECT boiler_kw, auxiliary_kw
        FROM imo_table17
        WHERE ship_type = 'container'
          AND mode = 'anchored'
          AND 13174 BETWEEN size_min AND coalesce(size_max, 1e18)
    """).fetchone()
    assert row == (630.0, 1800.0)


def test_table17_covers_every_ship_type_and_mode(db) -> None:
    """All 19 Table 17 ship types, so any hull resolves without transcription.

    Carrying only the types the pilot happened to need is what made the pipeline
    look complete when it was complete for container ships alone -- a template that
    needs a PDF read before it can price a car carrier is not a template.
    """
    types, modes = db.query(
        "SELECT count(DISTINCT ship_type), count(DISTINCT mode) FROM imo_table17"
    ).fetchone()
    assert types == 19
    assert modes == 4


def test_table17_prices_every_ship_type_in_the_source(db) -> None:
    present = {r[0] for r in db.query("SELECT DISTINCT ship_type FROM imo_table17").fetchall()}
    assert {"vehicle", "ro_ro", "cruise", "ferry_ropax", "refrigerated_bulk"} <= present


def test_vehicle_carrier_resolves(db) -> None:
    """RCC AMERICA, DWT 21,182 -- a candidate vessel B."""
    row = db.query("""
        SELECT boiler_kw, auxiliary_kw FROM imo_table17
        WHERE ship_type = 'vehicle' AND mode = 'anchored'
          AND 21182 BETWEEN size_min AND coalesce(size_max, 1e18)
    """).fetchone()
    assert row == (300.0, 550.0)


def test_table21_emission_factors_registered(db) -> None:
    assert db.query("SELECT ef_f FROM imo_table21 WHERE fuel = 'HFO'").fetchone()[0] == 3.114
    assert db.query("SELECT ef_f FROM imo_table21 WHERE fuel = 'MDO'").fetchone()[0] == 3.206


def test_table19_sfc_registered(db) -> None:
    value = db.query(
        "SELECT sfc_g_kwh FROM imo_table19 WHERE engine = 'SSD' AND fuel = 'HFO'"
    ).fetchone()[0]
    assert value == 175

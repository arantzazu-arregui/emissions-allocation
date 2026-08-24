"""Repository-layout checks for the pipeline structure.

Replaces the earlier version, which asserted the presence of three exploratory
scripts (``fetch_presence.py``, ``analyze_presence.py``, ``make_sample.py``) that
have since been removed as superseded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PIPELINE_MODULES = [
    "config.py", "gfw.py", "db.py", "selection.py", "activity.py", "specs.py",
    "fuel.py", "emissions.py", "allocation.py", "baselines.py", "impacts.py",
    "validate.py",
]

CONFIG_FILES = ["pilot.yaml", "vessel_specs.yaml", "emission_factors.yaml",
                "eexi_parameters.yaml"]

SUPERSEDED = ["fetch_presence.py", "analyze_presence.py", "make_sample.py"]

EXPLORATORY_PROBES = [
    "dump_api_samples.py", "probe_vessel_scope.py", "probe_vessel_groups.py",
    "probe_scale_and_coverage.py",
]


@pytest.mark.parametrize("module", PIPELINE_MODULES)
def test_pipeline_module_exists(module: str) -> None:
    assert (PROJECT_ROOT / "src" / "emissions_allocation" / module).is_file()


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_config_file_exists(filename: str) -> None:
    assert (PROJECT_ROOT / "config" / filename).is_file()


def test_entry_point_exists() -> None:
    assert (PROJECT_ROOT / "scripts" / "run_pipeline.py").is_file()


@pytest.mark.parametrize("script", SUPERSEDED)
def test_superseded_scripts_are_gone(script: str) -> None:
    """They were written to understand the datasets and produced invalid output."""
    assert not (PROJECT_ROOT / "scripts" / script).exists()
    assert not (PROJECT_ROOT / script).exists()


@pytest.mark.parametrize("probe", EXPLORATORY_PROBES)
def test_probes_retained_as_provenance(probe: str) -> None:
    """The probes are the source for the API behaviour asserted in CLAUDE.md.

    Deleting them would leave those claims unsourced, so they are kept out of the
    pipeline path but retained under ``scripts/exploratory/``.
    """
    assert (PROJECT_ROOT / "scripts" / "exploratory" / probe).is_file()


def test_exploratory_readme_explains_provenance() -> None:
    readme = PROJECT_ROOT / "scripts" / "exploratory" / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8").lower()
    assert "provenance" in text
    assert "data/sample/api" in text


def test_sql_lives_in_files_not_string_literals() -> None:
    """SQL is a deliverable and must be readable on its own."""
    sql_dir = PROJECT_ROOT / "src" / "emissions_allocation" / "sql"
    assert sql_dir.is_dir()
    assert list(sql_dir.glob("*.sql")), "no .sql files found"


def test_invalid_sample_workbook_removed() -> None:
    """Its speed-bin sheet reported identical hours in all seven bins."""
    assert not (PROJECT_ROOT / "data" / "sample" / "gfw_data_sample.xlsx").exists()


def test_captured_api_responses_retained() -> None:
    """These stand in for re-querying the API when a response shape is needed."""
    api_dir = PROJECT_ROOT / "data" / "sample" / "api"
    assert api_dir.is_dir()
    for round_dir in ("round2", "round3", "round4"):
        assert (api_dir / round_dir).is_dir()


def test_notebook_exists_and_has_outputs() -> None:
    """The notebook demonstrates the pipeline; it must ship with results shown."""
    import json

    path = PROJECT_ROOT / "notebooks" / "01_methodology_walkthrough.ipynb"
    assert path.is_file()
    nb = json.loads(path.read_text(encoding="utf-8"))
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert code_cells, "no code cells"
    assert any(c.get("outputs") for c in code_cells), "notebook has no executed output"
    assert not any(
        o.get("output_type") == "error"
        for c in code_cells for o in c.get("outputs", [])
    ), "notebook contains an error output"

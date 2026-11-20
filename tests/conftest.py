from collections.abc import Callable
from pathlib import Path

import pytest

from schema_impact.core.registry import Model, Project, load_project

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def toy_project_path() -> Path:
    """Path to the synthetic toy project used as the test fixture."""
    return REPO_ROOT / "examples" / "toy_project"


@pytest.fixture
def make_project(toy_project_path) -> Callable[[str], Project]:
    """Build a one-model project from a SQL string, using the toy project's tables.

    Lets edge-case tests define their SQL inline instead of adding files to the toy project.
    """
    schema = load_project(toy_project_path).schema

    def build(sql: str, name: str = "m") -> Project:
        model = Model(name=name, sql=sql, path=Path(f"{name}.sql"))
        return Project(models={name: model}, schema=schema, dialect="duckdb")

    return build

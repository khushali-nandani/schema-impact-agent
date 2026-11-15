from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def toy_project_path() -> Path:
    """Path to the synthetic toy project used as the test fixture."""
    return REPO_ROOT / "examples" / "toy_project"

"""The package: one place for the version, a command that reports it, and a wheel that works.

The version lives in `src/schema_impact/__init__.py` and nowhere else (`pyproject.toml` reads it
from there), so a release is one edit and a tag. The wheel tests build the package and run what
was installed from it, not the source tree, which is the only way to see that a module left out of
the package or a missing entry point would break a user. They need `uv build` and are run with
`pytest -m package`.
"""

import re
import subprocess
import sys
import tomllib
import zipfile
from importlib.metadata import version
from pathlib import Path

import pytest

import schema_impact
from schema_impact.cli import main

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


# --- the version --------------------------------------------------------------------------------


def test_the_version_is_read_from_the_package_not_written_twice():
    assert "version" not in PYPROJECT["project"]
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["hatch"]["version"]["path"] == "src/schema_impact/__init__.py"


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", schema_impact.__version__)
    assert schema_impact.__version__ != "0.0.1"


def test_the_installed_metadata_agrees_with_the_package():
    assert version("schema-impact-agent") == schema_impact.__version__


def test_the_command_reports_the_version(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == f"schema-impact {schema_impact.__version__}"


# --- the wheel ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(ROOT)],
        check=True,
        capture_output=True,
    )
    (built,) = out.glob("*.whl")
    return built


@pytest.mark.package
def test_the_wheel_is_named_after_the_version(wheel):
    assert wheel.name == f"schema_impact_agent-{schema_impact.__version__}-py3-none-any.whl"


@pytest.mark.package
def test_the_wheel_has_every_module_of_the_package_and_nothing_else(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    sources = {
        f"schema_impact/{path.relative_to(ROOT / 'src' / 'schema_impact').as_posix()}"
        for path in (ROOT / "src" / "schema_impact").rglob("*.py")
    }
    assert sources <= names
    outside = {n for n in names if not n.startswith(("schema_impact/", "schema_impact_agent-"))}
    assert outside == set()


@pytest.mark.package
def test_the_wheel_declares_the_command(wheel):
    with zipfile.ZipFile(wheel) as archive:
        entry_points = next(n for n in archive.namelist() if n.endswith("entry_points.txt"))
        text = archive.read(entry_points).decode()
    assert "schema-impact = schema_impact.cli:main" in text


@pytest.mark.package
def test_what_is_installed_from_the_wheel_runs(wheel, tmp_path):
    target = tmp_path / "site"
    subprocess.run(
        ["uv", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        check=True,
        capture_output=True,
    )
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(target), "HOME": str(tmp_path)}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args], env=env, capture_output=True, text=True, cwd=tmp_path
        )

    where = run("-c", "import schema_impact; print(schema_impact.__file__)")
    assert where.stdout.strip().startswith(str(target))  # not the source tree
    toy = ROOT / "examples" / "toy_project"
    result = run("-m", "schema_impact", "diff", str(toy), str(toy))
    assert result.returncode == 0, result.stderr
    assert "No downstream impact" in result.stdout
    assert run("-m", "schema_impact", "--version").stdout.strip() == (
        f"schema-impact {schema_impact.__version__}"
    )

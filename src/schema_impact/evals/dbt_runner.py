"""Run dbt on the project and on mutated copies of it, and collect the ground truth.

For each mutation: copy the project, apply the mutation, `dbt seed`, `dbt run`, then
`dbt docs generate` (which compiles every model, including the ones that failed or were
skipped, and catalogs the seeds). Each model that the mutation is judged on is labelled from
its dbt status and a comparison of its output with the unmutated build.

Needs the bench dependency group: `uv sync --group bench`.

Big data files: a project's `data/` folder (the CSV files that dbt-duckdb sources read) is not
copied for every case. The copy gets a real `data/` folder whose files are links to the originals.
A mutation that edits one of them replaces the link with a new file (see `_write_csv` in
mutations.py), so the original is never written to. Symlinks: macOS and Linux only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.registry import Project
from schema_impact.evals.groundtruth import label_model, scored_models, statuses_from_run_results
from schema_impact.evals.mutations import Mutation, MutationError, apply_mutation
from schema_impact.evals.results import Baseline, CaseResult

TIMEOUT_SECONDS = 300
_NOT_COPIED = shutil.ignore_patterns("target", "logs", "*.duckdb", "*.duckdb.wal", ".user.yml")
_LINKED_FOLDERS = ("data",)  # their files are shared between copies, not copied


class DbtError(Exception):
    """dbt could not be run, or did not leave the files the ground truth needs."""


def run_baseline(project_dir: str | Path, workdir: str | Path) -> Baseline:
    """Build the unmutated project in `workdir/baseline` and keep its database."""
    target = Path(workdir) / "baseline"
    shutil.rmtree(target, ignore_errors=True)
    _copy_project(Path(project_dir), target)
    statuses, project = _build(target)
    return Baseline(statuses=statuses, project=project, database=_database(target))


def run_case(
    project_dir: str | Path, baseline: Baseline, mutation: Mutation, workdir: str | Path
) -> CaseResult:
    """Apply one mutation to a fresh copy of the project, build it and label the models."""
    from schema_impact.evals.tablediff import outputs_changed  # needs duckdb: keep import light

    case_dir = Path(workdir) / re.sub(r"[^A-Za-z0-9_.-]", "_", mutation.id)
    shutil.rmtree(case_dir, ignore_errors=True)
    _copy_project(Path(project_dir), case_dir)
    try:
        apply_mutation(mutation, case_dir)
        statuses, after = _build(case_dir)
        labels: dict[str, str] = {}
        for model in scored_models(mutation, baseline.project.models):
            status = statuses.get(model)
            if status is None:
                raise DbtError(f"dbt reported no status for model {model}")
            changed = status == "success" and outputs_changed(
                baseline.database, _database(case_dir), model
            )
            labels[model] = label_model(status, changed)
        return CaseResult(mutation, statuses, labels, after, None)
    except (DbtError, MutationError) as error:
        return CaseResult(mutation, {}, {}, None, str(error))
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)


def _build(project_dir: Path) -> tuple[dict[str, str], Project]:
    """dbt seed, dbt run, dbt docs generate. A failing seed or run is a result, not an error."""
    if _needs_seed(project_dir):
        _dbt(project_dir, "seed")
    run_results = project_dir / "target" / "run_results.json"
    run_results.unlink(missing_ok=True)  # do not mistake the seed's results for the run's
    run = _dbt(project_dir, "run")
    if not run_results.exists():
        raise DbtError(f"dbt run wrote no run_results.json:\n{_tail(run)}")
    # read now: `dbt docs generate` overwrites run_results.json
    statuses = statuses_from_run_results(json.loads(run_results.read_text()))

    docs = _dbt(project_dir, "docs", "generate")
    if docs.returncode != 0:
        raise DbtError(f"dbt docs generate failed:\n{_tail(docs)}")
    return statuses, load_dbt_project(project_dir)


def _dbt(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_dbt_binary(), *args, "--profiles-dir", ".", "--no-use-colors"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env={**os.environ, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"},
    )


def _dbt_binary() -> str:
    beside_python = Path(sys.executable).parent / "dbt"
    binary = str(beside_python) if beside_python.exists() else shutil.which("dbt")
    if binary is None:
        raise DbtError("dbt is not installed. Run `uv sync --group bench`.")
    return binary


def _copy_project(source: Path, destination: Path) -> None:
    """Copy a project for one case. Raises DbtError if its dbt packages were never installed."""
    if (source / "packages.yml").is_file() and not (source / "dbt_packages").is_dir():
        raise DbtError(f"{source} needs dbt packages. Run `dbt deps` in it first.")

    def ignore(folder: str, names: list[str]) -> set[str]:
        skipped = set(_NOT_COPIED(folder, names))
        if Path(folder) == source:  # only the project's own data folder, not any other `data`
            skipped.update(name for name in names if name in _LINKED_FOLDERS)
        return skipped

    shutil.copytree(source, destination, ignore=ignore)
    for folder in _LINKED_FOLDERS:
        _link_files(source / folder, destination / folder)


def _link_files(source: Path, destination: Path) -> None:
    """A real folder `destination` whose files are links to the files of `source`."""
    if not source.is_dir():
        return
    destination.mkdir()
    for file in sorted(source.iterdir()):
        (destination / file.name).symlink_to(file.resolve())


def _needs_seed(project_dir: Path) -> bool:
    """Whether the project has any seed file; `dbt seed` has nothing to do otherwise."""
    return any((project_dir / "seeds").rglob("*.csv"))


def _database(project_dir: Path) -> Path:
    files = sorted(project_dir.glob("*.duckdb"))
    if len(files) != 1:
        raise DbtError(f"Expected one DuckDB file in {project_dir}, found {len(files)}")
    return files[0]


def _tail(process: subprocess.CompletedProcess[str], lines: int = 15) -> str:
    return "\n".join((process.stdout + process.stderr).strip().splitlines()[-lines:])

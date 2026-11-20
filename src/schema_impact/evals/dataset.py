"""The benchmark dataset: for every mutation, what dbt says happened.

Building it runs dbt once per mutation (slow, needs `uv sync --group bench`), so the result is
written to a JSON file and committed. Scoring then reads the file and never has to run dbt.

The file is written after every case, so an interrupted build loses at most one case, and running
it again only does the cases that are missing (or ended in an error).
"""

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

from schema_impact.core.registry import Model, Project
from schema_impact.evals.mutations import Mutation, Recipe, generate_mutations
from schema_impact.evals.results import Baseline, CaseResult


@dataclass(frozen=True)
class Dataset:
    meta: dict[str, Any]
    baseline_statuses: dict[str, str]
    baseline_project: Project
    cases: list[CaseResult]


# --- JSON <-> objects --------------------------------------------------------


def project_to_dict(project: Project) -> dict[str, Any]:
    return {
        "dialect": project.dialect,
        "schema": project.schema,
        "models": {
            name: {"sql": model.sql, "path": str(model.path)}
            for name, model in project.models.items()
        },
    }


def project_from_dict(data: dict[str, Any]) -> Project:
    return Project(
        models={
            name: Model(name, model["sql"], Path(model["path"]))
            for name, model in data["models"].items()
        },
        schema=data["schema"],
        dialect=data["dialect"],
    )


def case_to_dict(case: CaseResult) -> dict[str, Any]:
    return {
        "mutation": case.mutation.to_dict(),
        "statuses": case.statuses,
        "labels": case.labels,
        "error": case.error,
        "after": project_to_dict(case.after) if case.after else None,
    }


def case_from_dict(data: dict[str, Any]) -> CaseResult:
    return CaseResult(
        mutation=Mutation.from_dict(data["mutation"]),
        statuses=data["statuses"],
        labels=data["labels"],
        after=project_from_dict(data["after"]) if data["after"] else None,
        error=data["error"],
    )


def load_dataset(path: str | Path) -> Dataset:
    data = json.loads(Path(path).read_text())
    return Dataset(
        meta=data["meta"],
        baseline_statuses=data["baseline"]["statuses"],
        baseline_project=project_from_dict(data["baseline"]["project"]),
        cases=[case_from_dict(case) for case in data["cases"]],
    )


# --- building ---------------------------------------------------------------


def build_dataset(
    project_dir: str | Path,
    out_path: str | Path,
    recipes: Sequence[Recipe] = (),
    mutations: Sequence[Mutation] | None = None,
    workdir: str | Path | None = None,
    limit: int | None = None,
    run_baseline: Callable[..., Baseline] | None = None,
    run_case: Callable[..., CaseResult] | None = None,
    progress: Callable[[str], None] = print,
) -> Dataset:
    """Run dbt on the project and on each mutation and save what happened to `out_path`.

    `mutations` defaults to every mutation generated for the project. `limit` builds only the
    first N. `run_baseline` and `run_case` default to the real dbt runner; tests pass fakes.
    """
    if run_baseline is None or run_case is None:
        from schema_impact.evals import dbt_runner  # needs dbt and DuckDB

        run_baseline = run_baseline or dbt_runner.run_baseline
        run_case = run_case or dbt_runner.run_case

    out_path = Path(out_path)
    own_workdir = workdir is None
    workdir = Path(tempfile.mkdtemp(prefix="schema_impact_bench_") if own_workdir else workdir)
    try:
        baseline = run_baseline(project_dir, workdir)
        if mutations is None:
            mutations = generate_mutations(baseline.database.parent, recipes)
        selected = list(mutations if limit is None else mutations[:limit])

        results = _already_done(out_path, baseline)
        todo = [m for m in selected if m.id not in results]
        progress(f"{len(selected)} mutations, {len(selected) - len(todo)} already done")

        for number, mutation in enumerate(todo, start=1):
            started = time.monotonic()
            results[mutation.id] = run_case(project_dir, baseline, mutation, workdir)
            _save(out_path, baseline, [results[m.id] for m in selected if m.id in results])
            progress(
                f"[{number}/{len(todo)}] {mutation.id}: {_summary(results[mutation.id])} "
                f"({time.monotonic() - started:.1f}s)"
            )
        return load_dataset(out_path)
    finally:
        if own_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _already_done(out_path: Path, baseline: Baseline) -> dict[str, CaseResult]:
    """Cases from an earlier, interrupted build. Cases that ended in an error are tried again."""
    if not out_path.exists():
        return {}
    saved = load_dataset(out_path)
    if saved.baseline_project != baseline.project:
        raise ValueError(f"{out_path} was built from a different project; delete it to start again")
    return {case.mutation.id: case for case in saved.cases if case.error is None}


def _save(out_path: Path, baseline: Baseline, cases: list[CaseResult]) -> None:
    data = {
        "meta": _meta(len(cases)),
        "baseline": {
            "statuses": baseline.statuses,
            "project": project_to_dict(baseline.project),
        },
        "cases": [case_to_dict(case) for case in cases],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(out_path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=1) + "\n")
    os.replace(temporary, out_path)  # never leaves a half-written file behind


def _meta(cases: int) -> dict[str, Any]:
    def version(package: str) -> str | None:
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            return None

    return {
        "generated": date.today().isoformat(),
        "mutations": cases,
        "dbt_core": version("dbt-core"),
        "dbt_duckdb": version("dbt-duckdb"),
        "duckdb": version("duckdb"),
    }


def _summary(case: CaseResult) -> str:
    if case.error:
        return f"ERROR {case.error.splitlines()[0][:80]}"
    counts: dict[str, int] = {}
    for label in case.labels.values():
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{n} {label}" for label, n in sorted(counts.items())) or "no scored models"

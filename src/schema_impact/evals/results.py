"""What running dbt on a project and on its mutations produces.

Kept apart from `dbt_runner` so that code which only reads results (the dataset, the scorer)
does not need dbt or DuckDB installed.
"""

from dataclasses import dataclass
from pathlib import Path

from schema_impact.core.registry import Project
from schema_impact.evals.mutations import Mutation


@dataclass(frozen=True)
class Baseline:
    """The unmutated project after a build."""

    statuses: dict[str, str]  # model -> dbt status
    project: Project  # as read back from dbt's artifacts
    database: Path  # the DuckDB file the build wrote


@dataclass(frozen=True)
class CaseResult:
    mutation: Mutation
    statuses: dict[str, str]  # model -> dbt status ("success", "error", "skipped")
    labels: dict[str, str]  # model -> breaking / silent / safe, for the scored models
    after: Project | None  # the project after the mutation, from dbt's artifacts
    error: str | None  # why the case could not be evaluated; then labels and after are empty

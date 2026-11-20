"""Ground truth: what a mutation really does, as measured by running dbt.

A mutation is applied to a copy of the project, dbt builds it, and each model gets a label:

- breaking  the model failed, or was skipped because something upstream failed
- silent    the model built, but its output (rows or columns) is different from before
- safe      the model built and its output is the same as before

Tests (`dbt test`) are not part of this: a test that starts failing is a different question
from whether the pipeline breaks or its numbers change.

Known limit: "same output" is judged on the project's own seed data. A change that happens to
leave this small data unchanged is labelled safe even though other data could expose it.
"""

from collections.abc import Iterable

from schema_impact.evals.mutations import (
    MODEL_DROP_COLUMN,
    MODEL_LOGIC,
    MODEL_RENAME_COLUMN,
    Mutation,
)

BREAKING = "breaking"
SILENT = "silent"
SAFE = "safe"

_FAILED = {"error", "skipped"}
_MODEL_KINDS = {MODEL_RENAME_COLUMN, MODEL_DROP_COLUMN, MODEL_LOGIC}


def label_model(status: str, output_changed: bool) -> str:
    """The label of one model, from its dbt status and whether its output changed."""
    if status in _FAILED:
        return BREAKING
    if status == "success":
        return SILENT if output_changed else SAFE
    raise ValueError(f"Unknown dbt status {status!r}")


def statuses_from_run_results(run_results: dict) -> dict[str, str]:
    """Model name -> dbt status, from the parsed contents of run_results.json."""
    prefix = "model."
    return {
        result["unique_id"].rsplit(".", 1)[-1]: result["status"]
        for result in run_results["results"]
        if result["unique_id"].startswith(prefix)
    }


def mutated_model(mutation: Mutation) -> str | None:
    """The model whose source the mutation edits, or None for a seed mutation."""
    if mutation.kind not in _MODEL_KINDS:
        return None
    return mutation.target.split(".")[0]


def scored_models(mutation: Mutation, models: Iterable[str]) -> list[str]:
    """The models a mutation is judged on.

    The model a mutation edits is left out: its output changes by construction, so it is the
    change itself and not an impact of it.
    """
    edited = mutated_model(mutation)
    return [model for model in models if model != edited]

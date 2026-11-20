"""Impact analysis: which models and columns a change affects.

Pass the project as it is *before* the change: its SQL is what refers to the old columns,
so its lineage tells us who is affected.

Effects (deterministic, no LLM):
- breaking:      the change makes affected queries fail (rename, drop, dropped table)
- needs_review:  the queries still run but results may differ (type change, changed logic);
                 a later layer decides how risky that is

A change with no impacts is safe. The effect of a change is passed on unchanged to every
model further down the chain.
"""

from collections import deque
from dataclasses import dataclass

from schema_impact.core.changes import (
    Change,
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    TableDropped,
)
from schema_impact.core.lineage import ColumnRef, Edge, build_lineage
from schema_impact.core.registry import Project

BREAKING = "breaking"
NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class Impact:
    """One affected place caused by one change."""

    change: Change
    model: str
    column: str | None  # affected output column; None means the whole model
    roles: frozenset[str]  # how the model uses what changed: select, join, where, ...
    effect: str  # BREAKING or NEEDS_REVIEW
    path: tuple[ColumnRef, ...]  # from the changed column to this place


def compute_impact(project: Project, changes: list[Change]) -> list[Impact]:
    """Find every place affected by each change, following chains of models."""
    edges = build_lineage(project)
    by_source: dict[ColumnRef, list[Edge]] = {}
    model_columns: dict[str, list[str]] = {}
    for edge in edges:
        by_source.setdefault(edge.source, []).append(edge)
        if edge.target.column is not None:
            columns = model_columns.setdefault(edge.target.table, [])
            if edge.target.column not in columns:
                columns.append(edge.target.column)

    impacts: list[Impact] = []
    for change in changes:
        seeds = _starting_columns(change, project, model_columns)
        impacts.extend(_follow(change, seeds, by_source, model_columns))
    return impacts


def _starting_columns(
    change: Change, project: Project, model_columns: dict[str, list[str]]
) -> list[ColumnRef]:
    """The columns whose meaning or existence the change touches."""
    match change:
        case ColumnRenamed(table, old, _):
            return [ColumnRef(table, old)]
        case ColumnDropped(table, column) | ColumnTypeChanged(table, column, _, _):
            return [ColumnRef(table, column)]
        case TableDropped(table):
            return [ColumnRef(table, column) for column in project.schema.get(table, {})]
        case ModelChanged(model):
            return [ColumnRef(model, column) for column in model_columns.get(model, [])]
    raise TypeError(f"Unknown change: {change!r}")


def _effect_of(change: Change) -> str:
    if isinstance(change, ColumnRenamed | ColumnDropped | TableDropped):
        return BREAKING
    return NEEDS_REVIEW


def _follow(
    change: Change,
    seeds: list[ColumnRef],
    by_source: dict[ColumnRef, list[Edge]],
    model_columns: dict[str, list[str]],
) -> list[Impact]:
    """Walk the lineage graph breadth first from the changed columns."""
    found: dict[tuple[str, str | None], tuple[set[str], tuple[ColumnRef, ...]]] = {}
    visited = set(seeds)
    queue = deque((seed, (seed,)) for seed in seeds)

    while queue:
        source, path = queue.popleft()
        for edge in by_source.get(source, []):
            target = edge.target
            key = (target.table, target.column)
            if key not in found:
                found[key] = (set(), (*path, target))
            found[key][0].add(edge.kind)

            # A model-level target (column None) means the whole model is affected, so
            # every column of that model carries the effect on to whoever reads it.
            if target.column is None:
                onward = [ColumnRef(target.table, c) for c in model_columns.get(target.table, [])]
            else:
                onward = [target]
            for ref in onward:
                if ref not in visited:
                    visited.add(ref)
                    queue.append((ref, (*path, target)))

    effect = _effect_of(change)
    return [
        Impact(change, model, column, frozenset(roles), effect, path)
        for (model, column), (roles, path) in sorted(
            found.items(), key=lambda item: (item[0][0], item[0][1] is not None, item[0][1] or "")
        )
    ]

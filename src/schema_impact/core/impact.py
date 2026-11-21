"""Impact analysis: which models and columns a change affects.

Pass the project as it is *before* the change: its SQL is what refers to the old columns,
so its lineage tells us who is affected.

Effects (deterministic, no LLM):
- breaking:      the change makes affected queries fail (rename, drop, dropped table
                 or model)
- needs_review:  the queries still run but results may differ (type change, changed logic);
                 a later layer decides how risky that is

A change with no impacts is safe. The effect of a change is passed on unchanged to every
model further down the chain.

A breaking change makes the query of an affected model fail, so that model cannot be built and
every model that reads it is affected too, whichever of its columns they use. A change that only
needs review follows the columns: only what is computed from the changed column is affected.

One kind of review turns into a failure: a column that becomes text. A model that only selects it
is fine, but `SUM(amount)`, `amount / 100` or `date_diff(...)` on text fail on DuckDB, where this
was measured. The text type travels along columns that are passed on as they are, and a model that
gives it to one of those fails like a breaking change does. A cast on the way stops it.
"""

from collections import deque
from dataclasses import dataclass, replace

from schema_impact.core.changes import (
    Change,
    ColumnDropped,
    ColumnLogicChanged,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    ModelDropped,
    TableDropped,
)
from schema_impact.core.columnuse import PASS, STRICT, column_uses
from schema_impact.core.lineage import SELECT, ColumnRef, Edge, build_lineage
from schema_impact.core.registry import Project

BREAKING = "breaking"
NEEDS_REVIEW = "needs_review"

# Where giving text to SUM, arithmetic or date_diff is an error. Only DuckDB has been measured;
# others, Snowflake for one, convert text to numbers on the fly.
_TEXT_FAILS_IN = {"duckdb"}
_TEXT_TYPES = {"VARCHAR", "TEXT", "STRING", "CHAR", "NVARCHAR", "BPCHAR"}


@dataclass(frozen=True)
class Impact:
    """One affected place caused by one change."""

    change: Change
    model: str
    column: str | None  # affected output column; None means the whole model
    roles: frozenset[str]  # how the model uses what changed: select, join, where, ...
    effect: str  # BREAKING or NEEDS_REVIEW
    path: tuple[ColumnRef, ...]  # from the changed column to this place

    def __post_init__(self) -> None:
        # Always a tuple, so the impact stays hashable even when it was rebuilt from a
        # checkpoint, which hands tuples back as lists.
        object.__setattr__(self, "path", tuple(self.path))


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

    uses: dict[tuple[ColumnRef, ColumnRef], str] | None = None
    impacts: list[Impact] = []
    for change in changes:
        seeds = _starting_columns(change, project, model_columns)
        found = _follow(change, seeds, by_source, model_columns, _effect_of(change))
        if _becomes_text(change) and project.dialect in _TEXT_FAILS_IN:
            uses = column_uses(project) if uses is None else uses
            found = _models_that_fail_on_text(change, seeds, found, by_source, model_columns, uses)
        impacts.extend(found)
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
        case ColumnLogicChanged(model, column):
            return [ColumnRef(model, column)]
        case TableDropped(table):
            return [ColumnRef(table, column) for column in project.schema.get(table, {})]
        case ModelChanged(model) | ModelDropped(model):
            return [ColumnRef(model, column) for column in model_columns.get(model, [])]
    raise TypeError(f"Unknown change: {change!r}")


def _effect_of(change: Change) -> str:
    if isinstance(change, ColumnRenamed | ColumnDropped | TableDropped | ModelDropped):
        return BREAKING
    return NEEDS_REVIEW


def _is_text(type_name: str) -> bool:
    return type_name.upper().split("(")[0].strip() in _TEXT_TYPES


def _becomes_text(change: Change) -> bool:
    return (
        isinstance(change, ColumnTypeChanged)
        and _is_text(change.new_type)
        and not _is_text(change.old_type)
    )


def _models_that_fail_on_text(
    change: Change,
    seeds: list[ColumnRef],
    impacts: list[Impact],
    by_source: dict[ColumnRef, list[Edge]],
    model_columns: dict[str, list[str]],
    uses: dict[tuple[ColumnRef, ColumnRef], str],
) -> list[Impact]:
    """Make the models that give the text column to SUM, arithmetic or date_diff breaking.

    The text type travels along the columns that are passed on unchanged (PASS). A model that
    gives it to a strict use cannot be built, so it and everything that reads it is breaking,
    whichever columns they use. A use that is neither (a cast, ROUND, COUNT, ...) ends the trail.
    """
    failing: set[str] = set()
    typed = set(seeds)
    queue = deque(seeds)
    while queue:
        source = queue.popleft()
        for edge in by_source.get(source, []):
            if edge.kind != SELECT:
                continue
            use = uses.get((edge.source, edge.target))
            if use == STRICT:
                failing.add(edge.target.table)
            elif use == PASS and edge.target not in typed:
                typed.add(edge.target)
                queue.append(edge.target)
    if not failing:
        return impacts

    down = {}  # what the failing models take with them, by (model, column)
    for model in sorted(failing):
        columns = [ColumnRef(model, c) for c in model_columns.get(model, [])]
        for impact in _follow(change, columns, by_source, model_columns, BREAKING):
            down[(impact.model, impact.column)] = impact
    broken = failing | {model for model, _ in down}

    merged = {
        (i.model, i.column): replace(i, effect=BREAKING) if i.model in broken else i
        for i in impacts
    }
    merged.update(down)
    return [merged[key] for key in sorted(merged, key=_impact_order)]


def _impact_order(key: tuple[str, str | None]) -> tuple[str, bool, str]:
    model, column = key
    return (model, column is not None, column or "")


def _follow(
    change: Change,
    seeds: list[ColumnRef],
    by_source: dict[ColumnRef, list[Edge]],
    model_columns: dict[str, list[str]],
    effect: str,
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

            # The whole model carries the effect on, so every one of its columns does, when
            # - the target is model-level (column None): a join, filter or group by changed, or
            # - the change is breaking: the model's query cannot run at all, so every model that
            #   reads it is skipped, whichever of its columns it uses.
            # A change that only needs review (a type change) follows just the column.
            if target.column is None or effect == BREAKING:
                onward = [ColumnRef(target.table, c) for c in model_columns.get(target.table, [])]
            else:
                onward = [target]
            for ref in onward:
                if ref not in visited:
                    visited.add(ref)
                    queue.append((ref, (*path, target)))

    return [
        Impact(change, model, column, frozenset(roles), effect, path)
        for (model, column), (roles, path) in sorted(
            found.items(), key=lambda item: _impact_order(item[0])
        )
    ]

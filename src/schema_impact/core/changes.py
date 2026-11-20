"""Detect what changed between a "before" and an "after" project.

Detected: renamed / dropped columns, column type changes, dropped tables, dropped models,
and models whose SQL changed in a way that is not just formatting.

Not detected: added columns, tables and models (they cannot break anything downstream).
"""

from dataclasses import dataclass

import sqlglot

from schema_impact.core.lineage import (
    UNKNOWN_TYPE,
    CircularDependencyError,
    TopSelect,
    _in_dependency_order,
    analyse_top_select,
    model_outputs,
    model_top_selects,
    output_columns_best_effort,
)
from schema_impact.core.registry import Model, Project, Schema


@dataclass(frozen=True)
class ColumnRenamed:
    table: str
    old: str
    new: str


@dataclass(frozen=True)
class ColumnDropped:
    table: str
    column: str


@dataclass(frozen=True)
class ColumnTypeChanged:
    table: str
    column: str
    old_type: str
    new_type: str


@dataclass(frozen=True)
class TableDropped:
    table: str


@dataclass(frozen=True)
class ModelChanged:
    model: str


@dataclass(frozen=True)
class ModelDropped:
    model: str


Change = (
    ColumnRenamed | ColumnDropped | ColumnTypeChanged | TableDropped | ModelChanged | ModelDropped
)


def diff_projects(before: Project, after: Project) -> list[Change]:
    """List the changes that turn `before` into `after`."""
    changes: list[Change] = []
    changes.extend(_schema_changes(before.schema, after.schema))
    changes.extend(_model_changes(before, after))
    return changes


def _schema_changes(before: Schema, after: Schema) -> list[Change]:
    changes: list[Change] = []

    for table, old_columns in before.items():
        if table not in after:
            changes.append(TableDropped(table))
            continue

        new_columns = after[table]
        old_names, new_names = list(old_columns), list(new_columns)
        added = [name for name in new_names if name not in old_columns]
        used_as_rename: set[str] = set()

        for position, name in enumerate(old_names):
            if name in new_columns:
                if old_columns[name] != new_columns[name]:
                    changes.append(
                        ColumnTypeChanged(table, name, old_columns[name], new_columns[name])
                    )
                continue

            candidate = new_names[position] if position < len(new_names) else None
            is_rename = (
                candidate in added
                and candidate not in used_as_rename
                and new_columns[candidate] == old_columns[name]
            )
            if is_rename:
                used_as_rename.add(candidate)
                changes.append(ColumnRenamed(table, name, candidate))
            else:
                changes.append(ColumnDropped(table, name))

    return changes


def _model_changes(before: Project, after: Project) -> list[Change]:
    changes: list[Change] = []
    edited: list[str] = []
    for name, model in before.models.items():
        if name not in after.models:
            changes.append(ModelDropped(name))
            continue
        old = _normalized(model.sql, before.dialect)
        new = _normalized(after.models[name].sql, after.dialect)
        if old != new:
            edited.append(name)
    changes.extend(_edited_model_changes(before, after, edited))
    return changes


def _edited_model_changes(before: Project, after: Project, edited: list[str]) -> list[Change]:
    """What changed in the models whose SQL was edited.

    A renamed or dropped output column is a column change. The model as a whole is reported as
    changed unless the edit only changed the shape of the output: the rest of the query is the
    same and the columns that remain are computed the same way. Then the models that read it are
    only affected through the columns that changed.

    Only edited models are looked at: a model that was not edited cannot have changed its own
    columns, and its SQL may no longer resolve against the new schema. When the new SQL of an
    edited model cannot be resolved, anything may have changed, so it is reported as changed.
    """
    if not edited:
        return []
    outputs = model_outputs(before)
    before_tops = model_top_selects(before)
    # the schema the new SQL is read against: the source tables as they are now, and the output
    # columns of the models, as they were or, once an edited model has been read, as they are now
    schema: Schema = {table: dict(columns) for table, columns in after.schema.items()}
    for name, columns in outputs.items():
        schema[name] = dict.fromkeys(columns, UNKNOWN_TYPE)

    changes: list[Change] = []
    for model in _models_in_order(after):
        if model.name not in edited:
            continue
        top = analyse_top_select(model.sql, schema, after.dialect)
        new_columns = (
            top.names if top else output_columns_best_effort(model.sql, schema, after.dialect)
        )

        column_changes: list[Change] = []
        if new_columns is not None:
            schema[model.name] = dict.fromkeys(new_columns, UNKNOWN_TYPE)
            column_changes = _schema_changes(
                {model.name: dict.fromkeys(outputs[model.name], UNKNOWN_TYPE)},
                {model.name: dict.fromkeys(new_columns, UNKNOWN_TYPE)},
            )
        if not _only_the_output_shape_changed(before_tops.get(model.name), top, column_changes):
            changes.append(ModelChanged(model.name))
        changes.extend(column_changes)
    return changes


def _only_the_output_shape_changed(
    before: TopSelect | None, after: TopSelect | None, column_changes: list[Change]
) -> bool:
    """True if the edit left the query alone and only changed which columns it outputs."""
    if before is None or after is None or before.body != after.body:
        return False
    renames = {c.old: c.new for c in column_changes if isinstance(c, ColumnRenamed)}
    for name, expression in before.projections.items():
        new_name = renames.get(name, name)
        if new_name in after.projections and after.projections[new_name] != expression:
            return False
    return True


def _models_in_order(project: Project) -> list[Model]:
    try:
        return _in_dependency_order(project)
    except CircularDependencyError:
        return [project.models[name] for name in sorted(project.models)]


def _normalized(sql: str, dialect: str) -> str:
    """SQL with whitespace and comments stripped, so formatting changes compare equal."""
    return sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect, comments=False)

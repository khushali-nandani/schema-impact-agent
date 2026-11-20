"""Detect what changed between a "before" and an "after" project.

Detected: renamed / dropped columns, column type changes, dropped tables, and models whose
SQL changed in a way that is not just formatting.

Not detected: added columns (they cannot break anything downstream), and models or tables
that were added or removed as a whole model (only dropped tables are reported).
"""

from dataclasses import dataclass

import sqlglot

from schema_impact.core.registry import Project, Schema


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


Change = ColumnRenamed | ColumnDropped | ColumnTypeChanged | TableDropped | ModelChanged


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
    for name, model in before.models.items():
        if name not in after.models:
            continue
        old = _normalized(model.sql, before.dialect)
        new = _normalized(after.models[name].sql, after.dialect)
        if old != new:
            changes.append(ModelChanged(name))
    return changes


def _normalized(sql: str, dialect: str) -> str:
    """SQL with whitespace and comments stripped, so formatting changes compare equal."""
    return sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect, comments=False)

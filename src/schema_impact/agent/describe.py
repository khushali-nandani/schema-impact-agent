"""Plain-language descriptions of changes and impacts.

Everything here is deterministic. It is used for reports, for the explanation of breaking
impacts (no model needed), and later as the facts handed to an LLM judge.
"""

from schema_impact.core.changes import (
    Change,
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    ModelDropped,
    TableDropped,
)
from schema_impact.core.impact import Impact
from schema_impact.core.lineage import ColumnRef


def describe_change(change: Change) -> str:
    match change:
        case ColumnRenamed(table, old, new):
            return f"Column {table}.{old} was renamed to {new}"
        case ColumnDropped(table, column):
            return f"Column {table}.{column} was dropped"
        case ColumnTypeChanged(table, column, old_type, new_type):
            return f"Type of {table}.{column} changed from {old_type} to {new_type}"
        case TableDropped(table):
            return f"Table {table} was dropped"
        case ModelDropped(model):
            return f"Model {model} was dropped"
        case ModelChanged(model):
            return f"The SQL of model {model} changed"
    raise TypeError(f"Unknown change: {change!r}")


def describe_place(impact: Impact) -> str:
    """The affected place: a column, or a whole model."""
    if impact.column is None:
        return f"{impact.model} (whole model)"
    return f"{impact.model}.{impact.column}"


def describe_roles(impact: Impact) -> str:
    return ", ".join(sorted(impact.roles))


def breaking_explanation(impact: Impact) -> str:
    """Why a breaking impact fails. No model is needed: the column or table is simply gone."""
    return (
        f"{describe_change(impact.change)}, and {describe_place(impact)} depends on it "
        f"({describe_roles(impact)}), so it will fail."
    )


def breaking_fix(change: Change) -> str:
    match change:
        case ColumnRenamed(table, old, new):
            return f"Update the SQL that uses {table}.{old} to use {new}."
        case ColumnDropped(table, column):
            return f"Restore {table}.{column}, or change the SQL that uses it."
        case TableDropped(table):
            return f"Restore table {table}, or update every model that reads it."
        case ModelDropped(model):
            return f"Restore model {model}, or update every model that reads it."
    return "Review the affected SQL."


def describe_path(impact: Impact) -> str:
    """The chain from the changed column to the affected place."""
    return " -> ".join(_describe_ref(ref) for ref in impact.path)


def _describe_ref(ref: ColumnRef) -> str:
    if ref.column is None:
        return f"{ref.table} (whole model)"
    return f"{ref.table}.{ref.column}"

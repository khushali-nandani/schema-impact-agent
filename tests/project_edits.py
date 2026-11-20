"""Helpers that build an "after" project from a "before" project, for change tests.

Each function returns a new Project and leaves the original untouched.
"""

from dataclasses import replace
from pathlib import Path

from schema_impact.core.registry import Model, Project


def _schema_copy(project: Project) -> dict[str, dict[str, str]]:
    return {table: dict(columns) for table, columns in project.schema.items()}


def rename_column(project: Project, table: str, old: str, new: str) -> Project:
    """Rename a column, keeping its position and type."""
    schema = _schema_copy(project)
    schema[table] = {(new if c == old else c): t for c, t in schema[table].items()}
    return replace(project, schema=schema)


def drop_column(project: Project, table: str, column: str) -> Project:
    schema = _schema_copy(project)
    del schema[table][column]
    return replace(project, schema=schema)


def add_column(project: Project, table: str, column: str, type_: str) -> Project:
    """Add a column at the end of the table."""
    schema = _schema_copy(project)
    schema[table][column] = type_
    return replace(project, schema=schema)


def change_type(project: Project, table: str, column: str, new_type: str) -> Project:
    schema = _schema_copy(project)
    schema[table][column] = new_type
    return replace(project, schema=schema)


def drop_table(project: Project, table: str) -> Project:
    schema = _schema_copy(project)
    del schema[table]
    return replace(project, schema=schema)


def change_model_sql(project: Project, model: str, sql: str) -> Project:
    models = dict(project.models)
    models[model] = Model(name=model, sql=sql, path=models[model].path)
    return replace(project, models=models)


def drop_model(project: Project, model: str) -> Project:
    models = dict(project.models)
    del models[model]
    return replace(project, models=models)


def add_model(project: Project, model: str, sql: str) -> Project:
    models = dict(project.models)
    models[model] = Model(name=model, sql=sql, path=Path(f"{model}.sql"))
    return replace(project, models=models)

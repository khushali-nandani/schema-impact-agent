"""Load a SQL project folder into a registry of models and a table schema.

Expected layout (see examples/toy_project):

    <root>/schema.sql        CREATE TABLE statements for the source tables
    <root>/models/*.sql      one SELECT per file; the file name is the model name
"""

from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp

DEFAULT_DIALECT = "duckdb"

# table name -> {column name -> type}, in the shape sqlglot expects for a schema
Schema = dict[str, dict[str, str]]


@dataclass(frozen=True)
class Model:
    name: str
    sql: str
    path: Path


@dataclass(frozen=True)
class Project:
    models: dict[str, Model]
    schema: Schema
    dialect: str


def load_project(root: str | Path, dialect: str = DEFAULT_DIALECT) -> Project:
    """Read models and schema from a project folder."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Project folder not found: {root}")

    models_dir = root / "models"
    if not models_dir.is_dir():
        raise FileNotFoundError(f"Models folder not found: {models_dir}")

    return Project(
        models=_load_models(models_dir),
        schema=_load_schema(root / "schema.sql", dialect),
        dialect=dialect,
    )


def _load_models(models_dir: Path) -> dict[str, Model]:
    models: dict[str, Model] = {}
    for path in sorted(models_dir.glob("*.sql")):
        models[path.stem] = Model(name=path.stem, sql=path.read_text(), path=path)
    return models


def _load_schema(schema_path: Path, dialect: str) -> Schema:
    """Parse CREATE TABLE statements. A missing schema file means an empty schema."""
    if not schema_path.is_file():
        return {}

    schema: Schema = {}
    for statement in sqlglot.parse(schema_path.read_text(), read=dialect):
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        table = statement.this
        if not isinstance(table, exp.Schema):
            continue
        schema[table.this.name] = {
            column.name: column.args["kind"].sql(dialect=dialect)
            for column in table.expressions
            if isinstance(column, exp.ColumnDef)
        }
    return schema

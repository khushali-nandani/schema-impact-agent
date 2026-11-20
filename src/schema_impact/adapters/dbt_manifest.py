"""Load a dbt project into a `Project` from the artifacts dbt writes to `target/`.

dbt models are Jinja templates (`{{ ref('x') }}`, loops, macros), not plain SQL, so they cannot be
parsed directly. dbt has already done the hard part: after `dbt compile` (or `dbt run`) every
model's final SQL is stored in `target/manifest.json` as `compiled_code`. The columns of the
source tables (seeds and dbt sources) are in `target/catalog.json`, written by
`dbt docs generate`.

    dbt seed && dbt run && dbt docs generate
    project = load_dbt_project("path/to/dbt_project")

CSV sources: with dbt-duckdb a source can be a file (`external_location`). dbt compiles it to
`select * from 'data/orders.csv'`, a table named by its path, and `dbt docs generate` lists no
columns for it. Such a file is named `<source>__<table>` (e.g. `olist__orders`) in the models,
and its columns and types are read from the file with DuckDB. That needs the `duckdb` package and
the CSV files in place.

Limits: ephemeral models are inlined by dbt into their readers and are loaded like any other model.
Table and column names are used as dbt writes them; no case folding is done.
"""

import json
from pathlib import Path

import sqlglot
from sqlglot import exp

from schema_impact.core.registry import DEFAULT_DIALECT, Model, Project, Schema

_SOURCE_PREFIXES = ("seed.", "source.")


def load_dbt_project(project_dir: str | Path, dialect: str = DEFAULT_DIALECT) -> Project:
    """Read models (compiled SQL) and source tables (columns) from a dbt project's target/."""
    project_dir = Path(project_dir)
    target = project_dir / "target"
    manifest = _read_json(target / "manifest.json", "Run `dbt compile` (or `dbt run`) first.")
    catalog = _read_json(target / "catalog.json", "Run `dbt docs generate` first.")
    csv_sources = _csv_sources(manifest)
    schema = _schema(catalog)
    for name, path in csv_sources.items():
        schema[name] = _csv_columns(name, project_dir / path)
    return Project(
        models=_models(manifest, dialect, {path: name for name, path in csv_sources.items()}),
        schema=schema,
        dialect=dialect,
    )


def _read_json(path: Path, hint: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. {hint}")
    return json.loads(path.read_text())


def _models(manifest: dict, dialect: str, csv_names: dict[str, str]) -> dict[str, Model]:
    models: dict[str, Model] = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] != "model":
            continue
        # other models read this one by its relation name, which is the alias if there is one
        name = node.get("alias") or node["name"]
        sql = node.get("compiled_code")
        if not sql:
            raise ValueError(
                f"Model {name} has no compiled_code in manifest.json. Run `dbt compile` first."
            )
        models[name] = Model(
            name=name,
            sql=_without_database_and_schema(sql, dialect, csv_names),
            path=Path(node["original_file_path"]),
        )
    return dict(sorted(models.items()))


def _schema(catalog: dict) -> Schema:
    """Seeds and dbt sources are the tables the models read from."""
    schema: Schema = {}
    for unique_id, node in catalog["nodes"].items():
        if not unique_id.startswith(_SOURCE_PREFIXES):
            continue
        columns = sorted(node["columns"].values(), key=lambda column: column["index"])
        schema[node["metadata"]["name"]] = {c["name"]: c["type"] for c in columns}
    return schema


def _csv_sources(manifest: dict) -> dict[str, str]:
    """Sources that are files: {name used in the models: path of the file}.

    dbt gives such a source the file path, in single quotes, as its relation name
    (`'data/orders.csv'`); a source that is a table has a dotted, double-quoted name instead.
    """
    files: dict[str, str] = {}
    for source in manifest.get("sources", {}).values():
        relation = source.get("relation_name") or ""
        if len(relation) > 2 and relation[0] == relation[-1] == "'":
            files[f"{source['source_name']}__{source['name']}"] = relation[1:-1]
    return dict(sorted(files.items()))


def _csv_columns(name: str, path: Path) -> dict[str, str]:
    """Column names and types of a CSV file, in file order, as DuckDB reads them."""
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. The source {name} is read from this file, so it must be there."
        )
    try:
        import duckdb
    except ImportError as error:
        raise ImportError(
            f"Reading the columns of {path.name} needs duckdb: `uv sync --group bench`."
        ) from error
    connection = duckdb.connect()
    try:
        rows = connection.execute("DESCRIBE SELECT * FROM read_csv(?)", [str(path)]).fetchall()
    finally:
        connection.close()
    return {column: column_type for column, column_type, *_ in rows}


def _without_database_and_schema(sql: str, dialect: str, csv_names: dict[str, str]) -> str:
    """dbt writes "jaffle_shop"."main"."stg_orders"; the rest of the tool knows it as stg_orders.

    A table that is the path of a CSV file gets the name of its source instead.
    The SQL comes back from sqlglot, so keywords are normalised to upper case.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    for table in tree.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
        if table.name in csv_names:
            table.set("this", exp.to_identifier(csv_names[table.name]))
    return tree.sql(dialect=dialect)

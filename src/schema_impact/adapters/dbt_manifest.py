"""Load a dbt project into a `Project` from the artifacts dbt writes to `target/`.

dbt models are Jinja templates (`{{ ref('x') }}`, loops, macros), not plain SQL, so they cannot be
parsed directly. dbt has already done the hard part: after `dbt compile` (or `dbt run`) every
model's final SQL is stored in `target/manifest.json` as `compiled_code`. The columns of the
source tables (seeds and dbt sources) are in `target/catalog.json`, written by
`dbt docs generate`.

    dbt seed && dbt run && dbt docs generate
    project = load_dbt_project("path/to/dbt_project")

CSV sources: with dbt-duckdb a source can be a file (`external_location`). dbt compiles it to
`select * from 'data/orders.csv'` (or to a `read_csv(...)` call when the location holds options):
a table named by its path, whose columns `dbt docs generate` does not list. Such a file is named
`<source>__<table>` (e.g. `olist__orders`) in the models, and its columns and types are read from
the file with DuckDB. That needs the `duckdb` package and the CSV files in place.

Limits: ephemeral models are inlined by dbt into their readers and are loaded like any other model.
Table and column names are used as dbt writes them; no case folding is done.
"""

import json
import re
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
    csv_sources = _csv_sources(manifest, dialect)
    schema = _schema(catalog)
    for name, table in csv_sources.items():
        schema[name] = _csv_columns(name, table, project_dir, dialect)
    names = {_table_key(table, dialect): name for name, table in csv_sources.items()}
    return Project(
        models=_models(manifest, dialect, names),
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


_CSV_CALL = re.compile(r"(?i)read_csv(_auto)?\(")


def _csv_sources(manifest: dict, dialect: str) -> dict[str, exp.Table]:
    """Sources that are files: {name used in the models: the table expression dbt wrote for it}.

    dbt gives such a source its file as the relation name: a path in single quotes
    (`'data/orders.csv'`) or a call (`read_csv('data/orders.csv', types={...})`). A source that is
    a table has a dotted, double-quoted name instead.
    """
    files: dict[str, exp.Table] = {}
    for source in manifest.get("sources", {}).values():
        relation = source.get("relation_name") or ""
        quoted_path = len(relation) > 2 and relation[0] == relation[-1] == "'"
        if quoted_path or _CSV_CALL.match(relation):
            parsed = sqlglot.parse_one(f"SELECT * FROM {relation}", read=dialect)
            files[f"{source['source_name']}__{source['name']}"] = parsed.find(exp.Table)
    return dict(sorted(files.items()))


def _table_key(table: exp.Table, dialect: str) -> str:
    """The text that identifies a table expression, whatever its alias or schema."""
    bare = table.copy()
    for part in ("alias", "db", "catalog"):
        bare.set(part, None)
    return bare.sql(dialect=dialect)


def _csv_columns(name: str, table: exp.Table, project_dir: Path, dialect: str) -> dict[str, str]:
    """Column names and types of a CSV source, in file order, as DuckDB reads them.

    Options given in a `read_csv(...)` call count: `types={'price': 'VARCHAR'}` makes it VARCHAR.
    """
    literal = table.find(exp.Literal) if isinstance(table.this, exp.ReadCSV) else None
    relative = literal.this if literal is not None else table.name
    path = project_dir / relative
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

    if literal is None:
        query, parameters = "DESCRIBE SELECT * FROM read_csv(?)", [str(path)]
    else:
        located = table.copy()
        located.find(exp.Literal).set("this", str(path))  # the file, wherever we run from
        query, parameters = f"DESCRIBE SELECT * FROM {located.sql(dialect=dialect)}", []
    connection = duckdb.connect()
    try:
        rows = connection.execute(query, parameters).fetchall()
    finally:
        connection.close()
    return {column: column_type for column, column_type, *_ in rows}


def _without_database_and_schema(sql: str, dialect: str, csv_names: dict[str, str]) -> str:
    """dbt writes "jaffle_shop"."main"."stg_orders"; the rest of the tool knows it as stg_orders.

    A table that is a CSV file (a path or a `read_csv(...)` call) gets the name of its source.
    The SQL comes back from sqlglot, so keywords are normalised to upper case.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    for table in tree.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
        name = csv_names.get(_table_key(table, dialect)) if csv_names else None
        if name is not None:
            table.set("this", exp.to_identifier(name))
    return tree.sql(dialect=dialect)

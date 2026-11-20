"""Load a dbt project into a `Project` from the artifacts dbt writes to `target/`.

dbt models are Jinja templates (`{{ ref('x') }}`, loops, macros), not plain SQL, so they cannot be
parsed directly. dbt has already done the hard part: after `dbt compile` (or `dbt run`) every
model's final SQL is stored in `target/manifest.json` as `compiled_code`. The columns of the
source tables (seeds and dbt sources) are in `target/catalog.json`, written by
`dbt docs generate`.

    dbt seed && dbt run && dbt docs generate
    project = load_dbt_project("path/to/dbt_project")

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
    target = Path(project_dir) / "target"
    manifest = _read_json(target / "manifest.json", "Run `dbt compile` (or `dbt run`) first.")
    catalog = _read_json(target / "catalog.json", "Run `dbt docs generate` first.")
    return Project(
        models=_models(manifest, dialect),
        schema=_schema(catalog),
        dialect=dialect,
    )


def _read_json(path: Path, hint: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. {hint}")
    return json.loads(path.read_text())


def _models(manifest: dict, dialect: str) -> dict[str, Model]:
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
            sql=_without_database_and_schema(sql, dialect),
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


def _without_database_and_schema(sql: str, dialect: str) -> str:
    """dbt writes "jaffle_shop"."main"."stg_orders"; the rest of the tool knows it as stg_orders.

    The SQL comes back from sqlglot, so keywords are normalised to upper case.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    for table in tree.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
    return tree.sql(dialect=dialect)

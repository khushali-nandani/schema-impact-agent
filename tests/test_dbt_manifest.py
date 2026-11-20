import json
import shutil
from pathlib import Path

import pytest
from schema_impact.adapters.dbt_manifest import load_dbt_project

from schema_impact.core.lineage import ColumnRef, build_lineage

FIXTURE = Path(__file__).parent / "fixtures" / "jaffle_shop_dbt"


@pytest.fixture
def project():
    return load_dbt_project(FIXTURE)


def write_artifacts(root: Path, manifest_nodes: dict, catalog_nodes: dict | None = None) -> Path:
    target = root / "target"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps({"nodes": manifest_nodes}))
    (target / "catalog.json").write_text(json.dumps({"nodes": catalog_nodes or {}}))
    return root


def model_node(name: str, sql: str | None = "select 1 as x", alias: str | None = None) -> dict:
    node = {
        "unique_id": f"model.p.{name}",
        "resource_type": "model",
        "name": name,
        "alias": alias or name,
        "original_file_path": f"models/{name}.sql",
        "compiled_code": sql,
    }
    return node


# --- models -----------------------------------------------------------------


def test_every_model_is_loaded(project):
    assert set(project.models) == {
        "customers",
        "orders",
        "stg_customers",
        "stg_orders",
        "stg_payments",
    }


def test_the_compiled_sql_is_used_not_the_jinja_source(project):
    sql = project.models["orders"].sql
    assert "{{" not in sql and "{%" not in sql
    # the {% for %} over payment methods has been expanded by dbt
    assert "sum(case when payment_method = 'coupon' then amount else 0 end)" in sql


def test_database_and_schema_prefixes_are_removed_from_tables(project):
    for model in project.models.values():
        assert '"jaffle_shop"' not in model.sql, model.name
    assert "from stg_orders" in project.models["orders"].sql.lower().replace('"', "")


def test_a_model_remembers_its_source_file(project):
    assert project.models["stg_orders"].path == Path("models/staging/stg_orders.sql")


def test_the_dialect_is_duckdb(project):
    assert project.dialect == "duckdb"


def test_an_alias_is_the_name_other_models_read_it_by(tmp_path):
    root = write_artifacts(tmp_path, {"model.p.orders": model_node("orders", alias="orders_v2")})
    assert set(load_dbt_project(root).models) == {"orders_v2"}


# --- schema -----------------------------------------------------------------


def test_seeds_are_the_source_tables(project):
    assert set(project.schema) == {"raw_customers", "raw_orders", "raw_payments"}


def test_seed_columns_keep_their_order_and_types(project):
    assert project.schema["raw_orders"] == {
        "id": "INTEGER",
        "user_id": "INTEGER",
        "order_date": "DATE",
        "status": "VARCHAR",
    }


def test_models_are_not_part_of_the_schema(project):
    assert "stg_orders" not in project.schema
    assert "customers" not in project.schema


def test_dbt_sources_are_part_of_the_schema(tmp_path):
    catalog = {
        "source.p.crm.accounts": {
            "metadata": {"name": "accounts", "type": "BASE TABLE"},
            "columns": {"id": {"name": "id", "type": "INTEGER", "index": 1}},
        }
    }
    root = write_artifacts(tmp_path, {"model.p.m": model_node("m")}, catalog)
    assert load_dbt_project(root).schema == {"accounts": {"id": "INTEGER"}}


# --- it feeds the rest of the tool -------------------------------------------


def test_the_loaded_project_goes_through_lineage(project):
    edges = {(e.source, e.target) for e in build_lineage(project) if e.kind == "select"}
    # a seed column renamed in staging...
    assert (ColumnRef("raw_orders", "user_id"), ColumnRef("stg_orders", "customer_id")) in edges
    # ...and a column that travels through two models into a mart
    assert (
        ColumnRef("stg_payments", "amount"),
        ColumnRef("customers", "customer_lifetime_value"),
    ) in edges


# --- missing or incomplete artifacts ----------------------------------------


def test_a_project_that_was_never_compiled_gives_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="dbt"):
        load_dbt_project(tmp_path)


def test_a_missing_catalog_gives_a_clear_error(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "p")
    (tmp_path / "p" / "target" / "catalog.json").unlink()
    with pytest.raises(FileNotFoundError, match="docs generate"):
        load_dbt_project(tmp_path / "p")


def test_a_model_without_compiled_code_gives_a_clear_error(tmp_path):
    root = write_artifacts(tmp_path, {"model.p.m": model_node("m", sql=None)})
    with pytest.raises(ValueError, match="dbt compile"):
        load_dbt_project(root)

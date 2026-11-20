"""dbt sources that are read straight from CSV files (dbt-duckdb `external_location`).

dbt compiles such a source to `select * from 'data/olist_orders_dataset.csv'`: the table is a file
path, and `dbt docs generate` knows nothing about its columns. The adapter has to give the file a
table name and read the columns from the file itself.

The fixture is a trimmed manifest of a real project. The CSV files here are written by the tests
with made-up values; no real data is stored in the repo.
"""

import json
import shutil
from pathlib import Path

import pytest

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.lineage import SELECT, ColumnRef, Edge, build_lineage
from test_dbt_manifest import model_node, write_artifacts

pytest.importorskip("duckdb")  # reads the CSV headers; part of the `bench` dependency group

FIXTURE = Path(__file__).parent / "fixtures" / "olist_dbt"
REPO = Path(__file__).resolve().parent.parent

ORDERS_CSV = (
    "order_id,customer_id,order_status,order_purchase_timestamp,order_approved_at,"
    "order_delivered_carrier_date,order_delivered_customer_date,order_estimated_delivery_date\n"
    "o1,c1,delivered,2020-01-01 10:00:00,2020-01-01 11:00:00,"
    "2020-01-02 10:00:00,2020-01-05 10:00:00,2020-01-07 00:00:00\n"
)
ORDER_ITEMS_CSV = (
    "order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value\n"
    "o1,1,p1,s1,2020-01-03 10:00:00,12.5,2.0\n"
)


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "olist"
    shutil.copytree(FIXTURE, root)
    data = root / "data"
    data.mkdir()
    (data / "olist_orders_dataset.csv").write_text(ORDERS_CSV)
    (data / "olist_order_items_dataset.csv").write_text(ORDER_ITEMS_CSV)
    return root


@pytest.fixture
def project(project_dir):
    return load_dbt_project(project_dir)


# --- the file path becomes a table name ---------------------------------------


def test_a_csv_path_in_a_model_is_replaced_by_the_name_of_its_source(project):
    sql = project.models["stg_olist__orders"].sql
    assert ".csv" not in sql
    assert "olist__orders" in sql


def test_no_model_reads_a_file_path(project):
    for model in project.models.values():
        assert "data/" not in model.sql, model.name


def test_models_that_read_other_models_are_left_alone(project):
    sql = project.models["int_order_items_aggregated"].sql
    assert sql.count("stg_olist__order_items") == 1
    assert sql.count("olist__order_items") == 1  # only inside the model's own name


# --- the columns come from the file -----------------------------------------------


def test_each_csv_source_is_in_the_schema_under_its_source_name(project):
    assert set(project.schema) == {"olist__orders", "olist__order_items"}


def test_columns_are_in_the_order_of_the_file_header(project):
    assert list(project.schema["olist__order_items"]) == [
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    ]


def test_column_types_are_the_ones_duckdb_reads_from_the_file(project):
    types = project.schema["olist__order_items"]
    assert types["order_id"] == "VARCHAR"
    assert types["order_item_id"] == "BIGINT"
    assert types["price"] == "DOUBLE"
    assert types["shipping_limit_date"] == "TIMESTAMP"


def test_a_missing_csv_is_reported_with_its_path(project_dir):
    (project_dir / "data" / "olist_orders_dataset.csv").unlink()
    with pytest.raises(FileNotFoundError, match="olist_orders_dataset.csv"):
        load_dbt_project(project_dir)


def test_a_source_that_is_not_a_file_needs_no_csv(tmp_path):
    root = write_artifacts(tmp_path, {"model.p.m": model_node("m")})
    table = {
        "resource_type": "source",
        "name": "t",
        "source_name": "raw",
        "relation_name": '"db"."main"."t"',  # a table in the database, not a file
    }
    manifest = root / "target" / "manifest.json"
    manifest.write_text(json.dumps({"nodes": {}, "sources": {"source.p.raw.t": table}}))
    assert load_dbt_project(root).schema == {}


# --- the lineage works on top of it -----------------------------------------------


def test_lineage_starts_at_the_source_columns(project):
    edges = set(build_lineage(project))
    assert (
        Edge(
            ColumnRef("olist__orders", "order_status"),
            ColumnRef("stg_olist__orders", "order_status"),
            SELECT,
        )
        in edges
    )


def test_a_renamed_source_column_is_followed(project):
    edges = set(build_lineage(project))
    assert (
        Edge(
            ColumnRef("olist__orders", "order_approved_at"),
            ColumnRef("stg_olist__orders", "order_approved_timestamp"),
            SELECT,
        )
        in edges
    )


def test_lineage_runs_through_a_model_that_reads_a_staging_model(project):
    edges = set(build_lineage(project))
    assert (
        Edge(
            ColumnRef("stg_olist__order_items", "price"),
            ColumnRef("int_order_items_aggregated", "total_item_price"),
            SELECT,
        )
        in edges
    )


# --- the real project (needs the Olist CSVs and a dbt build) ---------------------------


@pytest.mark.bench
def test_the_real_olist_project_loads_and_every_model_resolves():
    root = REPO / "benchmarks" / "olist"
    has_csvs = any((root / "data").glob("*.csv"))
    if not (root / "target" / "manifest.json").is_file() or not has_csvs:
        pytest.skip("build benchmarks/olist first (see its DATA.md)")
    project = load_dbt_project(root)
    assert len(project.models) == 22
    assert len(project.schema) == 9
    assert not [m.name for m in project.models.values() if "data/" in m.sql]
    sources = {e.target.table for e in build_lineage(project) if e.kind == SELECT}
    assert sources == set(project.models)


# --- a source read with read_csv(...) options ----------------------------------------------------
#
# With an `external_location` that holds a "(", dbt-duckdb writes it as it is, so the source can
# be `read_csv('data/x.csv', types={'price': 'VARCHAR'})`. The benchmark uses this to change a
# column's type.

PLAIN = "'data/olist_order_items_dataset.csv'"
WITH_TYPES = "read_csv('data/olist_order_items_dataset.csv', types={'price': 'VARCHAR'})"


@pytest.fixture
def typed_project_dir(project_dir):
    """The same project, with order_items read through read_csv(..., types=...)."""
    manifest = project_dir / "target" / "manifest.json"
    text = manifest.read_text()
    assert PLAIN in text
    manifest.write_text(text.replace(PLAIN, WITH_TYPES))
    return project_dir


def test_a_read_csv_call_in_a_model_is_replaced_by_the_name_of_its_source(typed_project_dir):
    sql = load_dbt_project(typed_project_dir).models["stg_olist__order_items"].sql
    assert "READ_CSV" not in sql.upper()
    assert "olist__order_items" in sql


def test_the_types_given_to_read_csv_are_in_the_schema(typed_project_dir):
    types = load_dbt_project(typed_project_dir).schema["olist__order_items"]
    assert types["price"] == "VARCHAR"  # overridden
    assert types["freight_value"] == "DOUBLE"  # still sniffed
    assert list(types)[:2] == ["order_id", "order_item_id"]


def test_a_missing_csv_is_reported_for_a_read_csv_source_too(typed_project_dir):
    (typed_project_dir / "data" / "olist_order_items_dataset.csv").unlink()
    with pytest.raises(FileNotFoundError, match="olist_order_items_dataset.csv"):
        load_dbt_project(typed_project_dir)


def test_lineage_runs_on_a_source_read_with_read_csv(typed_project_dir):
    edges = set(build_lineage(load_dbt_project(typed_project_dir)))
    assert (
        Edge(
            ColumnRef("olist__order_items", "price"),
            ColumnRef("stg_olist__order_items", "price"),
            SELECT,
        )
        in edges
    )


def test_a_file_that_has_an_alias_in_a_model_is_replaced_too(tmp_path):
    root = tmp_path / "p"
    (root / "data").mkdir(parents=True)
    (root / "data" / "t.csv").write_text("a,b\n1,2\n")
    sql = "select o.a from 'data/t.csv' as o"
    write_artifacts(root, {"model.p.m": model_node("m", sql)})
    manifest = root / "target" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "nodes": json.loads(manifest.read_text())["nodes"],
                "sources": {
                    "source.p.raw.t": {
                        "resource_type": "source",
                        "name": "t",
                        "source_name": "raw",
                        "relation_name": "'data/t.csv'",
                    }
                },
            }
        )
    )
    project = load_dbt_project(root)
    assert "raw__t" in project.models["m"].sql and "t.csv" not in project.models["m"].sql
    assert list(project.schema["raw__t"]) == ["a", "b"]

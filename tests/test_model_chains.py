from pathlib import Path

import pytest

from schema_impact.core.lineage import CircularDependencyError, ColumnRef, Edge, build_lineage
from schema_impact.core.registry import Model, Project, load_project


@pytest.fixture
def edges(toy_project_path):
    return set(build_lineage(load_project(toy_project_path)))


def edge(source: tuple[str, str], target: tuple[str, str | None], kind: str) -> Edge:
    return Edge(ColumnRef(*source), ColumnRef(*target), kind)


def test_top_customers_reads_from_a_model(edges):
    found = {e for e in edges if e.target.table == "top_customers"}
    assert found == {
        edge(("customer_totals", "name"), ("top_customers", "name"), "select"),
        edge(("customer_totals", "total"), ("top_customers", "total"), "select"),
        edge(("customer_totals", "total"), ("top_customers", None), "where"),
        edge(("customer_totals", "total"), ("top_customers", None), "order_by"),
    }


def test_chain_is_connected_end_to_end(edges):
    # orders.amount feeds customer_totals.total, which feeds top_customers.total
    assert edge(("orders", "amount"), ("customer_totals", "total"), "select") in edges
    assert edge(("customer_totals", "total"), ("top_customers", "total"), "select") in edges


def test_model_columns_never_appear_as_source_tables(edges):
    # every source must be a real table or another model, never an alias
    known = {"customers", "products", "orders", "order_items", "customer_totals"}
    sources = {e.source.table for e in edges if e.target.table == "top_customers"}
    assert sources <= known


def project_with(toy_project_path, **models: str) -> Project:
    schema = load_project(toy_project_path).schema
    return Project(
        models={n: Model(n, sql, Path(f"{n}.sql")) for n, sql in models.items()},
        schema=schema,
        dialect="duckdb",
    )


def test_a_model_can_be_defined_before_the_model_it_reads(toy_project_path):
    # "a_report" sorts before "z_base" but depends on it
    project = project_with(
        toy_project_path,
        a_report="SELECT total FROM z_base",
        z_base="SELECT SUM(amount) AS total FROM orders",
    )
    found = {(e.source, e.target) for e in build_lineage(project) if e.kind == "select"}
    assert (ColumnRef("z_base", "total"), ColumnRef("a_report", "total")) in found
    assert (ColumnRef("orders", "amount"), ColumnRef("z_base", "total")) in found


def test_a_longer_chain_is_followed(toy_project_path):
    project = project_with(
        toy_project_path,
        c="SELECT total FROM b",
        b="SELECT total FROM a",
        a="SELECT SUM(amount) AS total FROM orders",
    )
    found = {(e.source, e.target) for e in build_lineage(project) if e.kind == "select"}
    assert (ColumnRef("a", "total"), ColumnRef("b", "total")) in found
    assert (ColumnRef("b", "total"), ColumnRef("c", "total")) in found


def test_circular_models_raise_a_clear_error(toy_project_path):
    project = project_with(
        toy_project_path,
        a="SELECT x FROM b",
        b="SELECT x FROM a",
    )
    with pytest.raises(CircularDependencyError):
        build_lineage(project)

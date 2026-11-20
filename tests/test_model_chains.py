import pytest

from schema_impact.core.lineage import ColumnRef, Edge, build_lineage
from schema_impact.core.registry import load_project


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

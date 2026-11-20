import pytest

from schema_impact.core.lineage import ColumnRef, Edge, build_lineage
from schema_impact.core.registry import load_project


def clause_edge(source: str, model: str, kind: str) -> Edge:
    """Build a model-level edge from 'table.column'. column=None means the whole model."""
    table, column = source.split(".")
    return Edge(source=ColumnRef(table, column), target=ColumnRef(model, None), kind=kind)


@pytest.fixture
def edges(toy_project_path):
    return build_lineage(load_project(toy_project_path))


@pytest.fixture
def clause_edges(edges):
    return {e for e in edges if e.kind != "select"}


def test_customer_totals_clause_edges(clause_edges):
    found = {e for e in clause_edges if e.target.table == "customer_totals"}
    assert found == {
        clause_edge("orders.customer_id", "customer_totals", "join"),
        clause_edge("customers.id", "customer_totals", "join"),
        clause_edge("customers.name", "customer_totals", "group_by"),
    }


def test_product_sales_clause_edges(clause_edges):
    found = {e for e in clause_edges if e.target.table == "product_sales"}
    assert found == {
        clause_edge("order_items.order_id", "product_sales", "join"),
        clause_edge("orders.id", "product_sales", "join"),
        clause_edge("order_items.product_id", "product_sales", "join"),
        clause_edge("products.id", "product_sales", "join"),
        clause_edge("products.name", "product_sales", "group_by"),
        clause_edge("orders.status", "product_sales", "where"),
    }


def test_clause_edges_point_to_the_whole_model(clause_edges):
    assert clause_edges
    assert all(e.target.column is None for e in clause_edges)


def test_aliases_are_resolved_in_clauses(clause_edges):
    source_tables = {e.source.table for e in clause_edges}
    assert source_tables <= {"customers", "products", "orders", "order_items"}


def test_one_column_can_have_several_roles(edges):
    kinds = {e.kind for e in edges if e.source == ColumnRef("customers", "name")}
    assert kinds == {"select", "group_by"}


def test_model_that_does_not_use_a_column_has_no_edge_to_it(edges):
    # orders.amount is used by customer_totals only, never by product_sales
    amount = ColumnRef("orders", "amount")
    assert not any(e.source == amount and e.target.table == "product_sales" for e in edges)
    assert any(e.source == amount and e.target.table == "customer_totals" for e in edges)

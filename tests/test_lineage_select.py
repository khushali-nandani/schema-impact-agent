import pytest

from schema_impact.core.lineage import ColumnRef, Edge, build_lineage
from schema_impact.core.registry import load_project


def edge(source: str, target: str) -> Edge:
    """Build a select edge from 'table.column' strings."""
    source_table, source_column = source.split(".")
    target_table, target_column = target.split(".")
    return Edge(
        source=ColumnRef(source_table, source_column),
        target=ColumnRef(target_table, target_column),
        kind="select",
    )


@pytest.fixture
def select_edges(toy_project_path):
    project = load_project(toy_project_path)
    return {e for e in build_lineage(project) if e.kind == "select"}


def test_customer_totals_select_edges(select_edges):
    found = {e for e in select_edges if e.target.table == "customer_totals"}
    assert found == {
        edge("customers.name", "customer_totals.name"),
        edge("orders.amount", "customer_totals.total"),
    }


def test_product_sales_select_edges(select_edges):
    found = {e for e in select_edges if e.target.table == "product_sales"}
    assert found == {
        edge("products.name", "product_sales.product_name"),
        edge("order_items.quantity", "product_sales.units_sold"),
        edge("order_items.quantity", "product_sales.revenue"),
        edge("products.price", "product_sales.revenue"),
    }


def test_aliases_are_resolved_to_real_tables(select_edges):
    source_tables = {e.source.table for e in select_edges}
    assert source_tables <= {"customers", "products", "orders", "order_items"}


def test_expression_column_has_several_sources(select_edges):
    revenue_sources = {
        e.source for e in select_edges if e.target == ColumnRef("product_sales", "revenue")
    }
    assert revenue_sources == {
        ColumnRef("order_items", "quantity"),
        ColumnRef("products", "price"),
    }


def test_unused_columns_never_appear(select_edges):
    used = {e.source for e in select_edges}
    assert ColumnRef("customers", "country") not in used

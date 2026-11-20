"""Columns used in JOIN, WHERE, GROUP BY, HAVING or ORDER BY that come from a CTE or a subquery.

dbt models are written as chains of CTEs, so most clause columns belong to a CTE and not to a real
table. They must be traced back to the table columns that define them.
"""

from pathlib import Path

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.lineage import ColumnRef, build_lineage
from schema_impact.core.registry import Model, Project, load_project


def clause_edges(project, kind: str) -> set[ColumnRef]:
    return {e.source for e in build_lineage(project) if e.kind == kind}


def col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table, column)


def test_a_group_by_on_a_cte_column_is_traced_to_the_table(make_project):
    project = make_project(
        "WITH o AS (SELECT customer_id, amount FROM orders) "
        "SELECT customer_id, SUM(amount) AS t FROM o GROUP BY customer_id"
    )
    assert clause_edges(project, "group_by") == {col("orders", "customer_id")}


def test_a_join_between_two_ctes_is_traced_to_both_tables(make_project):
    project = make_project(
        "WITH o AS (SELECT id, customer_id FROM orders), c AS (SELECT id FROM customers) "
        "SELECT c.id FROM c JOIN o ON o.customer_id = c.id"
    )
    assert clause_edges(project, "join") == {col("orders", "customer_id"), col("customers", "id")}


def test_a_where_on_a_cte_column_is_traced_to_the_table(make_project):
    project = make_project(
        "WITH o AS (SELECT amount, status FROM orders) SELECT amount FROM o WHERE status != 'x'"
    )
    assert clause_edges(project, "where") == {col("orders", "status")}


def test_a_column_is_traced_through_several_ctes(make_project):
    project = make_project(
        "WITH a AS (SELECT status FROM orders), b AS (SELECT status FROM a) "
        "SELECT 1 AS one FROM b WHERE status = 'x'"
    )
    assert clause_edges(project, "where") == {col("orders", "status")}


def test_a_computed_cte_column_is_traced_to_what_it_is_computed_from(make_project):
    project = make_project(
        "WITH o AS (SELECT amount * 2 AS twice FROM orders) SELECT 1 AS one FROM o WHERE twice > 10"
    )
    assert clause_edges(project, "where") == {col("orders", "amount")}


def test_a_column_of_a_subquery_in_from_is_traced(make_project):
    project = make_project(
        "SELECT x.status FROM (SELECT status, amount FROM orders) AS x WHERE x.amount > 1"
    )
    assert clause_edges(project, "where") == {col("orders", "amount")}


def test_an_order_by_on_an_alias_of_a_cte_column_is_traced(make_project):
    project = make_project(
        "WITH o AS (SELECT amount FROM orders) SELECT amount AS a FROM o ORDER BY a"
    )
    assert clause_edges(project, "order_by") == {col("orders", "amount")}


def test_only_the_columns_a_clause_uses_are_traced(make_project):
    project = make_project(
        "WITH o AS (SELECT amount, status FROM orders) SELECT amount FROM o WHERE amount > 1"
    )
    assert clause_edges(project, "where") == {col("orders", "amount")}


def test_a_cte_column_read_from_another_model_is_traced_to_that_model(toy_project_path):
    toy = load_project(toy_project_path)
    project = Project(
        models={
            "base": Model("base", "SELECT customer_id, amount FROM orders", Path("base.sql")),
            "report": Model(
                "report",
                "WITH b AS (SELECT customer_id FROM base) "
                "SELECT 1 AS one FROM b GROUP BY customer_id",
                Path("report.sql"),
            ),
        },
        schema=toy.schema,
        dialect="duckdb",
    )
    assert clause_edges(project, "group_by") == {col("base", "customer_id")}


# --- on real dbt models ---------------------------------------------------------


def test_the_group_by_and_joins_of_the_jaffle_marts_are_traced_through_their_ctes():
    jaffle = load_dbt_project(Path(__file__).parent / "fixtures" / "jaffle_shop_dbt")
    edges = {(e.source, e.target.table, e.kind) for e in build_lineage(jaffle)}
    # customers: GROUP BY customer_id inside the customer_orders CTE, over stg_orders
    assert (col("stg_orders", "customer_id"), "customers", "group_by") in edges
    # customers: payments LEFT JOIN orders ON payments.order_id = orders.order_id
    assert (col("stg_payments", "order_id"), "customers", "join") in edges
    assert (col("stg_orders", "order_id"), "customers", "join") in edges
    # orders: GROUP BY order_id over payments, and the join to order_payments
    assert (col("stg_payments", "order_id"), "orders", "group_by") in edges
    assert (col("stg_payments", "order_id"), "orders", "join") in edges

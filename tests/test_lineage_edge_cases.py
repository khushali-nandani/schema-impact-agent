from schema_impact.core.lineage import ColumnRef, build_lineage


def edges_of(project, kind=None):
    return {
        (e.source, e.target.column, e.kind)
        for e in build_lineage(project)
        if kind is None or e.kind == kind
    }


def col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table, column)


# --- SELECT * ---------------------------------------------------------------


def test_select_star_expands_to_every_column(make_project):
    project = make_project("SELECT * FROM customers")
    assert edges_of(project, "select") == {
        (col("customers", "id"), "id", "select"),
        (col("customers", "name"), "name", "select"),
        (col("customers", "country"), "country", "select"),
    }


def test_select_star_with_a_join_keeps_each_table(make_project):
    project = make_project(
        "SELECT o.* FROM orders AS o JOIN customers AS c ON o.customer_id = c.id"
    )
    tables = {e.source.table for e in build_lineage(project) if e.kind == "select"}
    assert tables == {"orders"}


# --- clauses inside CTEs and subqueries -------------------------------------


def test_where_inside_a_cte_is_found(make_project):
    project = make_project(
        "WITH r AS (SELECT id FROM orders WHERE status != 'returned') SELECT id FROM r"
    )
    assert (col("orders", "status"), None, "where") in edges_of(project, "where")


def test_where_inside_a_subquery_is_found(make_project):
    project = make_project("SELECT x.id FROM (SELECT id FROM orders WHERE amount > 10) AS x")
    assert edges_of(project, "where") == {(col("orders", "amount"), None, "where")}


def test_join_inside_a_cte_is_found(make_project):
    project = make_project(
        "WITH j AS (SELECT o.id FROM orders AS o JOIN customers AS c ON o.customer_id = c.id) "
        "SELECT id FROM j"
    )
    assert edges_of(project, "join") == {
        (col("orders", "customer_id"), None, "join"),
        (col("customers", "id"), None, "join"),
    }


def test_group_by_inside_a_cte_is_found(make_project):
    project = make_project(
        "WITH g AS (SELECT customer_id, SUM(amount) AS t FROM orders GROUP BY customer_id) "
        "SELECT customer_id FROM g"
    )
    assert edges_of(project, "group_by") == {(col("orders", "customer_id"), None, "group_by")}


def test_conditions_in_different_scopes_do_not_mix_aliases(make_project):
    # alias "o" means orders in the CTE and customers in the outer query
    project = make_project(
        "WITH a AS (SELECT o.id FROM orders AS o WHERE o.status = 'x') "
        "SELECT o.id FROM customers AS o JOIN a ON o.id = a.id WHERE o.country = 'IN'"
    )
    wheres = edges_of(project, "where")
    assert (col("orders", "status"), None, "where") in wheres
    assert (col("customers", "country"), None, "where") in wheres


# --- HAVING and ORDER BY ----------------------------------------------------


def test_having_creates_an_edge(make_project):
    project = make_project(
        "SELECT customer_id FROM orders GROUP BY customer_id HAVING SUM(amount) > 100"
    )
    assert edges_of(project, "having") == {(col("orders", "amount"), None, "having")}


def test_order_by_creates_an_edge(make_project):
    project = make_project("SELECT id FROM orders ORDER BY amount")
    assert edges_of(project, "order_by") == {(col("orders", "amount"), None, "order_by")}


def test_order_by_inside_a_window_is_not_a_clause_edge(make_project):
    project = make_project(
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount) AS rn FROM orders"
    )
    assert edges_of(project, "order_by") == set()
    # window columns still feed the output column
    sources = {s for s, target, kind in edges_of(project, "select") if target == "rn"}
    assert sources == {col("orders", "customer_id"), col("orders", "amount")}


# --- other shapes that must keep working ------------------------------------


def test_join_with_several_conditions(make_project):
    project = make_project(
        "SELECT o.id FROM orders AS o LEFT JOIN customers AS c "
        "ON o.customer_id = c.id AND c.country = 'IN'"
    )
    assert edges_of(project, "join") == {
        (col("orders", "customer_id"), None, "join"),
        (col("customers", "id"), None, "join"),
        (col("customers", "country"), None, "join"),
    }


def test_case_expression_uses_all_its_columns(make_project):
    project = make_project(
        "SELECT CASE WHEN status = 'returned' THEN 0 ELSE amount END AS a FROM orders"
    )
    assert edges_of(project, "select") == {
        (col("orders", "status"), "a", "select"),
        (col("orders", "amount"), "a", "select"),
    }


def test_literal_only_select_has_no_edges(make_project):
    assert build_lineage(make_project("SELECT 1 AS one FROM orders")) == []

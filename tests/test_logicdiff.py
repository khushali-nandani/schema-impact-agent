"""Which output columns of a model changed when its SQL was edited.

`changed_outputs(before, after, ...)` compares two versions of one model and answers with

- a set of output columns: only these were computed differently (an empty set: the edit changed
  nothing that matters, e.g. a renamed CTE or different spacing)
- None: it cannot narrow the edit down, so the whole model must be treated as changed

How it decides. Each CTE is compared on its own, projection by projection, and on its "frame",
everything but the select list (FROM, JOIN, WHERE, GROUP BY, HAVING, QUALIFY, DISTINCT, ORDER BY,
LIMIT). CTE names are made positional first, so renaming one is no change. A changed frame changes
which rows the CTE has, so every column of it. A changed column that a later CTE reads in its
frame (a join key, a filter, a grouping) changes that CTE's rows too; one it only selects or
aggregates changes only the columns computed from it. Anything it does not understand is None: a
wrong narrowing would hide a real change, and None is what the tool did before.
"""

from pathlib import Path

import pytest

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.lineage import UNKNOWN_TYPE, model_outputs
from schema_impact.core.logicdiff import changed_outputs

SCHEMA = {
    "orders": {"id": "INTEGER", "customer_id": "INTEGER", "amount": "DOUBLE", "status": "VARCHAR"},
}


def changed(before: str, after: str, schema=SCHEMA):
    return changed_outputs(before, after, schema, schema, "duckdb")


A = "WITH a AS (SELECT id, amount * 2 AS x FROM orders) "
A_EDITED = A.replace("* 2", "* 3")


# --- nothing changed that matters ---------------------------------------------------------------


def test_the_same_query_changed_nothing():
    assert changed("SELECT id FROM orders", "SELECT id FROM orders") == set()


def test_spacing_case_and_comments_are_not_a_change():
    assert changed("select id from orders", "SELECT  id -- the key\nFROM orders") == set()


def test_renaming_a_cte_is_not_a_change():
    before = A + "SELECT id, x FROM a"
    after = A.replace("WITH a AS", "WITH b AS") + "SELECT id, x FROM b"
    assert changed(before, after) == set()


def test_renaming_a_cte_that_is_aliased_to_itself_is_not_a_change():
    before = "WITH a AS (SELECT id FROM orders) SELECT a.id FROM a"
    after = "WITH b AS (SELECT id FROM orders) SELECT b.id FROM b"
    assert changed(before, after) == set()


def test_a_changed_column_that_nothing_selects_changes_no_output():
    before = "WITH a AS (SELECT id, amount * 2 AS dead FROM orders) SELECT id FROM a"
    after = "WITH a AS (SELECT id, amount * 3 AS dead FROM orders) SELECT id FROM a"
    assert changed(before, after) == set()


# --- one or a few columns -----------------------------------------------------------------------


def test_a_changed_projection_is_the_only_changed_column():
    assert changed(
        "SELECT id, amount AS a FROM orders", "SELECT id, amount * 2 AS a FROM orders"
    ) == {"a"}


def test_every_changed_projection_is_reported():
    before = "SELECT amount AS a, status AS s, id FROM orders"
    after = "SELECT amount * 2 AS a, UPPER(status) AS s, id FROM orders"
    assert changed(before, after) == {"a", "s"}


@pytest.mark.parametrize(
    "old, new",
    [
        ("SUM(amount)", "AVG(amount)"),
        ("amount", "amount + 1"),
        ("1", "2"),
        ("CASE WHEN amount > 0 THEN 1 ELSE 0 END", "CASE WHEN amount > 5 THEN 1 ELSE 0 END"),
    ],
)
def test_a_change_inside_one_expression_is_that_column(old, new):
    before = f"SELECT id, {old} AS c FROM orders GROUP BY id, amount"
    after = f"SELECT id, {new} AS c FROM orders GROUP BY id, amount"
    assert changed(before, after) == {"c"}


# --- through CTEs -------------------------------------------------------------------------------


def test_a_changed_cte_column_that_is_only_selected_changes_that_output():
    assert changed(
        A + "SELECT id, x FROM a WHERE id > 1", A_EDITED + "SELECT id, x FROM a WHERE id > 1"
    ) == {"x"}


def test_a_changed_cte_column_inside_an_aggregate_changes_the_aggregate():
    sql = "SELECT id, SUM(x) AS s FROM a GROUP BY id"
    assert changed(A + sql, A_EDITED + sql) == {"s"}


def test_a_change_travels_through_two_ctes():
    tail = "b AS (SELECT id, x + 1 AS y FROM a) SELECT id, y FROM b"
    assert changed(A + ", " + tail, A_EDITED + ", " + tail) == {"y"}


def test_a_changed_cte_column_in_a_window_order_changes_the_windowed_column():
    sql = "SELECT id, ROW_NUMBER() OVER (ORDER BY x) AS rn FROM a"
    assert changed(A + sql, A_EDITED + sql) == {"rn"}


def test_a_dbt_style_select_star_chain():
    before = (
        "WITH s AS (SELECT * FROM orders), r AS (SELECT id, amount AS total FROM s) SELECT * FROM r"
    )
    after = before.replace("amount AS total", "amount * 100 AS total")
    assert changed(before, after) == {"total"}


# --- the rows change, so every column does ------------------------------------------------------


@pytest.mark.parametrize(
    "before, after",
    [
        ("SELECT id FROM orders", "SELECT id FROM orders WHERE amount > 1"),
        ("SELECT id FROM orders WHERE amount > 1", "SELECT id FROM orders WHERE amount > 2"),
        ("SELECT status FROM orders", "SELECT DISTINCT status FROM orders"),
        ("SELECT id FROM orders", "SELECT id FROM orders LIMIT 5"),
        ("SELECT id FROM orders ORDER BY id", "SELECT id FROM orders ORDER BY id DESC"),
        (
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status",
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status HAVING COUNT(*) > 1",
        ),
        (
            "SELECT o.id FROM orders o LEFT JOIN orders p ON o.id = p.customer_id",
            "SELECT o.id FROM orders o INNER JOIN orders p ON o.id = p.customer_id",
        ),
        (
            "SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id",
            "SELECT customer_id, COUNT(*) AS n FROM orders WHERE amount > 0 GROUP BY customer_id",
        ),
    ],
)
def test_a_changed_clause_changes_the_whole_model(before, after):
    assert changed(before, after) is None


def test_a_changed_filter_in_an_earlier_cte_changes_the_whole_model():
    before = "WITH a AS (SELECT id FROM orders) SELECT id FROM a"
    after = "WITH a AS (SELECT id FROM orders WHERE amount > 1) SELECT id FROM a"
    assert changed(before, after) is None


def test_a_changed_cte_column_used_in_a_later_filter_changes_the_whole_model():
    sql = "SELECT id, x FROM a WHERE x > 10"
    assert changed(A + sql, A_EDITED + sql) is None


def test_a_changed_cte_column_used_in_a_later_group_by_changes_the_whole_model():
    sql = "SELECT x, COUNT(*) AS n FROM a GROUP BY x"
    assert changed(A + sql, A_EDITED + sql) is None


def test_a_changed_cte_column_used_in_a_later_join_changes_the_whole_model():
    sql = "SELECT a.id FROM a JOIN orders o ON o.amount = a.x"
    assert changed(A + sql, A_EDITED + sql) is None


def test_a_changed_cte_column_used_in_a_later_having_changes_the_whole_model():
    sql = "SELECT id, COUNT(*) AS n FROM a GROUP BY id HAVING MAX(x) > 5"
    assert changed(A + sql, A_EDITED + sql) is None


# --- an alias read by another column, a QUALIFY, a subquery -------------------------------------


def test_a_column_that_reads_the_alias_of_a_changed_one_changes_too():
    before = "SELECT amount AS a, a + 1 AS b FROM orders"
    after = "SELECT amount * 2 AS a, a + 1 AS b FROM orders"
    assert changed(before, after) == {"a", "b"}


def test_a_changed_column_read_in_a_where_through_its_alias_changes_the_whole_model():
    before = "SELECT amount AS a FROM orders WHERE a > 1"
    after = "SELECT amount * 2 AS a FROM orders WHERE a > 1"
    assert changed(before, after) is None


def test_a_scalar_subquery_over_a_changed_cte_changes_its_column():
    sql = "SELECT id, (SELECT MAX(x) FROM a) AS m FROM orders"
    assert changed(A + sql, A_EDITED + sql) == {"m"}


def test_a_change_inside_a_derived_table_changes_the_whole_model():
    before = "SELECT t.x FROM (SELECT amount * 2 AS x FROM orders) AS t"
    after = "SELECT t.x FROM (SELECT amount * 3 AS x FROM orders) AS t"
    assert changed(before, after) is None


# --- an alias or a position that ORDER BY reads -------------------------------------------------
#
# Qualify writes an alias read by WHERE, GROUP BY, HAVING or QUALIFY as the expression behind it, so
# a change shows in the text of the clause. ORDER BY keeps the alias, so `ORDER BY a` reads as the
# same text whatever `a` is computed as. With a LIMIT it decides which rows are kept; without, the
# order of the rows. Either way the model as a whole changed.


def test_an_order_by_that_reads_the_alias_of_a_changed_column_changes_the_whole_model():
    before = "SELECT id, amount AS a FROM orders ORDER BY a"
    after = "SELECT id, -amount AS a FROM orders ORDER BY a"
    assert changed(before, after) is None


def test_the_same_with_a_limit_which_decides_the_rows_kept():
    before = "SELECT id, amount AS a FROM orders ORDER BY a LIMIT 5"
    after = "SELECT id, amount * 2 AS a FROM orders ORDER BY a LIMIT 5"
    assert changed(before, after) is None


def test_an_order_by_expression_that_contains_the_alias_of_a_changed_column_counts_too():
    before = "SELECT id, amount AS a FROM orders ORDER BY a + 1 DESC"
    after = "SELECT id, amount * 2 AS a FROM orders ORDER BY a + 1 DESC"
    assert changed(before, after) is None


def test_an_order_by_position_that_points_at_a_changed_column_changes_the_whole_model():
    before = "SELECT id, amount AS a FROM orders ORDER BY 2 LIMIT 5"
    after = "SELECT id, amount * 2 AS a FROM orders ORDER BY 2 LIMIT 5"
    assert changed(before, after) is None


def test_an_order_by_that_reads_a_column_that_did_not_change_is_not_a_change():
    before = "SELECT id, amount AS a, status AS s FROM orders ORDER BY s"
    after = "SELECT id, amount * 2 AS a, status AS s FROM orders ORDER BY s"
    assert changed(before, after) == {"a"}


def test_an_order_by_position_of_a_column_that_did_not_change_is_not_a_change():
    before = "SELECT id, amount AS a FROM orders ORDER BY 1"
    after = "SELECT id, amount * 2 AS a FROM orders ORDER BY 1"
    assert changed(before, after) == {"a"}


def test_an_order_by_on_a_table_column_is_not_a_change():
    before = "SELECT id, amount AS a FROM orders ORDER BY id"
    after = "SELECT id, amount * 2 AS a FROM orders ORDER BY id"
    assert changed(before, after) == {"a"}


def test_an_order_by_alias_of_a_column_changed_through_an_earlier_cte_changes_the_whole_model():
    before = A + "SELECT id, x AS y FROM a ORDER BY y"
    after = A_EDITED + "SELECT id, x AS y FROM a ORDER BY y"
    assert changed(before, after) is None


@pytest.mark.parametrize(
    "clause",
    [
        "GROUP BY s",
        "GROUP BY 1",
        "GROUP BY s HAVING n > 1",
    ],
)
def test_the_other_clauses_that_read_an_alias_see_the_change_in_their_text(clause):
    before = f"SELECT status AS s, COUNT(*) AS n FROM orders {clause}"
    after = before.replace("status AS s", "UPPER(status) AS s")
    assert changed(before, after) is None


def test_a_qualify_that_reads_the_alias_of_a_changed_window_changes_the_whole_model():
    before = "SELECT id, ROW_NUMBER() OVER (ORDER BY amount) AS rn FROM orders QUALIFY rn = 1"
    after = "SELECT id, ROW_NUMBER() OVER (ORDER BY amount DESC) AS rn FROM orders QUALIFY rn = 1"
    assert changed(before, after) is None


# --- what it will not narrow down ---------------------------------------------------------------


def test_a_different_set_of_output_columns_is_not_narrowed():
    assert changed("SELECT id, amount FROM orders", "SELECT id FROM orders") is None
    assert changed("SELECT id FROM orders", "SELECT id, amount FROM orders") is None


def test_the_same_columns_in_another_order_are_not_narrowed():
    assert changed("SELECT id, amount FROM orders", "SELECT amount, id FROM orders") is None


def test_a_cte_added_or_removed_is_not_narrowed():
    plain = "SELECT id FROM orders"
    wrapped = "WITH a AS (SELECT id FROM orders) SELECT id FROM a"
    assert changed(plain, wrapped) is None
    assert changed(wrapped, plain) is None


def test_a_union_is_not_narrowed():
    before = "SELECT id FROM orders UNION ALL SELECT id FROM orders"
    after = "SELECT id FROM orders UNION ALL SELECT customer_id FROM orders"
    assert changed(before, after) is None


def test_a_union_inside_a_cte_is_not_narrowed():
    union = "SELECT id FROM orders UNION ALL SELECT {} FROM orders"
    before = f"WITH u AS ({union.format('id')}) SELECT id FROM u"
    after = f"WITH u AS ({union.format('customer_id')}) SELECT id FROM u"
    assert changed(before, after) is None


def test_new_sql_that_does_not_resolve_is_not_narrowed():
    assert changed("SELECT id FROM orders", "SELECT missing FROM orders") is None


def test_sql_that_does_not_parse_is_not_narrowed():
    assert changed("SELECT id FROM orders", "SELECT FROM WHERE") is None


def test_each_version_is_read_against_its_own_schema():
    after_schema = {"orders": {**SCHEMA["orders"], "discount": "DOUBLE"}}
    before, after = "SELECT id, amount AS a FROM orders", "SELECT id, discount AS a FROM orders"
    # `discount` exists only in the schema of the new version
    assert changed_outputs(before, after, SCHEMA, after_schema, "duckdb") == {"a"}
    assert changed(before, after) is None  # with one schema for both it does not resolve


# --- on the models of a real dbt project --------------------------------------------------------


@pytest.fixture(scope="module")
def jaffle():
    project = load_dbt_project(Path(__file__).parent / "fixtures" / "jaffle_shop_dbt")
    schema = {table: dict(columns) for table, columns in project.schema.items()}
    for model, columns in model_outputs(project).items():
        schema[model] = dict.fromkeys(columns, UNKNOWN_TYPE)
    return project, schema


def edited(jaffle, model: str, old: str, new: str):
    project, schema = jaffle
    sql = project.models[model].sql
    assert old in sql, f"{old!r} is not in {model}"
    return changed_outputs(sql, sql.replace(old, new), schema, schema, "duckdb")


def test_jaffle_orders_a_changed_total_is_only_the_amount_column(jaffle):
    # order_payments.total_amount is selected as `amount` and as nothing else
    assert edited(
        jaffle, "orders", "SUM(amount) AS total_amount", "AVG(amount) AS total_amount"
    ) == {"amount"}


def test_jaffle_orders_a_changed_case_branch_is_only_that_payment_method(jaffle):
    result = edited(
        jaffle,
        "orders",
        "THEN amount ELSE 0 END) AS credit_card_amount",
        "THEN amount * 2 ELSE 0 END) AS credit_card_amount",
    )
    assert result == {"credit_card_amount"}


def test_jaffle_orders_a_stricter_join_is_the_whole_model(jaffle):
    assert edited(jaffle, "orders", "LEFT JOIN order_payments", "INNER JOIN order_payments") is None


def test_jaffle_customers_min_to_max_is_only_first_order(jaffle):
    result = edited(
        jaffle, "customers", "MIN(order_date) AS first_order", "MAX(order_date) AS first_order"
    )
    assert result == {"first_order"}


def test_jaffle_customers_a_stricter_join_in_an_inner_cte_is_the_whole_model(jaffle):
    result = edited(
        jaffle, "customers", "LEFT JOIN orders ON payments", "INNER JOIN orders ON payments"
    )
    assert result is None


def test_jaffle_customers_renaming_a_cte_changes_nothing(jaffle):
    assert edited(jaffle, "customers", "customer_orders", "cust_orders") == set()


def test_jaffle_orders_renaming_the_final_cte_changes_nothing(jaffle):
    assert edited(jaffle, "orders", "final", "result") == set()

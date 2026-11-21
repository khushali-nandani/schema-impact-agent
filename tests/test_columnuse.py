"""How a model uses a column on its way to an output column: as it is, strictly, or otherwise.

When a column's type changes to text, what happens to a model that reads it depends on what the
model does with it. A column that is only selected stays text and breaks nothing; `SUM(amount)`
or `amount / 100` on text makes the query fail. So for every (source column, output column) pair
of a model, `column_uses` says:

- PASS    the output is the column itself (an alias, a CASE branch or a COALESCE argument are
          still the column), so the output has the column's type
- STRICT  the column is the direct argument of SUM, AVG, date_diff or plain arithmetic, which a
          text value cannot be given to
- OTHER   anything else (a cast, ROUND, UPPER, COUNT, MIN, MAX, a comparison, a CASE condition):
          the output has some other type, or the use is not one we know to fail

Across the CTEs of one model the use closest to the table decides, unless it is PASS: a cast
before a SUM means the SUM sees the cast type. When one column reaches an output in several
ways, STRICT wins over OTHER and OTHER over PASS, the cautious order for finding failures.
"""

from pathlib import Path

import pytest
from schema_impact.core.columnuse import OTHER, PASS, STRICT, column_uses

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import Model, Project

SCHEMA = {
    "orders": {"id": "INTEGER", "customer_id": "INTEGER", "status": "VARCHAR", "amount": "DOUBLE"},
    "events": {"id": "INTEGER", "started": "TIMESTAMP", "ended": "TIMESTAMP"},
}


def project(**models: str) -> Project:
    return Project(
        models={name: Model(name, sql, Path(f"{name}.sql")) for name, sql in models.items()},
        schema=SCHEMA,
        dialect="duckdb",
    )


def use(sql: str, source: str = "orders.amount", output: str = "x") -> str | None:
    """The use of `source` in the output column `output` of a model `m` made from `sql`."""
    table, column = source.split(".")
    uses = column_uses(project(m=sql))
    return uses.get((ColumnRef(table, column), ColumnRef("m", output)))


# --- PASS: the output is the column itself ------------------------------------------------------


def test_a_column_that_is_only_selected_passes():
    assert use("SELECT amount AS x FROM orders") == PASS


def test_a_column_selected_under_its_own_name_passes():
    assert use("SELECT amount FROM orders", output="amount") == PASS


def test_a_column_passed_through_ctes_unchanged_passes():
    sql = (
        "WITH a AS (SELECT amount FROM orders), b AS (SELECT amount FROM a) "
        "SELECT amount AS x FROM b"
    )
    assert use(sql) == PASS


def test_coalesce_and_case_branches_are_still_the_column():
    assert use("SELECT COALESCE(amount, 0) AS x FROM orders") == PASS
    assert use("SELECT CASE WHEN id > 1 THEN amount ELSE 0 END AS x FROM orders") == PASS


def test_a_star_that_is_expanded_passes():
    sql = "WITH o AS (SELECT * FROM orders) SELECT * FROM o"
    assert use(sql, output="amount") == PASS


# --- STRICT: a text value cannot be given to it -------------------------------------------------


@pytest.mark.parametrize("function", ["SUM", "AVG"])
def test_an_aggregate_that_needs_numbers_is_strict(function):
    assert use(f"SELECT {function}(amount) AS x FROM orders") == STRICT


def test_a_window_aggregate_is_strict():
    assert use("SELECT SUM(amount) OVER (PARTITION BY customer_id) AS x FROM orders") == STRICT


@pytest.mark.parametrize("expression", ["amount * 100", "amount / 100", "amount + 1", "1 - amount"])
def test_plain_arithmetic_is_strict(expression):
    assert use(f"SELECT {expression} AS x FROM orders") == STRICT


def test_date_diff_is_strict():
    sql = "SELECT date_diff('day', started, ended) AS x FROM events"
    assert use(sql, "events.started") == STRICT
    assert use(sql, "events.ended") == STRICT


def test_a_sum_over_a_case_is_still_strict_for_the_value_but_not_for_the_condition():
    sql = "SELECT SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS x FROM orders"
    assert use(sql) == STRICT
    assert use(sql, "orders.status") == OTHER  # a comparison: text is fine there


def test_strict_across_ctes_when_the_column_was_only_passed_on_before():
    sql = "WITH o AS (SELECT amount FROM orders) SELECT SUM(amount) AS x FROM o"
    assert use(sql) == STRICT


# --- OTHER: some other type comes out, or the use is not one we know to fail --------------------


@pytest.mark.parametrize(
    "expression",
    [
        "CAST(amount AS INTEGER)",
        "ROUND(amount, 2)",
        "COUNT(amount)",
        "MAX(amount)",
        "MIN(amount)",
        "amount > 10",
        "-amount",
    ],
)
def test_other_uses_are_neither_pass_nor_strict(expression):
    assert use(f"SELECT {expression} AS x FROM orders") == OTHER


def test_text_functions_are_other():
    assert use("SELECT UPPER(status) AS x FROM orders", "orders.status") == OTHER


def test_a_cast_before_a_sum_means_the_sum_does_not_see_the_column_type():
    sql = (
        "WITH o AS (SELECT CAST(amount AS INTEGER) AS amount FROM orders) "
        "SELECT SUM(amount) AS x FROM o"
    )
    assert use(sql) == OTHER


def test_a_cast_inside_the_sum_is_other():
    assert use("SELECT SUM(CAST(amount AS INTEGER)) AS x FROM orders") == OTHER


# --- several uses of one column in one output ---------------------------------------------------


def test_strict_wins_over_other():
    sql = "SELECT CASE WHEN amount > 0 THEN amount * 2 ELSE ROUND(amount) END AS x FROM orders"
    assert use(sql) == STRICT


def test_other_wins_over_pass():
    assert use("SELECT COALESCE(amount, ROUND(amount)) AS x FROM orders") == OTHER


# --- which pairs have an entry ------------------------------------------------------------------


def test_a_column_used_only_in_a_filter_or_join_feeds_no_output():
    uses = column_uses(project(m="SELECT id AS x FROM orders WHERE amount > 10"))
    assert (ColumnRef("orders", "amount"), ColumnRef("m", "x")) not in uses


def test_every_entry_is_a_pair_of_a_source_column_and_an_output_column_of_a_model():
    uses = column_uses(project(m="SELECT id AS x, SUM(amount) AS y FROM orders GROUP BY id"))
    assert set(uses) == {
        (ColumnRef("orders", "id"), ColumnRef("m", "x")),
        (ColumnRef("orders", "amount"), ColumnRef("m", "y")),
    }


def test_a_literal_has_no_source_column():
    assert column_uses(project(m="SELECT 1 AS x FROM orders")) == {}


def test_a_model_reading_another_model_is_keyed_by_that_model():
    uses = column_uses(
        project(a="SELECT SUM(amount) AS total FROM orders", b="SELECT total / 2 AS x FROM a")
    )
    assert uses[(ColumnRef("orders", "amount"), ColumnRef("a", "total"))] == STRICT
    assert uses[(ColumnRef("a", "total"), ColumnRef("b", "x"))] == STRICT


def test_a_cte_that_is_a_union_is_not_traced():
    sql = (
        "WITH u AS (SELECT amount FROM orders UNION ALL SELECT amount FROM orders) "
        "SELECT SUM(amount) AS x FROM u"
    )
    # the same limit as the clause edges of the lineage: callers treat a missing pair as OTHER
    assert (ColumnRef("orders", "amount"), ColumnRef("m", "x")) not in column_uses(project(m=sql))


# --- on a real dbt project ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def jaffle() -> Project:
    return load_dbt_project(Path(__file__).parent / "fixtures" / "jaffle_shop_dbt")


def test_jaffle_stg_payments_divides_the_amount_so_a_text_amount_fails_there(jaffle):
    uses = column_uses(jaffle)
    assert (
        uses[(ColumnRef("raw_payments", "amount"), ColumnRef("stg_payments", "amount"))] == STRICT
    )


def test_jaffle_staging_columns_that_are_only_renamed_pass(jaffle):
    uses = column_uses(jaffle)
    assert uses[(ColumnRef("raw_orders", "status"), ColumnRef("stg_orders", "status"))] == PASS
    assert (
        uses[(ColumnRef("raw_customers", "id"), ColumnRef("stg_customers", "customer_id"))] == PASS
    )


def test_jaffle_orders_sums_the_payment_amount_inside_a_case(jaffle):
    uses = column_uses(jaffle)
    key = (ColumnRef("stg_payments", "amount"), ColumnRef("orders", "amount"))
    assert uses[key] == STRICT

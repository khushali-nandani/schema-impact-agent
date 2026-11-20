import pytest

from project_edits import (
    add_column,
    add_model,
    change_model_sql,
    change_type,
    drop_column,
    drop_model,
    drop_table,
    rename_column,
)
from schema_impact.core.changes import (
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    ModelDropped,
    TableDropped,
    diff_projects,
)
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def test_identical_projects_have_no_changes(before):
    assert diff_projects(before, before) == []


def test_renamed_column_is_detected(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    assert diff_projects(before, after) == [ColumnRenamed("orders", "customer_id", "user_id")]


def test_dropped_last_column_is_detected(before):
    after = drop_column(before, "orders", "amount")
    assert diff_projects(before, after) == [ColumnDropped("orders", "amount")]


def test_dropped_middle_column_is_detected(before):
    # columns after it shift left, but nothing was added, so it is a plain drop
    after = drop_column(before, "orders", "status")
    assert diff_projects(before, after) == [ColumnDropped("orders", "status")]


def test_type_change_is_detected(before):
    after = change_type(before, "orders", "amount", "VARCHAR")
    assert diff_projects(before, after) == [
        ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR")
    ]


def test_dropped_table_is_one_change_not_one_per_column(before):
    after = drop_table(before, "order_items")
    assert diff_projects(before, after) == [TableDropped("order_items")]


def test_added_column_is_not_a_change(before):
    after = add_column(before, "orders", "discount", "INT")
    assert diff_projects(before, after) == []


def test_drop_plus_add_with_a_different_type_is_not_a_rename(before):
    after = add_column(drop_column(before, "orders", "amount"), "orders", "discount", "VARCHAR")
    assert diff_projects(before, after) == [ColumnDropped("orders", "amount")]


def test_changed_model_logic_is_detected(before):
    sql = before.models["product_sales"].sql.replace("'returned'", "'cancelled'")
    after = change_model_sql(before, "product_sales", sql)
    assert diff_projects(before, after) == [ModelChanged("product_sales")]


def test_formatting_only_change_to_a_model_is_ignored(before):
    sql = "-- reformatted\n" + before.models["customer_totals"].sql.replace(" ", "   ")
    after = change_model_sql(before, "customer_totals", sql)
    assert diff_projects(before, after) == []


def test_several_changes_are_all_reported(before):
    after = drop_column(rename_column(before, "customers", "name", "full_name"), "orders", "status")
    assert diff_projects(before, after) == [
        ColumnRenamed("customers", "name", "full_name"),
        ColumnDropped("orders", "status"),
    ]


def test_removed_model_is_detected(before):
    after = drop_model(before, "top_customers")
    assert diff_projects(before, after) == [ModelDropped("top_customers")]


def test_added_model_is_not_a_change(before):
    after = add_model(before, "extra", "SELECT id FROM orders")
    assert diff_projects(before, after) == []


# --- a model's own output columns --------------------------------------------

TOP = "SELECT name, total FROM customer_totals WHERE total > 1000 ORDER BY total DESC"


def test_a_renamed_output_column_of_a_model_is_detected(before):
    sql = TOP.replace("SELECT name,", "SELECT name AS customer_name,")
    after = change_model_sql(before, "top_customers", sql)
    changes = diff_projects(before, after)
    assert ColumnRenamed("top_customers", "name", "customer_name") in changes
    assert ModelChanged("top_customers") in changes


def test_a_dropped_output_column_of_a_model_is_detected(before):
    after = change_model_sql(before, "top_customers", "SELECT name FROM customer_totals")
    assert ColumnDropped("top_customers", "total") in diff_projects(before, after)


def test_a_logic_change_that_keeps_the_columns_adds_no_column_change(before):
    after = change_model_sql(before, "top_customers", TOP.replace("1000", "5000"))
    assert diff_projects(before, after) == [ModelChanged("top_customers")]


def test_the_columns_of_a_select_star_are_known_before_and_after(before):
    star = add_model(before, "everyone", "SELECT * FROM customers")
    after = change_model_sql(star, "everyone", "SELECT id, name FROM customers")
    assert ColumnDropped("everyone", "country") in diff_projects(star, after)


def test_a_model_whose_new_sql_no_longer_resolves_is_still_compared(before):
    # it now reads a column that does not exist; its select list is still readable
    base = add_model(before, "m", "SELECT id, name FROM customers")
    after = change_model_sql(base, "m", "SELECT id, nonexistent FROM customers")
    assert ColumnRenamed("m", "name", "nonexistent") in diff_projects(base, after)


def test_an_unresolvable_select_star_gives_only_the_model_change(before):
    base = add_model(before, "m", "SELECT id FROM customers")
    after = change_model_sql(base, "m", "SELECT * FROM a_table_that_does_not_exist")
    assert diff_projects(base, after) == [ModelChanged("m")]


def test_a_model_that_was_not_edited_is_not_analysed_against_the_new_schema(before):
    # customer_totals' SQL reads orders.amount, which is gone: that must not make the diff fail
    after = drop_column(before, "orders", "amount")
    assert diff_projects(before, after) == [ColumnDropped("orders", "amount")]

import pytest
from schema_impact.core.changes import (
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    TableDropped,
    diff_projects,
)

from project_edits import (
    add_column,
    change_model_sql,
    change_type,
    drop_column,
    drop_table,
    rename_column,
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

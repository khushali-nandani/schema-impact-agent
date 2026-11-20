from schema_impact.agent.describe import describe_change, describe_place

from schema_impact.core.changes import (
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    ModelDropped,
    TableDropped,
)
from schema_impact.core.impact import BREAKING, Impact


def impact(model: str, column: str | None) -> Impact:
    return Impact(ColumnDropped("a", "b"), model, column, frozenset({"select"}), BREAKING, ())


def test_describe_a_renamed_column():
    text = describe_change(ColumnRenamed("orders", "customer_id", "user_id"))
    assert text == "Column orders.customer_id was renamed to user_id"


def test_describe_a_dropped_column():
    assert describe_change(ColumnDropped("orders", "amount")) == "Column orders.amount was dropped"


def test_describe_a_type_change():
    text = describe_change(ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR"))
    assert text == "Type of orders.amount changed from DECIMAL(10, 2) to VARCHAR"


def test_describe_a_dropped_table():
    assert describe_change(TableDropped("order_items")) == "Table order_items was dropped"


def test_describe_a_dropped_model():
    assert describe_change(ModelDropped("customer_totals")) == "Model customer_totals was dropped"


def test_describe_a_changed_model():
    assert (
        describe_change(ModelChanged("product_sales")) == "The SQL of model product_sales changed"
    )


def test_describe_a_column_place():
    assert describe_place(impact("customer_totals", "total")) == "customer_totals.total"


def test_describe_a_whole_model_place():
    assert describe_place(impact("customer_totals", None)) == "customer_totals (whole model)"

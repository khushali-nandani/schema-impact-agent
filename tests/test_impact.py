"""Impact of hand-made changes on the toy project.

Models: customer_totals (reads orders, customers), product_sales (reads order_items,
orders, products), top_customers (reads customer_totals).
"""

from pathlib import Path

import pytest

from project_edits import (
    change_model_sql,
    change_type,
    drop_column,
    drop_model,
    drop_table,
    rename_column,
)
from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.core.changes import (
    ColumnDropped,
    ColumnRenamed,
    ColumnTypeChanged,
    ModelChanged,
    ModelDropped,
    TableDropped,
    diff_projects,
)
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact, compute_impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def places(impacts):
    """The (model, column) pairs that are affected. column None means the whole model."""
    return {(i.model, i.column) for i in impacts}


def models(impacts):
    return {i.model for i in impacts}


# --- the 10 hand-made changes -----------------------------------------------


def test_1_rename_join_key_breaks_the_model_and_everything_reading_it(before):
    impacts = compute_impact(before, [ColumnRenamed("orders", "customer_id", "user_id")])
    assert places(impacts) == {
        ("customer_totals", None),
        ("top_customers", "name"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    assert {i.effect for i in impacts} == {BREAKING}
    own = next(i for i in impacts if i.model == "customer_totals")
    assert own.roles == frozenset({"join"})


def test_2_drop_a_summed_column_takes_the_whole_model_down_and_so_its_readers(before):
    # customer_totals can no longer run at all, so top_customers cannot build, whichever of
    # customer_totals' columns it reads (name as well as total)
    impacts = compute_impact(before, [ColumnDropped("orders", "amount")])
    assert places(impacts) == {
        ("customer_totals", "total"),
        ("top_customers", "name"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    assert "product_sales" not in models(impacts)


def test_3_drop_a_filter_column_breaks_only_the_model_that_filters(before):
    impacts = compute_impact(before, [ColumnDropped("orders", "status")])
    assert places(impacts) == {("product_sales", None)}
    assert impacts[0].roles == frozenset({"where"})


def test_4_rename_a_select_and_group_by_column(before):
    impacts = compute_impact(before, [ColumnRenamed("customers", "name", "full_name")])
    assert places(impacts) == {
        ("customer_totals", "name"),
        ("customer_totals", None),
        ("top_customers", "name"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    own = {i.column: i.roles for i in impacts if i.model == "customer_totals"}
    assert own == {"name": frozenset({"select"}), None: frozenset({"group_by"})}


def test_5_rename_a_column_used_in_an_expression(before):
    impacts = compute_impact(before, [ColumnRenamed("products", "price", "unit_price")])
    assert places(impacts) == {("product_sales", "revenue")}


def test_6_type_change_needs_review_and_follows_the_chain(before):
    change = ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR")
    impacts = compute_impact(before, [change])
    assert places(impacts) == {
        ("customer_totals", "total"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    assert {i.effect for i in impacts} == {NEEDS_REVIEW}


def test_7_dropping_an_unused_column_is_safe(before):
    assert compute_impact(before, [ColumnDropped("customers", "country")]) == []


def test_8_rename_a_join_key_used_by_one_model(before):
    impacts = compute_impact(before, [ColumnRenamed("orders", "id", "order_id")])
    assert places(impacts) == {("product_sales", None)}


def test_9_dropping_a_table_breaks_every_model_that_reads_it(before):
    impacts = compute_impact(before, [TableDropped("order_items")])
    assert places(impacts) == {
        ("product_sales", None),
        ("product_sales", "units_sold"),
        ("product_sales", "revenue"),
    }
    assert {i.effect for i in impacts} == {BREAKING}


def test_10_changed_model_logic_needs_review_for_its_readers(before):
    # customer_totals is read by top_customers, so a logic change there can shift its numbers
    impacts = compute_impact(before, [ModelChanged("customer_totals")])
    assert places(impacts) == {
        ("top_customers", "name"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    assert {i.effect for i in impacts} == {NEEDS_REVIEW}


def test_10b_changed_model_logic_with_no_readers_affects_nothing_downstream(before):
    assert compute_impact(before, [ModelChanged("product_sales")]) == []


# --- details ----------------------------------------------------------------


def test_effect_follows_the_kind_of_change(before):
    rename = compute_impact(before, [ColumnRenamed("orders", "amount", "total")])
    retype = compute_impact(before, [ColumnTypeChanged("orders", "amount", "A", "B")])
    assert {i.effect for i in rename} == {BREAKING}
    assert {i.effect for i in retype} == {NEEDS_REVIEW}


def test_impact_records_the_path_through_the_chain(before):
    impacts = compute_impact(before, [ColumnDropped("orders", "amount")])
    top_total = next(i for i in impacts if i.model == "top_customers" and i.column == "total")
    assert top_total.path == (
        ColumnRef("orders", "amount"),
        ColumnRef("customer_totals", "total"),
        ColumnRef("top_customers", "total"),
    )


def test_each_impact_points_back_to_the_change_that_caused_it(before):
    safe = ColumnDropped("customers", "country")
    risky = ColumnDropped("orders", "status")
    impacts = compute_impact(before, [safe, risky])
    assert {i.change for i in impacts} == {risky}


def test_diff_then_impact_end_to_end(before):
    after = drop_column(
        rename_column(before, "orders", "customer_id", "user_id"), "orders", "status"
    )
    impacts = compute_impact(before, diff_projects(before, after))
    assert models(impacts) == {"customer_totals", "top_customers", "product_sales"}


def test_changes_that_edit_models_or_types_work_through_diff(before):
    sql = before.models["customer_totals"].sql.replace("GROUP BY", "WHERE o.amount > 0 GROUP BY")
    after = change_type(
        change_model_sql(before, "customer_totals", sql), "orders", "amount", "BIGINT"
    )
    changes = diff_projects(before, after)
    assert ModelChanged("customer_totals") in changes
    assert {i.effect for i in compute_impact(before, changes)} == {NEEDS_REVIEW}


def test_dropping_a_table_via_diff(before):
    after = drop_table(before, "order_items")
    assert models(compute_impact(before, diff_projects(before, after))) == {"product_sales"}


def test_dropping_a_model_breaks_the_models_that_read_it(before):
    impacts = compute_impact(before, [ModelDropped("customer_totals")])
    assert places(impacts) == {
        ("top_customers", "name"),
        ("top_customers", "total"),
        ("top_customers", None),
    }
    assert {i.effect for i in impacts} == {BREAKING}


def test_dropping_a_model_nobody_reads_affects_nothing_else(before):
    assert compute_impact(before, [ModelDropped("product_sales")]) == []


def test_dropping_a_model_via_diff(before):
    after = drop_model(before, "customer_totals")
    assert models(compute_impact(before, diff_projects(before, after))) == {"top_customers"}


def test_impact_keeps_its_path_a_tuple_even_if_built_from_a_list():
    # a checkpoint round trip hands tuples back as lists
    impact = Impact(
        change=ColumnDropped("orders", "amount"),
        model="customer_totals",
        column="total",
        roles=frozenset({"select"}),
        effect=BREAKING,
        path=[ColumnRef("orders", "amount"), ColumnRef("customer_totals", "total")],
    )
    assert isinstance(impact.path, tuple)
    assert hash(impact) is not None


# --- a breaking impact takes the whole model down ---------------------------------


def test_a_model_that_cannot_run_breaks_every_reader_even_of_columns_it_does_not_use(before):
    # top_customers reads customer_totals.name, which has nothing to do with orders.amount
    impacts = compute_impact(before, [ColumnDropped("orders", "amount")])
    assert ("top_customers", "name") in places(impacts)


def test_a_type_change_does_not_do_that(before):
    # the queries still run: only what is computed from the column is affected
    change = ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR")
    assert ("top_customers", "name") not in places(compute_impact(before, [change]))


def test_a_dropped_seed_column_reaches_models_that_never_read_the_staging_column():
    # on a real dbt project: raw_payments.id only feeds stg_payments.payment_id, which no mart
    # reads, yet stg_payments fails and customers and orders are skipped
    jaffle = load_dbt_project(Path(__file__).parent / "fixtures" / "jaffle_shop_dbt")
    impacts = compute_impact(jaffle, [ColumnDropped("raw_payments", "id")])
    assert models(impacts) == {"stg_payments", "customers", "orders"}
    assert {i.effect for i in impacts} == {BREAKING}


def test_renaming_the_output_column_of_a_model_breaks_the_models_that_read_it(before):
    impacts = compute_impact(before, [ColumnRenamed("customer_totals", "name", "customer_name")])
    assert places(impacts) == {("top_customers", "name")}
    assert {i.effect for i in impacts} == {BREAKING}


def test_dropping_an_output_column_nobody_reads_affects_nothing(before):
    assert compute_impact(before, [ColumnDropped("top_customers", "name")]) == []

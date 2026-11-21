"""A column that becomes text breaks the models that give it to something that needs numbers.

`orders.amount` going from DECIMAL to VARCHAR changes nothing for a model that only selects it,
but `SUM(amount)` fails on text, so the model cannot be built and every model that reads it is
skipped, whichever of its columns they use. A type change that cannot make a query fail (to
another number type, or from text to text) still only needs review.

Measured on DuckDB only; other dialects keep the old behaviour (a type change needs review), since
some of them convert text to numbers on the fly.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from project_edits import change_type
from schema_impact.core.changes import ColumnTypeChanged, diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, compute_impact
from schema_impact.core.registry import Model, Project, load_project
from schema_impact.evals.scorer import PREDICTED_BREAKING as B
from schema_impact.evals.scorer import PREDICTED_NONE as N
from schema_impact.evals.scorer import predict_case

ALL = ["customer_totals", "product_sales", "top_customers"]
TO_TEXT = ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR")

SCHEMA = {
    "orders": {"id": "INTEGER", "customer_id": "INTEGER", "status": "VARCHAR", "amount": "DOUBLE"},
    "events": {"id": "INTEGER", "started": "TIMESTAMP", "ended": "TIMESTAMP"},
}


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def project(**models: str) -> Project:
    return Project(
        models={name: Model(name, sql, Path(f"{name}.sql")) for name, sql in models.items()},
        schema=SCHEMA,
        dialect="duckdb",
    )


def effects(impacts) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for impact in impacts:
        result.setdefault(impact.model, set()).add(impact.effect)
    return result


# --- on the toy project -------------------------------------------------------------------------


def test_summing_a_column_that_became_text_breaks_the_model(before):
    assert effects(compute_impact(before, [TO_TEXT]))["customer_totals"] == {BREAKING}


def test_a_failing_model_takes_everything_that_reads_it_down(before):
    impacts = compute_impact(before, [TO_TEXT])
    assert effects(impacts) == {"customer_totals": {BREAKING}, "top_customers": {BREAKING}}
    # whichever column of customer_totals the reader uses
    assert {i.column for i in impacts if i.model == "top_customers"} >= {"name", "total"}


def test_a_model_that_does_not_read_the_column_is_not_affected(before):
    assert "product_sales" not in effects(compute_impact(before, [TO_TEXT]))


def test_text_through_a_multiplication_breaks_the_model_too(before):
    change = ColumnTypeChanged("products", "price", "DECIMAL(10, 2)", "VARCHAR")
    assert effects(compute_impact(before, [change]))["product_sales"] == {BREAKING}


def test_everything_found_before_is_still_found(before):
    old = compute_impact(before, [replace(TO_TEXT, new_type="DOUBLE")])
    new = compute_impact(before, [TO_TEXT])
    assert {(i.model, i.column) for i in old} <= {(i.model, i.column) for i in new}


@pytest.mark.parametrize("new_type", ["VARCHAR", "varchar", "TEXT", "STRING", "VARCHAR(20)"])
def test_every_spelling_of_text_counts(before, new_type):
    change = replace(TO_TEXT, new_type=new_type)
    assert effects(compute_impact(before, [change]))["customer_totals"] == {BREAKING}


@pytest.mark.parametrize("new_type", ["DOUBLE", "BIGINT", "DECIMAL(18, 4)", "DATE"])
def test_a_type_that_is_not_text_still_only_needs_review(before, new_type):
    change = replace(TO_TEXT, new_type=new_type)
    assert set().union(*effects(compute_impact(before, [change])).values()) == {NEEDS_REVIEW}


def test_text_to_text_is_not_a_failure(before):
    change = ColumnTypeChanged("orders", "amount", "VARCHAR", "TEXT")
    assert BREAKING not in set().union(*effects(compute_impact(before, [change])).values())


def test_a_column_used_only_in_a_join_is_not_a_failure(before):
    change = ColumnTypeChanged("orders", "customer_id", "INTEGER", "VARCHAR")
    impacts = compute_impact(before, [change])
    assert impacts and {i.effect for i in impacts} == {NEEDS_REVIEW}


# --- along a chain of models --------------------------------------------------------------------


def test_a_column_passed_on_unchanged_fails_where_it_is_summed():
    chain = project(
        a="SELECT amount AS amt FROM orders",
        b="SELECT SUM(amt) AS total FROM a",
        c="SELECT total FROM b",
    )
    result = effects(compute_impact(chain, [TO_TEXT]))
    assert result == {"a": {NEEDS_REVIEW}, "b": {BREAKING}, "c": {BREAKING}}


def test_a_cast_stops_the_text_from_reaching_the_sum():
    chain = project(
        a="SELECT CAST(amount AS DOUBLE) AS amt FROM orders",
        b="SELECT SUM(amt) AS total FROM a",
    )
    result = effects(compute_impact(chain, [TO_TEXT]))
    assert result == {"a": {NEEDS_REVIEW}, "b": {NEEDS_REVIEW}}


def test_arithmetic_on_text_fails():
    chain = project(a="SELECT amount / 100 AS amt FROM orders")
    assert effects(compute_impact(chain, [TO_TEXT])) == {"a": {BREAKING}}


def test_date_diff_on_a_timestamp_that_became_text_fails():
    chain = project(a="SELECT date_diff('day', started, ended) AS days FROM events")
    change = ColumnTypeChanged("events", "started", "TIMESTAMP", "VARCHAR")
    assert effects(compute_impact(chain, [change])) == {"a": {BREAKING}}


def test_each_reader_is_judged_by_its_own_use():
    chain = project(
        strict="SELECT SUM(amount) AS s FROM orders",
        plain="SELECT amount AS a FROM orders",
        counted="SELECT COUNT(amount) AS n FROM orders",
    )
    result = effects(compute_impact(chain, [TO_TEXT]))
    assert result == {
        "strict": {BREAKING},
        "plain": {NEEDS_REVIEW},
        "counted": {NEEDS_REVIEW},
    }


# --- only where it was measured -----------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["postgres", "bigquery", "mysql"])
def test_other_dialects_keep_the_old_behaviour(before, dialect):
    other = replace(before, dialect=dialect)
    assert set().union(*effects(compute_impact(other, [TO_TEXT])).values()) == {NEEDS_REVIEW}


# --- through a diff and the scorer --------------------------------------------------------------


def test_the_change_is_found_from_a_diff_and_breaks_the_model(before):
    after = change_type(before, "orders", "amount", "VARCHAR")
    changes = diff_projects(before, after)
    assert effects(compute_impact(before, changes))["customer_totals"] == {BREAKING}


def test_the_scorer_predicts_breaking_for_the_failing_model_and_what_reads_it(before):
    after = change_type(before, "orders", "amount", "VARCHAR")
    assert predict_case(before, after, ALL) == {
        "customer_totals": B,
        "product_sales": N,
        "top_customers": B,
    }

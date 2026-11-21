"""An edit that only changes how some output columns of a model are computed.

Before, any such edit was "the SQL of the model changed", so every model that reads the edited one,
and every model that reads those, was flagged, whichever columns they use. Now it is
`ColumnLogicChanged(model, column)` for each column that is computed differently, and the effect
follows those columns only. The whole model is still reported as changed when the rows may differ
(a filter, a join, a grouping, a changed column that feeds one, an ORDER BY that reads one), and
for anything that cannot be narrowed down; a renamed CTE or different spacing is no change at all.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from project_edits import add_model, change_model_sql
from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.agent.checkpoint import make_checkpointer, review_config
from schema_impact.agent.graph import build_graph
from schema_impact.agent.judge import LOW, FakeJudge, Judgment
from schema_impact.agent.state import new_review
from schema_impact.core.changes import ColumnLogicChanged, ModelChanged, diff_projects
from schema_impact.core.impact import NEEDS_REVIEW, compute_impact
from schema_impact.core.registry import load_project
from schema_impact.evals.dataset import load_dataset
from schema_impact.evals.scorer import (
    PREDICTED_BREAKING,
    PREDICTED_NONE,
    PREDICTED_REVIEW,
    score_dataset,
)

REPO = Path(__file__).resolve().parent.parent
R, N = PREDICTED_REVIEW, PREDICTED_NONE


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def edit(project, model: str, old: str, new: str):
    sql = project.models[model].sql
    assert old in sql, f"{old!r} is not in {model}"
    return change_model_sql(project, model, sql.replace(old, new))


def places(impacts):
    return {(i.model, i.column) for i in impacts}


# --- what the diff reports ----------------------------------------------------------------------


def test_changing_how_one_column_is_computed_is_a_change_of_that_column(before):
    after = edit(before, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    assert diff_projects(before, after) == [ColumnLogicChanged("customer_totals", "total")]


def test_every_column_computed_differently_is_reported_in_output_order(before):
    after = edit(
        edit(before, "product_sales", "SUM(oi.quantity) AS", "SUM(oi.quantity) + 1 AS"),
        "product_sales",
        "SUM(oi.quantity * p.price)",
        "SUM(oi.quantity * p.price) * 2",
    )
    assert diff_projects(before, after) == [
        ColumnLogicChanged("product_sales", "units_sold"),
        ColumnLogicChanged("product_sales", "revenue"),
    ]


def test_a_column_that_is_not_the_one_edited_is_not_reported(before):
    after = edit(before, "customer_totals", "c.name,", "UPPER(c.name) AS name,")
    assert diff_projects(before, after) == [ColumnLogicChanged("customer_totals", "name")]


CHAIN = "WITH a AS (SELECT id, amount * 2 AS x FROM orders) SELECT id, x FROM a"


def test_renaming_a_cte_is_not_a_change(before):
    base = add_model(before, "m", CHAIN)
    after = change_model_sql(
        base, "m", CHAIN.replace("WITH a AS", "WITH b AS").replace("FROM a", "FROM b")
    )
    assert diff_projects(base, after) == []


def test_a_changed_column_that_a_filter_of_the_same_model_reads_is_a_model_change(before):
    base = add_model(before, "m", CHAIN + " WHERE x > 10")
    after = change_model_sql(base, "m", (CHAIN + " WHERE x > 10").replace("* 2", "* 3"))
    assert diff_projects(base, after) == [ModelChanged("m")]


def test_a_changed_filter_is_still_a_model_change(before):
    after = edit(before, "customer_totals", "GROUP BY", "WHERE o.amount > 0 GROUP BY")
    assert diff_projects(before, after) == [ModelChanged("customer_totals")]


def test_a_model_that_reads_a_changed_column_in_its_own_order_by_is_a_model_change(before):
    base = add_model(before, "m", "SELECT id, amount AS a FROM orders ORDER BY a LIMIT 5")
    after = change_model_sql(base, "m", "SELECT id, amount * 2 AS a FROM orders ORDER BY a LIMIT 5")
    assert diff_projects(base, after) == [ModelChanged("m")]


def test_each_edited_model_is_judged_on_its_own(before):
    after = edit(
        edit(before, "customer_totals", "SUM(o.amount)", "AVG(o.amount)"),
        "product_sales",
        "'returned'",
        "'cancelled'",
    )
    assert set(diff_projects(before, after)) == {
        ColumnLogicChanged("customer_totals", "total"),
        ModelChanged("product_sales"),
    }


def test_renaming_a_column_and_changing_its_logic_is_not_narrowed(before):
    sql = "SELECT name AS n, total * 2 AS total FROM customer_totals WHERE total > 1000"
    after = change_model_sql(before, "top_customers", sql)
    assert ModelChanged("top_customers") in diff_projects(before, after)


# --- on a real dbt project ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def jaffle():
    return load_dbt_project(Path(__file__).parent / "fixtures" / "jaffle_shop_dbt")


def test_jaffle_a_changed_total_is_only_the_amount_column_of_orders(jaffle):
    after = edit(jaffle, "orders", "SUM(amount) AS total_amount", "AVG(amount) AS total_amount")
    assert diff_projects(jaffle, after) == [ColumnLogicChanged("orders", "amount")]


def test_jaffle_renaming_a_cte_of_customers_is_not_a_change(jaffle):
    after = edit(jaffle, "customers", "customer_orders", "cust_orders")
    assert diff_projects(jaffle, after) == []


def test_jaffle_a_changed_column_of_stg_orders_is_not_felt_by_a_model_that_does_not_read_it(jaffle):
    after = edit(jaffle, "stg_orders", "status", "UPPER(status) AS status")
    changes = diff_projects(jaffle, after)
    assert changes == [ColumnLogicChanged("stg_orders", "status")]
    # customers reads stg_orders but not its status; orders does
    assert {i.model for i in compute_impact(jaffle, changes)} == {"orders"}


# --- what the impact is -------------------------------------------------------------------------


def test_a_changed_column_reaches_the_places_that_read_that_column(before):
    impacts = compute_impact(before, [ColumnLogicChanged("customer_totals", "name")])
    assert places(impacts) == {("top_customers", "name")}
    assert {i.effect for i in impacts} == {NEEDS_REVIEW}


def test_a_changed_column_that_a_reader_filters_on_reaches_the_whole_reader(before):
    impacts = compute_impact(before, [ColumnLogicChanged("customer_totals", "total")])
    # top_customers selects total and filters on it (WHERE total > 1000)
    assert places(impacts) == {("top_customers", "total"), ("top_customers", None)}


def test_a_changed_column_that_nothing_reads_affects_nothing(before):
    assert compute_impact(before, [ColumnLogicChanged("top_customers", "name")]) == []


def test_a_model_change_still_reaches_every_column_of_its_readers(before):
    impacts = compute_impact(before, [ModelChanged("customer_totals")])
    assert ("top_customers", "name") in places(impacts)
    assert ("top_customers", "total") in places(impacts)


def test_a_changed_column_never_reaches_a_model_that_does_not_read_its_model(before):
    impacts = compute_impact(before, [ColumnLogicChanged("customer_totals", "total")])
    assert "product_sales" not in {i.model for i in impacts}


# --- the scorer ---------------------------------------------------------------------------------


def predict(before, after, models):
    from schema_impact.evals.scorer import predict_case

    return predict_case(before, after, models)


def test_the_scorer_flags_the_readers_of_a_changed_column(before):
    after = edit(before, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    assert predict(before, after, ["product_sales", "top_customers"]) == {
        "product_sales": N,
        "top_customers": R,
    }


def test_the_scorer_flags_only_the_places_of_the_column_that_changed(before):
    after = edit(before, "customer_totals", "c.name,", "UPPER(c.name) AS name,")
    impacts = compute_impact(before, diff_projects(before, after))
    assert places(impacts) == {("top_customers", "name")}


def test_the_scorer_flags_nothing_for_a_renamed_cte(before):
    base = add_model(add_model(before, "m", CHAIN), "reader", "SELECT id FROM m")
    after = change_model_sql(
        base, "m", CHAIN.replace("WITH a AS", "WITH b AS").replace("FROM a", "FROM b")
    )
    assert predict(base, after, ["reader"]) == {"reader": N}


def test_the_scorer_still_flags_the_readers_of_a_changed_filter(before):
    after = edit(before, "customer_totals", "GROUP BY", "WHERE o.amount > 0 GROUP BY")
    assert predict(before, after, ["top_customers"]) == {"top_customers": R}


# --- through the agent and a checkpoint ---------------------------------------------------------


def test_a_changed_column_goes_to_the_judge_and_the_review_can_be_saved_and_read_back(
    before, caplog
):
    after = edit(before, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    judge = FakeJudge(Judgment(LOW, "fine", "nothing"))
    app = build_graph(judge, make_checkpointer())
    config = review_config("pr-7")

    result = app.invoke(new_review(before, after), config)

    assert result["changes"] == [ColumnLogicChanged("customer_totals", "total")]
    assert (
        len(judge.calls) == len(result["impacts"]) == 2
    )  # top_customers.total and the whole model
    assert result["needs_approval"] is False
    saved = app.get_state(config).values
    assert saved["changes"] == result["changes"]
    assert "unregistered" not in caplog.text and "Blocked" not in caplog.text


# --- guards on the committed datasets (no dbt needed) -------------------------------------------


def logic_cases(path: str, only: set[str] | None = None):
    dataset = load_dataset(REPO / "benchmarks" / "dataset" / path)
    cases = [
        c
        for c in dataset.cases
        if c.mutation.kind == "model_logic"
        and (only is None or c.mutation.id.split(":", 1)[1] in only)
    ]
    assert cases
    return score_dataset(replace(dataset, cases=cases)).outcomes


# a few Olist edits of each kind: one column, a refactor, and edits that change the rows
OLIST_SAMPLE = {
    "stg_orders.upper_status",
    "int_order_items.average_price",
    "customer_satisfaction.whole_spend",
    "fct_orders.rename_cte",
    "fct_orders.inner_join_items",
    "stg_order_items.only_priced_above_ten",
}


@pytest.fixture(scope="module")
def olist_outcomes():
    return logic_cases("olist.json", OLIST_SAMPLE)


@pytest.fixture(scope="module")
def jaffle_outcomes():
    return logic_cases("jaffle_shop.json")


@pytest.mark.parametrize("outcomes", ["olist_outcomes", "jaffle_outcomes"])
def test_no_silent_model_is_ever_missed(outcomes, request):
    result = request.getfixturevalue(outcomes)
    silent = [o for o in result if o.truth == "silent"]
    assert silent and all(o.prediction != N for o in silent)


@pytest.mark.parametrize("outcomes", ["olist_outcomes", "jaffle_outcomes"])
def test_no_model_is_called_breaking_that_is_not(outcomes, request):
    result = request.getfixturevalue(outcomes)
    assert all(o.truth == "breaking" for o in result if o.prediction == PREDICTED_BREAKING)


@pytest.mark.parametrize("outcomes", ["olist_outcomes", "jaffle_outcomes"])
def test_nothing_that_dbt_says_changed_is_predicted_unchanged(outcomes, request):
    result = request.getfixturevalue(outcomes)
    assert not [o for o in result if o.truth != "safe" and o.prediction == N]


def test_an_edit_of_one_olist_column_has_no_false_alarm(olist_outcomes):
    for case in ("stg_orders.upper_status", "int_order_items.average_price"):
        flagged_safe = [
            o
            for o in olist_outcomes
            if o.case_id.endswith(case) and o.truth == "safe" and o.prediction != N
        ]
        assert flagged_safe == [], case


def test_a_renamed_cte_of_olist_has_no_false_alarm(olist_outcomes):
    cases = [o for o in olist_outcomes if o.case_id.endswith("fct_orders.rename_cte")]
    assert cases and all(o.prediction == N for o in cases)


def test_the_false_alarms_of_all_jaffle_logic_edits_are_at_most_one(jaffle_outcomes):
    assert len([o for o in jaffle_outcomes if o.truth == "safe" and o.prediction != N]) <= 1

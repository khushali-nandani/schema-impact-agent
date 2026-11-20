from pathlib import Path

import pytest
from schema_impact.evals.llm_eval import (
    JudgedItem,
    apply_judgments,
    collect_review_items,
    format_comparison,
    item_key,
    judge_accuracy,
    judge_items,
    load_judgments,
    save_judgments,
)

from project_edits import change_type, rename_column
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment
from schema_impact.core.registry import load_project
from schema_impact.evals.dataset import Dataset, load_dataset
from schema_impact.evals.mutations import Mutation
from schema_impact.evals.results import CaseResult
from schema_impact.evals.scorer import (
    PREDICTED_BREAKING,
    PREDICTED_NONE,
    PREDICTED_REVIEW,
    Outcome,
    compute_metrics,
    score_dataset,
)

REPO = Path(__file__).resolve().parent.parent
B, R, N = PREDICTED_BREAKING, PREDICTED_REVIEW, PREDICTED_NONE


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def case(before, after, labels, case_id) -> CaseResult:
    return CaseResult(
        Mutation(case_id, "seed_change_type", "t.c", "d", {}), {}, labels, after, None
    )


@pytest.fixture
def small(before):
    """A type change (needs review) and a rename (breaking) on the toy project."""
    retype = case(
        before,
        change_type(before, "orders", "amount", "VARCHAR"),
        {"customer_totals": "silent", "product_sales": "safe", "top_customers": "silent"},
        "retype",
    )
    rename = case(
        before,
        rename_column(before, "orders", "customer_id", "user_id"),
        {"customer_totals": "breaking", "product_sales": "safe", "top_customers": "breaking"},
        "rename",
    )
    return Dataset({}, {}, before, [retype, rename])


def judged(case_id, model, risk, column=None, key=None) -> JudgedItem:
    return JudgedItem(
        key=key or f"{case_id}|{model}|{column}",
        case_id=case_id,
        model=model,
        column=column,
        roles=("select",),
        change="Type of orders.amount changed from A to B",
        risk=risk,
        explanation="why",
        fix="how",
    )


def outcome(case_id, model, truth, prediction):
    return Outcome(case_id=case_id, kind="k", model=model, truth=truth, prediction=prediction)


# --- which impacts are put to the judge -------------------------------------------


def test_only_impacts_of_models_predicted_needs_review_are_collected(small):
    items = collect_review_items(small)
    # the retype case reaches customer_totals and top_customers; product_sales is not reached
    assert {(i.case_id, i.model) for i in items} == {
        ("retype", "customer_totals"),
        ("retype", "top_customers"),
    }


def test_a_model_with_a_breaking_impact_is_not_put_to_the_judge(small):
    assert not [i for i in collect_review_items(small) if i.case_id == "rename"]


def test_each_item_carries_the_truth_and_its_impact(small):
    items = collect_review_items(small)
    assert {i.truth for i in items} == {"silent"}
    assert all(i.impact.effect == "needs_review" for i in items)
    assert {(i.model, i.impact.column) for i in items} == {
        ("customer_totals", "total"),
        ("top_customers", "total"),
        ("top_customers", None),
    }


def test_an_item_has_a_stable_key_that_tells_the_impacts_apart(small):
    keys = [item_key(i.case_id, i.impact) for i in collect_review_items(small)]
    assert len(keys) == len(set(keys)) == 3
    assert keys == [item_key(i.case_id, i.impact) for i in collect_review_items(small)]


def test_cases_that_ended_in_an_error_are_ignored(before):
    broken = CaseResult(Mutation("bad", "k", "t", "d", {}), {}, {}, None, "dbt fell over")
    assert collect_review_items(Dataset({}, {}, before, [broken])) == []


# --- asking the judge -------------------------------------------------------------


def test_every_item_is_judged_once_and_the_answer_is_kept(small):
    judge = FakeJudge(Judgment(HIGH, "numbers shift", "cast it"))
    result = judge_items(collect_review_items(small), judge, progress=lambda line: None)
    assert len(judge.calls) == len(result) == 3
    assert {j.risk for j in result} == {HIGH}
    assert {j.explanation for j in result} == {"numbers shift"}
    assert all("orders.amount" in j.change for j in result)


def test_items_already_judged_are_not_asked_again(small):
    items = collect_review_items(small)
    first = FakeJudge(Judgment(LOW, "a", "b"))
    done = judge_items(items[:1], first, progress=lambda line: None)

    second = FakeJudge(Judgment(HIGH, "c", "d"))
    result = judge_items(items, second, already=done, progress=lambda line: None)
    assert len(second.calls) == 2
    assert [j.risk for j in result] == [LOW, HIGH, HIGH]  # in the order of the items


def test_every_answer_can_be_saved_as_it_comes(small):
    saved = []
    judge_items(
        collect_review_items(small),
        FakeJudge(Judgment(LOW, "a", "b")),
        progress=lambda line: None,
        on_item=lambda all_so_far: saved.append(len(all_so_far)),
    )
    assert saved == [1, 2, 3]


def test_judgments_survive_a_round_trip_through_a_file(tmp_path):
    items = [judged("c1", "m1", HIGH, "total"), judged("c2", "m2", LOW)]
    save_judgments(tmp_path / "j.json", items, judge_model="some-model")
    meta, loaded = load_judgments(tmp_path / "j.json")
    assert loaded == items
    assert meta["judge_model"] == "some-model"
    assert meta["items"] == 2


# --- turning the answers into predictions --------------------------------------------


def test_a_high_risk_answer_keeps_the_model_flagged_and_a_low_one_drops_it():
    outcomes = [outcome("c", "high_model", "silent", R), outcome("c", "low_model", "safe", R)]
    answers = [judged("c", "high_model", HIGH), judged("c", "low_model", LOW)]
    result = {o.model: o.prediction for o in apply_judgments(outcomes, answers)}
    assert result == {"high_model": R, "low_model": N}


def test_one_high_answer_among_several_is_enough_to_keep_the_model_flagged():
    outcomes = [outcome("c", "m", "silent", R)]
    answers = [judged("c", "m", LOW, "a", key="1"), judged("c", "m", HIGH, "b", key="2")]
    assert apply_judgments(outcomes, answers)[0].prediction == R


def test_a_model_the_judge_was_not_asked_about_stays_flagged():
    outcomes = [outcome("c", "m", "silent", R)]
    assert apply_judgments(outcomes, [])[0].prediction == R


def test_breaking_and_none_predictions_are_never_changed():
    outcomes = [outcome("c", "a", "breaking", B), outcome("c", "b", "safe", N)]
    answers = [judged("c", "a", LOW), judged("c", "b", LOW)]
    assert [o.prediction for o in apply_judgments(outcomes, answers)] == [B, N]


def test_the_judge_is_scored_against_the_truth():
    outcomes = [
        outcome("c", "m1", "silent", R),
        outcome("c", "m2", "silent", R),
        outcome("c", "m3", "safe", R),
        outcome("c", "m4", "breaking", R),
        outcome("c", "m5", "safe", N),  # not asked: not counted
    ]
    answers = [
        judged("c", "m1", HIGH),
        judged("c", "m2", LOW),
        judged("c", "m3", LOW),
        judged("c", "m4", HIGH),
    ]
    assert judge_accuracy(outcomes, answers) == {
        ("silent", HIGH): 1,
        ("silent", LOW): 1,
        ("safe", LOW): 1,
        ("breaking", HIGH): 1,
    }


def test_the_comparison_shows_both_columns_and_what_the_judge_got_right():
    outcomes = [outcome("c", "m1", "silent", R), outcome("c", "m2", "safe", R)]
    answers = [judged("c", "m1", HIGH), judged("c", "m2", LOW)]
    text = format_comparison(
        compute_metrics(outcomes),
        compute_metrics(apply_judgments(outcomes, answers)),
        judge_accuracy(outcomes, answers),
    )
    assert "| Metric | Deterministic | With the judge |" in text
    assert "| Affected models: precision | 50.0% (1/2) | 100.0% (1/1) |" in text
    assert "| truth \\ judge | high | low |" in text
    assert "| silent | 1 | 0 |" in text
    assert "| safe | 0 | 1 |" in text


# --- on the real dataset, with a fake judge -------------------------------------------


@pytest.fixture(scope="module")
def real():
    dataset = load_dataset(REPO / "benchmarks" / "dataset" / "jaffle_shop.json")
    return dataset, score_dataset(dataset).outcomes, collect_review_items(dataset)


def test_every_needs_review_pair_of_the_real_dataset_has_an_item(real):
    _, outcomes, items = real
    flagged = {(o.case_id, o.model) for o in outcomes if o.prediction == R}
    assert flagged == {(i.case_id, i.model) for i in items}


def test_a_judge_that_always_says_high_changes_nothing(real):
    _, outcomes, items = real
    answers = judge_items(items, FakeJudge(Judgment(HIGH, "", "")), progress=lambda line: None)
    assert compute_metrics(apply_judgments(outcomes, answers)) == compute_metrics(outcomes)


def test_a_judge_that_always_says_low_keeps_only_the_breaking_predictions(real):
    _, outcomes, items = real
    answers = judge_items(items, FakeJudge(Judgment(LOW, "", "")), progress=lambda line: None)
    metrics = compute_metrics(apply_judgments(outcomes, answers))
    assert metrics.affected_precision.value == 1.0  # nothing is flagged but breaking models
    assert metrics.affected_recall.numerator == metrics.breaking_recall.numerator

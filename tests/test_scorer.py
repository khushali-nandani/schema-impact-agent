from pathlib import Path

import pytest

from project_edits import change_type, rename_column
from schema_impact.core.registry import load_project
from schema_impact.evals.dataset import Dataset, load_dataset
from schema_impact.evals.mutations import Mutation
from schema_impact.evals.results import CaseResult
from schema_impact.evals.scorer import (
    PREDICTED_BREAKING,
    PREDICTED_NONE,
    PREDICTED_REVIEW,
    Outcome,
    build_report,
    compute_metrics,
    format_by_kind,
    format_confusion,
    format_errors,
    format_report,
    metrics_by_kind,
    predict_case,
    score_dataset,
)

REPO = Path(__file__).resolve().parent.parent
B, R, N = PREDICTED_BREAKING, PREDICTED_REVIEW, PREDICTED_NONE


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def outcome(truth: str, prediction: str, kind: str = "k", case: str = "c", model: str = "m"):
    return Outcome(case_id=case, kind=kind, model=model, truth=truth, prediction=prediction)


# --- predicting for one case ---------------------------------------------------

ALL = ["customer_totals", "product_sales", "top_customers"]


def test_a_breaking_change_is_predicted_breaking_where_it_reaches(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    assert predict_case(before, after, ALL) == {
        "customer_totals": B,
        "product_sales": N,
        "top_customers": B,  # reached through customer_totals
    }


def test_a_type_change_is_predicted_needs_review(before):
    after = change_type(before, "orders", "amount", "DOUBLE")
    assert predict_case(before, after, ALL) == {
        "customer_totals": R,
        "product_sales": N,
        "top_customers": R,
    }


def test_breaking_wins_over_needs_review_for_the_same_model(before):
    after = change_type(
        rename_column(before, "orders", "customer_id", "user_id"), "orders", "amount", "BIGINT"
    )
    assert predict_case(before, after, ALL)["customer_totals"] == B


def test_only_the_asked_models_are_predicted(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    assert predict_case(before, after, ["product_sales"]) == {"product_sales": N}


def test_no_change_predicts_nothing_anywhere(before):
    assert set(predict_case(before, before, ALL).values()) == {N}


# --- from a dataset to outcomes -------------------------------------------------


def case(before_project, after_project, labels, error=None, case_id="x") -> CaseResult:
    mutation = Mutation(case_id, "seed_rename_column", "t.c", "d", {})
    return CaseResult(mutation, {}, labels, after_project, error)


def dataset(before_project, *cases: CaseResult) -> Dataset:
    return Dataset({}, {}, before_project, list(cases))


def test_each_scored_model_of_each_case_becomes_an_outcome(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    labels = {"customer_totals": "breaking", "product_sales": "safe", "top_customers": "breaking"}
    result = score_dataset(dataset(before, case(before, after, labels, case_id="a")))
    assert {(o.model, o.truth, o.prediction) for o in result.outcomes} == {
        ("customer_totals", "breaking", B),
        ("product_sales", "safe", N),
        ("top_customers", "breaking", B),
    }
    assert {o.case_id for o in result.outcomes} == {"a"}
    assert {o.kind for o in result.outcomes} == {"seed_rename_column"}


def test_a_case_that_ended_in_an_error_is_skipped_and_reported(before):
    broken = CaseResult(Mutation("bad", "k", "t", "d", {}), {}, {}, None, "dbt fell over")
    result = score_dataset(dataset(before, broken))
    assert result.outcomes == []
    assert result.skipped == ["bad"]


# --- the metrics -------------------------------------------------------------

#  truth     prediction   count
#  breaking  breaking      2
#  breaking  none          1
#  silent    needs_review  1
#  silent    none          1
#  safe      needs_review  1
#  safe      none          5
OUTCOMES = (
    [outcome("breaking", B)] * 2
    + [outcome("breaking", N)]
    + [outcome("silent", R), outcome("silent", N), outcome("safe", R)]
    + [outcome("safe", N)] * 5
)


def test_affected_precision_recall_and_f1():
    m = compute_metrics(OUTCOMES)
    assert (m.affected_precision.numerator, m.affected_precision.denominator) == (3, 4)
    assert (m.affected_recall.numerator, m.affected_recall.denominator) == (3, 5)
    assert m.affected_f1 == pytest.approx(2 * 0.75 * 0.6 / (0.75 + 0.6))


def test_breaking_counts_only_breaking_predictions():
    m = compute_metrics(OUTCOMES)
    assert (m.breaking_precision.numerator, m.breaking_precision.denominator) == (2, 2)
    assert (m.breaking_recall.numerator, m.breaking_recall.denominator) == (2, 3)


def test_a_silent_model_counts_as_flagged_by_any_prediction_but_none():
    m = compute_metrics(OUTCOMES)
    assert (m.silent_flagged.numerator, m.silent_flagged.denominator) == (1, 2)


def test_what_needs_review_turned_out_to_be():
    assert compute_metrics(OUTCOMES).review_breakdown == {"silent": 1, "safe": 1}


def test_the_confusion_matrix_counts_every_pair():
    confusion = compute_metrics(OUTCOMES).confusion
    assert confusion[("breaking", B)] == 2
    assert confusion[("silent", N)] == 1
    assert confusion[("safe", N)] == 5
    assert sum(confusion.values()) == len(OUTCOMES) == compute_metrics(OUTCOMES).pairs


def test_a_ratio_with_nothing_to_divide_is_undefined_not_zero():
    m = compute_metrics([])
    assert m.affected_precision.value is None
    assert m.affected_f1 is None
    assert compute_metrics([outcome("safe", N)]).affected_recall.value is None


def test_precision_is_undefined_when_nothing_was_predicted():
    m = compute_metrics([outcome("breaking", N), outcome("safe", N)])
    assert m.affected_precision.value is None
    assert m.affected_recall.value == 0.0


def test_metrics_can_be_split_by_kind():
    mixed = [
        outcome("breaking", B, kind="a"),
        outcome("safe", N, kind="a"),
        outcome("silent", N, kind="b"),
    ]
    by_kind = metrics_by_kind(mixed)
    assert set(by_kind) == {"a", "b"}
    assert by_kind["a"].pairs == 2 and by_kind["b"].pairs == 1
    assert by_kind["b"].silent_flagged.value == 0.0


# --- the report --------------------------------------------------------------


def test_the_report_shows_each_ratio_with_its_counts():
    text = format_report(compute_metrics(OUTCOMES))
    assert "75.0% (3/4)" in text
    assert "60.0% (3/5)" in text
    assert "66.7%" in text
    assert "100.0% (2/2)" in text
    assert "50.0% (1/2)" in text


def test_the_report_says_n_a_for_an_undefined_ratio():
    assert "n/a (0/0)" in format_report(compute_metrics([]))


def test_the_confusion_matrix_is_a_table_of_truth_against_prediction():
    text = format_confusion(compute_metrics(OUTCOMES))
    assert "| truth \\ predicted | breaking | needs_review | none |" in text
    assert "| silent | 0 | 1 | 1 |" in text
    assert "| breaking | 2 | 0 | 1 |" in text


def test_the_by_kind_table_has_one_row_per_kind():
    mixed = [
        outcome("breaking", B, kind="seed_rename_column"),
        outcome("silent", N, kind="model_logic"),
    ]
    text = format_by_kind(metrics_by_kind(mixed))
    assert "| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |" in text
    assert "| seed_rename_column | 1 | 100.0% (1/1) | 100.0% (1/1) | n/a (0/0) |" in text
    assert "| model_logic | 1 | 0.0% (0/1) | n/a (0/0) | 0.0% (0/1) |" in text


def test_the_error_lists_name_the_cases_and_models_that_went_wrong():
    outcomes = [
        outcome("breaking", N, case="c1", model="m1"),
        outcome("silent", N, case="c1", model="m2"),
        outcome("safe", R, case="c2", model="m1"),
        outcome("safe", N, case="c3", model="m1"),
    ]
    text = format_errors(outcomes)
    assert "Missed" in text and "False alarms" in text
    assert "c1: m1, m2" in text
    assert "c2: m1" in text
    assert "c3" not in text  # a correct "safe" is not an error


def test_the_error_lists_say_none_when_nothing_went_wrong():
    text = format_errors([outcome("safe", N), outcome("breaking", B)])
    assert text.count("none") >= 2


def test_a_full_report_has_the_title_counts_and_every_section(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    labels = {"customer_totals": "breaking", "product_sales": "safe", "top_customers": "breaking"}
    data = dataset(before, case(before, after, labels, case_id="a"))
    text = build_report(data, score_dataset(data), title="Baseline")
    assert text.startswith("# Baseline")
    assert "1 cases, 3 (case, model) pairs" in text
    for heading in ("## Metrics", "## Confusion matrix", "## By mutation kind", "## Errors"):
        assert heading in text


# --- on the real dataset -----------------------------------------------------

REAL = REPO / "benchmarks" / "dataset" / "jaffle_shop.json"


@pytest.fixture(scope="module")
def real_dataset():
    return load_dataset(REAL)


@pytest.fixture(scope="module")
def real_result(real_dataset):
    return score_dataset(real_dataset)


def test_the_real_dataset_scores_every_pair_without_error(real_result):
    assert real_result.skipped == []
    assert len(real_result.outcomes) == 441
    assert {o.prediction for o in real_result.outcomes} <= {B, R, N}
    assert {o.truth for o in real_result.outcomes} == {"breaking", "silent", "safe"}


def test_scoring_is_deterministic(real_dataset):
    first_ten = Dataset(
        real_dataset.meta, {}, real_dataset.baseline_project, real_dataset.cases[:10]
    )
    assert score_dataset(first_ten) == score_dataset(first_ten)


def test_no_breaking_model_of_the_real_dataset_is_missed_entirely(real_result):
    # a model that dbt could not build must at least be flagged: the tool used to predict
    # "none" for models skipped because a model upstream of them failed
    missed = [o for o in real_result.outcomes if o.truth == "breaking" and o.prediction == N]
    assert missed == []


def test_a_renamed_or_dropped_model_column_is_breaking_wherever_the_reader_uses_it(real_result):
    # including the readers that use it only in a GROUP BY or JOIN inside a CTE
    wrong = [
        o
        for o in real_result.outcomes
        if o.kind in ("model_rename_column", "model_drop_column")
        and o.truth == "breaking"
        and o.prediction != B
    ]
    assert wrong == []


def test_the_tool_never_calls_something_breaking_that_is_not(real_result):
    false_breaking = [
        o for o in real_result.outcomes if o.prediction == B and o.truth != "breaking"
    ]
    assert false_breaking == []


def test_a_renamed_or_dropped_model_column_raises_no_false_alarm(real_result):
    # the models that do not read the column used to be flagged "needs_review" because the whole
    # model counted as changed
    alarms = [
        o
        for o in real_result.outcomes
        if o.kind in ("model_rename_column", "model_drop_column")
        and o.truth == "safe"
        and o.prediction != N
    ]
    assert alarms == []


def test_every_silent_model_of_the_real_dataset_is_still_flagged(real_result):
    silent_missed = [o for o in real_result.outcomes if o.truth == "silent" and o.prediction == N]
    assert silent_missed == []

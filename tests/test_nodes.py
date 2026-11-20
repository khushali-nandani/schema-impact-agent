import pytest
from schema_impact.agent.nodes import (
    compute_impact_node,
    make_classify_risk_node,
    parse_diff_node,
    report_node,
)

from project_edits import change_type, rename_column
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment, Verdict
from schema_impact.agent.state import new_review
from schema_impact.core.changes import ColumnDropped, ColumnRenamed
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def impact(effect: str, model: str = "customer_totals", column: str | None = "total") -> Impact:
    return Impact(
        change=ColumnDropped("orders", "amount"),
        model=model,
        column=column,
        roles=frozenset({"select"}),
        effect=effect,
        path=(ColumnRef("orders", "amount"), ColumnRef(model, column)),
    )


def state_with(before, *impacts: Impact):
    state = new_review(before, before)
    state["impacts"] = list(impacts)
    return state


# --- parse_diff and compute_impact -------------------------------------------


def test_parse_diff_lists_the_changes(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    update = parse_diff_node(new_review(before, after))
    assert update == {"changes": [ColumnRenamed("orders", "customer_id", "user_id")]}


def test_compute_impact_uses_the_changes_in_the_state(before):
    state = new_review(before, before)
    state["changes"] = [ColumnDropped("orders", "status")]
    impacts = compute_impact_node(state)["impacts"]
    assert {i.model for i in impacts} == {"product_sales"}


# --- classify_risk -----------------------------------------------------------


def test_breaking_impacts_are_high_risk_without_asking_the_judge(before):
    judge = FakeJudge(Judgment(risk=LOW, explanation="", fix=""))
    update = make_classify_risk_node(judge)(state_with(before, impact(BREAKING)))
    assert judge.calls == []
    assert [v.judgment.risk for v in update["judgments"]] == [HIGH]
    assert update["needs_approval"] is True


def test_breaking_verdict_explains_itself_without_a_model(before):
    judge = FakeJudge(Judgment(risk=LOW, explanation="", fix=""))
    update = make_classify_risk_node(judge)(state_with(before, impact(BREAKING)))
    judgment = update["judgments"][0].judgment
    assert judgment.explanation
    assert judgment.fix


def test_needs_review_impacts_are_sent_to_the_judge(before):
    low = Judgment(risk=LOW, explanation="harmless", fix="nothing")
    judge = FakeJudge(low)
    target = impact(NEEDS_REVIEW)
    update = make_classify_risk_node(judge)(state_with(before, target))
    assert judge.calls == [target]
    assert update["judgments"] == [Verdict(target, low)]
    assert update["needs_approval"] is False


def test_a_high_risk_judgment_needs_approval(before):
    judge = FakeJudge(Judgment(risk=HIGH, explanation="numbers shift", fix="check"))
    update = make_classify_risk_node(judge)(state_with(before, impact(NEEDS_REVIEW)))
    assert update["needs_approval"] is True


def test_only_needs_review_impacts_reach_the_judge(before):
    judge = FakeJudge(Judgment(risk=LOW, explanation="", fix=""))
    breaking = impact(BREAKING, "customer_totals", "total")
    review = impact(NEEDS_REVIEW, "top_customers", "total")
    update = make_classify_risk_node(judge)(state_with(before, breaking, review))
    assert judge.calls == [review]
    assert [v.impact for v in update["judgments"]] == [breaking, review]


def test_no_impacts_means_no_verdicts_and_no_approval(before):
    judge = FakeJudge(Judgment(risk=HIGH, explanation="", fix=""))
    update = make_classify_risk_node(judge)(state_with(before))
    assert update == {"judgments": [], "needs_approval": False}
    assert judge.calls == []


# --- report ------------------------------------------------------------------


def test_report_for_a_change_with_no_impact_says_it_is_safe(before):
    state = state_with(before)
    state["changes"] = [ColumnDropped("customers", "country")]
    text = report_node(state)["report"]
    assert "Column customers.country was dropped" in text
    assert "safe" in text.lower()


def test_report_lists_each_impact_with_its_explanation_and_fix(before):
    target = impact(BREAKING)
    state = state_with(before, target)
    state["changes"] = [ColumnDropped("orders", "amount")]
    state["judgments"] = [
        Verdict(target, Judgment(risk=HIGH, explanation="WHY-TEXT", fix="FIX-TEXT"))
    ]
    text = report_node(state)["report"]
    assert "customer_totals.total" in text
    assert "WHY-TEXT" in text
    assert "FIX-TEXT" in text
    assert "high" in text.lower()


def test_report_summarises_counts_and_approval(before):
    breaking = impact(BREAKING, "customer_totals", "total")
    review = impact(NEEDS_REVIEW, "top_customers", "total")
    state = state_with(before, breaking, review)
    state["changes"] = [ColumnDropped("orders", "amount")]
    state["needs_approval"] = True
    state["judgments"] = [
        Verdict(breaking, Judgment(risk=HIGH, explanation="a", fix="b")),
        Verdict(review, Judgment(risk=LOW, explanation="c", fix="d")),
    ]
    text = report_node(state)["report"]
    assert "1 breaking" in text
    assert "1 needs review" in text
    assert "approval" in text.lower()


def test_type_change_end_to_end_inputs(before):
    # the node chain works on a real diff: a type change is needs_review, not breaking
    after = change_type(before, "orders", "amount", "VARCHAR")
    state = new_review(before, after)
    state.update(parse_diff_node(state))
    state.update(compute_impact_node(state))
    assert {i.effect for i in state["impacts"]} == {NEEDS_REVIEW}

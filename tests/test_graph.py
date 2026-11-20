import pytest
from schema_impact.agent.graph import build_graph

from project_edits import change_type, drop_column, rename_column
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment
from schema_impact.agent.state import new_review
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def low_judge() -> FakeJudge:
    return FakeJudge(Judgment(risk=LOW, explanation="small effect", fix="none needed"))


def test_a_breaking_change_runs_through_the_whole_graph(before):
    after = rename_column(before, "orders", "customer_id", "user_id")
    result = build_graph(low_judge()).invoke(new_review(before, after))
    assert len(result["changes"]) == 1
    assert {i.model for i in result["impacts"]} == {"customer_totals", "top_customers"}
    assert {v.judgment.risk for v in result["judgments"]} == {HIGH}
    assert result["needs_approval"] is True
    assert "customer_totals" in result["report"]


def test_a_safe_change_never_reaches_the_judge(before):
    judge = low_judge()
    after = drop_column(before, "customers", "country")
    result = build_graph(judge).invoke(new_review(before, after))
    assert result["impacts"] == []
    assert result["needs_approval"] is False
    assert judge.calls == []
    assert "safe" in result["report"].lower()


def test_needs_review_impacts_are_judged_one_by_one(before):
    judge = low_judge()
    after = change_type(before, "orders", "amount", "VARCHAR")
    result = build_graph(judge).invoke(new_review(before, after))
    assert len(judge.calls) == len(result["impacts"]) == 3
    assert result["needs_approval"] is False
    assert "small effect" in result["report"]


def test_a_high_risk_judgment_flags_the_review_for_approval(before):
    judge = FakeJudge(Judgment(risk=HIGH, explanation="totals shift", fix="recheck"))
    after = change_type(before, "orders", "amount", "VARCHAR")
    result = build_graph(judge).invoke(new_review(before, after))
    assert result["needs_approval"] is True


def test_the_graph_can_review_several_changes_at_once(before):
    after = drop_column(rename_column(before, "customers", "name", "full_name"), "orders", "status")
    result = build_graph(low_judge()).invoke(new_review(before, after))
    assert len(result["changes"]) == 2
    assert "product_sales" in {i.model for i in result["impacts"]}

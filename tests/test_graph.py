import pytest
from langgraph.types import Command

from project_edits import change_type, drop_column, rename_column
from schema_impact.agent.checkpoint import review_config
from schema_impact.agent.graph import build_graph
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment
from schema_impact.agent.state import new_review
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


@pytest.fixture
def config():
    return review_config("test-review")


def low_judge() -> FakeJudge:
    return FakeJudge(Judgment(risk=LOW, explanation="small effect", fix="none needed"))


def test_a_breaking_change_stops_for_approval_then_finishes(before, config):
    app = build_graph(low_judge())  # no checkpointer passed: a default one is used
    after = rename_column(before, "orders", "customer_id", "user_id")

    paused = app.invoke(new_review(before, after), config)
    assert len(paused["changes"]) == 1
    assert {i.model for i in paused["impacts"]} == {"customer_totals", "top_customers"}
    assert {v.judgment.risk for v in paused["judgments"]} == {HIGH}
    assert paused["needs_approval"] is True
    assert "__interrupt__" in paused

    done = app.invoke(Command(resume=True), config)
    assert "customer_totals" in done["report"]
    assert "Decision: approved" in done["report"]


def test_a_safe_change_never_reaches_the_judge(before, config):
    judge = low_judge()
    after = drop_column(before, "customers", "country")
    result = build_graph(judge).invoke(new_review(before, after), config)
    assert result["impacts"] == []
    assert result["needs_approval"] is False
    assert judge.calls == []
    assert "safe" in result["report"].lower()


def test_needs_review_impacts_are_judged_one_by_one(before, config):
    judge = low_judge()
    after = change_type(before, "orders", "amount", "VARCHAR")
    result = build_graph(judge).invoke(new_review(before, after), config)
    assert len(judge.calls) == len(result["impacts"]) == 3
    assert result["needs_approval"] is False
    assert "small effect" in result["report"]


def test_a_high_risk_judgment_flags_the_review_for_approval(before, config):
    judge = FakeJudge(Judgment(risk=HIGH, explanation="totals shift", fix="recheck"))
    after = change_type(before, "orders", "amount", "VARCHAR")
    result = build_graph(judge).invoke(new_review(before, after), config)
    assert result["needs_approval"] is True


def test_the_graph_can_review_several_changes_at_once(before, config):
    after = drop_column(rename_column(before, "customers", "name", "full_name"), "orders", "status")
    result = build_graph(low_judge()).invoke(new_review(before, after), config)
    assert len(result["changes"]) == 2
    assert "product_sales" in {i.model for i in result["impacts"]}

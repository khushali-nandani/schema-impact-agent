import pytest
from langgraph.types import Command
from schema_impact.agent.checkpoint import make_checkpointer, review_config

from project_edits import change_type, drop_column, rename_column
from schema_impact.agent.graph import build_graph
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment
from schema_impact.agent.nodes import report_node
from schema_impact.agent.state import new_review
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


@pytest.fixture
def risky(before):
    """A breaking change: renaming a join key."""
    return rename_column(before, "orders", "customer_id", "user_id")


def judge_saying(risk: str) -> FakeJudge:
    return FakeJudge(Judgment(risk=risk, explanation=f"{risk} risk", fix="check"))


def graph(judge=None, saver=None):
    return build_graph(judge or judge_saying(LOW), saver or make_checkpointer())


def approve(app, config, answer: bool):
    return app.invoke(Command(resume=answer), config)


# --- pausing ----------------------------------------------------------------


def test_a_risky_change_pauses_for_a_human(before, risky):
    app, config = graph(), review_config("pr-1")
    result = app.invoke(new_review(before, risky), config)
    assert "__interrupt__" in result
    assert app.get_state(config).next == ("human_approval",)
    assert not result["report"]  # no report until someone has decided


def test_the_pause_carries_a_summary_for_the_reviewer(before, risky):
    app, config = graph(), review_config("pr-1")
    result = app.invoke(new_review(before, risky), config)
    payload = result["__interrupt__"][0].value
    assert payload["changes"] == ["Column orders.customer_id was renamed to user_id"]
    assert "customer_totals (whole model)" in payload["high_risk"]


def test_a_safe_change_never_pauses(before):
    judge = judge_saying(HIGH)
    app, config = graph(judge), review_config("pr-1")
    result = app.invoke(new_review(before, drop_column(before, "customers", "country")), config)
    assert "__interrupt__" not in result
    assert result["approved"] is None
    assert result["report"]
    assert judge.calls == []


def test_a_low_risk_judgment_does_not_pause(before):
    app, config = graph(judge_saying(LOW)), review_config("pr-1")
    result = app.invoke(
        new_review(before, change_type(before, "orders", "amount", "VARCHAR")), config
    )
    assert "__interrupt__" not in result
    assert result["needs_approval"] is False


def test_a_high_risk_judgment_pauses(before):
    app, config = graph(judge_saying(HIGH)), review_config("pr-1")
    result = app.invoke(
        new_review(before, change_type(before, "orders", "amount", "VARCHAR")), config
    )
    assert "__interrupt__" in result


# --- resuming ---------------------------------------------------------------


def test_approving_finishes_the_review(before, risky):
    app, config = graph(), review_config("pr-1")
    app.invoke(new_review(before, risky), config)
    result = approve(app, config, True)
    assert result["approved"] is True
    assert "Decision: approved" in result["report"]
    assert app.get_state(config).next == ()


def test_rejecting_finishes_the_review_too(before, risky):
    app, config = graph(), review_config("pr-1")
    app.invoke(new_review(before, risky), config)
    result = approve(app, config, False)
    assert result["approved"] is False
    assert "Decision: rejected" in result["report"]


def test_a_new_graph_can_resume_from_the_saved_checkpoint(before, risky):
    saver = make_checkpointer()
    config = review_config("pr-1")
    graph(saver=saver).invoke(new_review(before, risky), config)

    restarted = graph(saver=saver)  # a fresh graph object, as after a restart
    result = approve(restarted, config, True)
    assert result["approved"] is True
    assert "customer_totals" in result["report"]


def test_resuming_does_not_ask_the_judge_again(before):
    judge = judge_saying(HIGH)
    app, config = graph(judge), review_config("pr-1")
    app.invoke(new_review(before, change_type(before, "orders", "amount", "VARCHAR")), config)
    asked = len(judge.calls)
    assert asked > 0
    approve(app, config, True)
    assert len(judge.calls) == asked


def test_two_reviews_do_not_disturb_each_other(before, risky):
    app = graph()
    one, two = review_config("pr-1"), review_config("pr-2")
    app.invoke(new_review(before, risky), one)
    app.invoke(new_review(before, risky), two)
    assert approve(app, one, True)["approved"] is True
    assert app.get_state(two).next == ("human_approval",)
    assert approve(app, two, False)["approved"] is False


# --- the report -------------------------------------------------------------


def test_report_has_no_decision_line_when_no_approval_was_needed(before):
    state = new_review(before, before)
    assert "Decision" not in report_node(state)["report"]

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from schema_impact.agent.judge import HIGH, LOW, Judgment, Verdict
from schema_impact.agent.state import ReviewState, new_review
from schema_impact.core.changes import ColumnDropped
from schema_impact.core.impact import BREAKING, Impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import load_project


@pytest.fixture
def project(toy_project_path):
    return load_project(toy_project_path)


def make_impact(model: str) -> Impact:
    return Impact(
        change=ColumnDropped("orders", "amount"),
        model=model,
        column="total",
        roles=frozenset({"select"}),
        effect=BREAKING,
        path=(ColumnRef("orders", "amount"), ColumnRef(model, "total")),
    )


def run(project, *updates: dict, checkpointer=None, thread="t"):
    """Run a graph whose nodes just return the given state updates, one after another."""
    graph = StateGraph(ReviewState)
    names = []
    for i, update in enumerate(updates):
        name = f"node{i}"
        graph.add_node(name, lambda state, update=update: update)
        names.append(name)
    graph.add_edge(START, names[0])
    for a, b in zip(names, names[1:], strict=False):
        graph.add_edge(a, b)
    graph.add_edge(names[-1], END)
    app = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread}}
    return app, config, app.invoke(new_review(project, project), config)


def test_new_review_starts_with_empty_results(project):
    state = new_review(project, project)
    assert state["before"] is project
    assert state["after"] is project
    assert state["changes"] == []
    assert state["impacts"] == []
    assert state["judgments"] == []
    assert state["needs_approval"] is False
    assert state["approved"] is None
    assert state["report"] == ""


def test_impacts_from_different_nodes_are_collected(project):
    one, two = make_impact("customer_totals"), make_impact("top_customers")
    _, _, result = run(project, {"impacts": [one]}, {"impacts": [two]})
    assert result["impacts"] == [one, two]


def test_judgments_from_different_nodes_are_collected(project):
    one, two = make_impact("customer_totals"), make_impact("top_customers")
    first = Verdict(one, Judgment(risk=HIGH, explanation="a", fix="b"))
    second = Verdict(two, Judgment(risk=LOW, explanation="c", fix="d"))
    _, _, result = run(project, {"judgments": [first]}, {"judgments": [second]})
    assert result["judgments"] == [first, second]


def test_plain_fields_are_overwritten_by_the_last_write(project):
    _, _, result = run(project, {"needs_approval": True}, {"needs_approval": False})
    assert result["needs_approval"] is False


def test_dataclasses_survive_the_checkpointer(project):
    impact = make_impact("customer_totals")
    verdict = Verdict(impact, Judgment(risk=HIGH, explanation="why", fix="how"))
    app, config, _ = run(
        project,
        {"impacts": [impact], "judgments": [verdict]},
        checkpointer=InMemorySaver(),
    )
    saved = app.get_state(config).values
    assert saved["impacts"] == [impact]
    assert saved["judgments"] == [verdict]
    assert saved["impacts"][0].path == impact.path
    assert saved["before"].schema == project.schema

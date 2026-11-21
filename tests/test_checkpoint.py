import logging

import pytest

from project_edits import rename_column
from schema_impact.agent.checkpoint import allowed_modules, make_checkpointer, review_config
from schema_impact.agent.graph import build_graph
from schema_impact.agent.judge import LOW, FakeJudge, Judgment
from schema_impact.agent.state import new_review
from schema_impact.core.registry import load_project


@pytest.fixture
def before(toy_project_path):
    return load_project(toy_project_path)


def test_review_config_names_the_thread():
    assert review_config("pr-42") == {"configurable": {"thread_id": "pr-42"}}


def test_every_dataclass_of_the_project_is_allowed():
    allowed = allowed_modules()
    for pair in [
        ("schema_impact.core.impact", "Impact"),
        ("schema_impact.core.lineage", "ColumnRef"),
        ("schema_impact.core.changes", "ColumnRenamed"),
        ("schema_impact.core.changes", "ColumnLogicChanged"),
        ("schema_impact.core.registry", "Project"),
        ("schema_impact.agent.judge", "Verdict"),
        ("schema_impact.agent.judge", "Judgment"),
    ]:
        assert pair in allowed


def test_a_saved_review_is_read_back_without_a_warning(before, caplog):
    caplog.set_level(logging.WARNING)
    app = build_graph(FakeJudge(Judgment(LOW, "", "")), make_checkpointer())
    config = review_config("pr-1")
    after = rename_column(before, "orders", "customer_id", "user_id")
    result = app.invoke(new_review(before, after), config)

    saved = app.get_state(config).values
    assert saved["impacts"] == result["impacts"]
    assert saved["judgments"] == result["judgments"]
    assert saved["before"].schema == before.schema
    assert "unregistered" not in caplog.text
    assert "Blocked" not in caplog.text

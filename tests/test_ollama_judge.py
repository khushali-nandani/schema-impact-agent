import json
from types import SimpleNamespace

import pytest
from schema_impact.agent.ollama_judge import OllamaJudge

from project_edits import change_type
from schema_impact.agent.checkpoint import review_config
from schema_impact.agent.graph import build_graph
from schema_impact.agent.judge import HIGH, LOW, Judge, Judgment
from schema_impact.agent.state import new_review
from schema_impact.core.changes import ColumnTypeChanged
from schema_impact.core.impact import NEEDS_REVIEW, Impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import load_project


class FakeClient:
    """Stands in for ollama.Client: replies with the given contents, one per call."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.replies.pop(0)))


def answer(risk="high", explanation="SUM over text", fix="cast it back") -> str:
    return json.dumps({"risk": risk, "explanation": explanation, "fix": fix})


@pytest.fixture
def impact():
    return Impact(
        change=ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR"),
        model="customer_totals",
        column="total",
        roles=frozenset({"select"}),
        effect=NEEDS_REVIEW,
        path=(ColumnRef("orders", "amount"), ColumnRef("customer_totals", "total")),
    )


# --- reading the model's answer ---------------------------------------------


def test_a_valid_answer_becomes_a_judgment(impact):
    judge = OllamaJudge(FakeClient(answer("high")))
    assert judge.judge(impact) == Judgment(HIGH, "SUM over text", "cast it back")


def test_a_low_risk_answer(impact):
    assert OllamaJudge(FakeClient(answer("low"))).judge(impact).risk == LOW


def test_it_satisfies_the_judge_interface(impact):
    judge: Judge = OllamaJudge(FakeClient(answer()))
    assert isinstance(judge.judge(impact), Judgment)


# --- what is sent to the model ----------------------------------------------


def test_it_asks_for_structured_json_with_no_randomness(impact):
    client = FakeClient(answer())
    OllamaJudge(client, model="some-model").judge(impact)
    call = client.calls[0]
    assert call["model"] == "some-model"
    assert call["options"]["temperature"] == 0
    assert set(call["format"]["properties"]) == {"risk", "explanation", "fix"}


def test_the_prompt_contains_the_facts_of_the_impact(impact):
    client = FakeClient(answer())
    OllamaJudge(client).judge(impact)
    system, user = client.calls[0]["messages"]
    assert system["role"] == "system"
    assert user["role"] == "user"
    text = user["content"]
    assert "Type of orders.amount changed from DECIMAL(10, 2) to VARCHAR" in text
    assert "customer_totals.total" in text
    assert "select" in text
    assert "orders.amount -> customer_totals.total" in text


def test_the_prompt_includes_the_sql_of_the_affected_model_when_known(impact):
    client = FakeClient(answer())
    OllamaJudge(client, model_sql={"customer_totals": "SELECT SUM(amount) FROM orders"}).judge(
        impact
    )
    assert "SELECT SUM(amount) FROM orders" in client.calls[0]["messages"][1]["content"]


def test_the_prompt_has_no_sql_section_when_the_sql_is_unknown(impact):
    client = FakeClient(answer())
    OllamaJudge(client).judge(impact)
    assert "SQL of" not in client.calls[0]["messages"][1]["content"]


# --- bad answers ------------------------------------------------------------


def test_an_invalid_answer_is_retried_with_a_correction(impact):
    client = FakeClient("this is not json", answer("low"))
    judgment = OllamaJudge(client).judge(impact)
    assert judgment.risk == LOW
    assert len(client.calls) == 2
    retry = client.calls[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": "this is not json"}
    assert retry[-1]["role"] == "user"
    assert "JSON" in retry[-1]["content"]


def test_an_unknown_risk_level_counts_as_invalid(impact):
    client = FakeClient(answer("medium"), answer("low"))
    assert OllamaJudge(client).judge(impact).risk == LOW
    assert len(client.calls) == 2


def test_an_empty_answer_counts_as_invalid(impact):
    client = FakeClient("", answer("high"))
    assert OllamaJudge(client).judge(impact).risk == HIGH
    assert len(client.calls) == 2


def test_after_the_retries_it_falls_back_to_high_risk(impact):
    client = FakeClient("x", "y", "z")
    judgment = OllamaJudge(client, retries=2).judge(impact)
    assert len(client.calls) == 3
    assert judgment.risk == HIGH
    assert "could not be understood" in judgment.explanation
    assert judgment.fix


def test_the_number_of_retries_can_be_set(impact):
    client = FakeClient("x")
    assert OllamaJudge(client, retries=0).judge(impact).risk == HIGH
    assert len(client.calls) == 1


def test_a_connection_problem_is_not_hidden(impact):
    class Down:
        def chat(self, **kwargs):
            raise ConnectionError("ollama is not running")

    with pytest.raises(ConnectionError):
        OllamaJudge(Down()).judge(impact)


# --- inside the graph -------------------------------------------------------


def test_the_judge_works_inside_the_review_graph(toy_project_path):
    before = load_project(toy_project_path)
    after = change_type(before, "orders", "amount", "VARCHAR")
    client = FakeClient(*[answer("low", "MODEL SAYS FINE", "nothing")] * 3)
    judge = OllamaJudge(client, model_sql={m: p.sql for m, p in before.models.items()})

    result = build_graph(judge).invoke(new_review(before, after), review_config("t"))

    assert len(client.calls) == 3
    assert result["needs_approval"] is False
    assert "MODEL SAYS FINE" in result["report"]

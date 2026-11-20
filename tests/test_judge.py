import pytest

from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judge, Judgment
from schema_impact.core.changes import ColumnTypeChanged
from schema_impact.core.impact import NEEDS_REVIEW, Impact
from schema_impact.core.lineage import ColumnRef


@pytest.fixture
def impact():
    change = ColumnTypeChanged("orders", "amount", "DECIMAL(10, 2)", "VARCHAR")
    return Impact(
        change=change,
        model="customer_totals",
        column="total",
        roles=frozenset({"select"}),
        effect=NEEDS_REVIEW,
        path=(ColumnRef("orders", "amount"), ColumnRef("customer_totals", "total")),
    )


def test_a_judgment_holds_risk_explanation_and_fix():
    judgment = Judgment(risk=HIGH, explanation="SUM over text", fix="cast back to DECIMAL")
    assert judgment.risk == HIGH
    assert judgment.explanation == "SUM over text"
    assert judgment.fix == "cast back to DECIMAL"


def test_risk_levels_are_distinct():
    assert HIGH != LOW


def test_fake_judge_returns_the_judgment_it_was_given(impact):
    judgment = Judgment(risk=LOW, explanation="harmless", fix="none")
    assert FakeJudge(judgment).judge(impact) == judgment


def test_fake_judge_records_what_it_was_asked(impact):
    judge = FakeJudge(Judgment(risk=LOW, explanation="x", fix="y"))
    judge.judge(impact)
    judge.judge(impact)
    assert judge.calls == [impact, impact]


def test_fake_judge_can_decide_per_impact(impact):
    def by_model(i: Impact) -> Judgment:
        risk = HIGH if i.model == "customer_totals" else LOW
        return Judgment(risk=risk, explanation=i.model, fix="")

    judge = FakeJudge(by_model)
    assert judge.judge(impact).risk == HIGH


def test_fake_judge_satisfies_the_judge_interface(impact):
    judge: Judge = FakeJudge(Judgment(risk=LOW, explanation="", fix=""))
    assert isinstance(judge.judge(impact), Judgment)

"""The steps of the review graph. Each node reads the state and returns a partial update.

Only `classify_risk` may involve an LLM, and only for impacts the core layer could not decide
(`needs_review`). Breaking impacts are high risk by definition and are explained without a
model.
"""

from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt

from schema_impact.agent.describe import (
    breaking_explanation,
    breaking_fix,
    describe_change,
    describe_place,
    describe_roles,
)
from schema_impact.agent.judge import HIGH, Judge, Judgment, Verdict
from schema_impact.agent.state import ReviewState
from schema_impact.core.changes import diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, compute_impact

Update = dict[str, Any]


def parse_diff_node(state: ReviewState) -> Update:
    return {"changes": diff_projects(state["before"], state["after"])}


def compute_impact_node(state: ReviewState) -> Update:
    return {"impacts": compute_impact(state["before"], state["changes"])}


def make_classify_risk_node(judge: Judge) -> Callable[[ReviewState], Update]:
    """Build the classify_risk node around a judge."""

    def classify_risk(state: ReviewState) -> Update:
        verdicts: list[Verdict] = []
        for impact in state["impacts"]:
            if impact.effect == BREAKING:
                judgment = Judgment(
                    risk=HIGH,
                    explanation=breaking_explanation(impact),
                    fix=breaking_fix(impact.change),
                )
            else:
                judgment = judge.judge(impact)
            verdicts.append(Verdict(impact, judgment))

        needs_approval = any(v.judgment.risk == HIGH for v in verdicts)
        return {"judgments": verdicts, "needs_approval": needs_approval}

    return classify_risk


def human_approval_node(state: ReviewState) -> Update:
    """Pause until a human decides. Resume with Command(resume=True) or Command(resume=False).

    When the graph resumes, this node runs again from the top, so nothing that has a side
    effect (such as asking a model) may happen before interrupt().
    """
    high_risk = [describe_place(v.impact) for v in state["judgments"] if v.judgment.risk == HIGH]
    answer = interrupt(
        {
            "question": "This change has high-risk impacts. Approve it?",
            "changes": [describe_change(change) for change in state["changes"]],
            "high_risk": high_risk,
        }
    )
    return {"approved": bool(answer)}


def report_node(state: ReviewState) -> Update:
    judgments = {v.impact: v.judgment for v in state["judgments"]}
    impacts = state["impacts"]
    lines = ["# Schema change review", "", "## Changes"]
    lines += [f"- {describe_change(change)}" for change in state["changes"]]

    lines += ["", "## Impact"]
    if not impacts:
        lines.append("No downstream impact. This change is safe.")
    for impact in impacts:
        judgment = judgments.get(impact)
        risk = f", risk {judgment.risk}" if judgment else ""
        lines.append(
            f"- {describe_place(impact)}: {impact.effect}{risk} (used in {describe_roles(impact)})"
        )
        if judgment:
            lines.append(f"  - Why: {judgment.explanation}")
            lines.append(f"  - Fix: {judgment.fix}")

    breaking = sum(1 for i in impacts if i.effect == BREAKING)
    review = sum(1 for i in impacts if i.effect == NEEDS_REVIEW)
    approval = "yes" if state["needs_approval"] else "no"
    lines += [
        "",
        "## Summary",
        f"{breaking} breaking, {review} needs review. Approval required: {approval}.",
    ]
    if state["approved"] is not None:
        lines.append(f"Decision: {'approved' if state['approved'] else 'rejected'}")
    return {"report": "\n".join(lines)}

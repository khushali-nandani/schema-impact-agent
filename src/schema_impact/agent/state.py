"""The state that flows through the review graph.

Every node reads this state and returns a partial update. Fields without a reducer are
overwritten by the last write. Fields with a reducer combine writes instead:

- impacts   are appended, so several analysis nodes can each add their own
- judgments are appended too, as Verdict(impact, judgment) pairs

Judgments are a list of pairs rather than a dict keyed by impact, because the checkpointer
cannot serialise a dataclass used as a dict key.
"""

from operator import add
from typing import Annotated, TypedDict

from schema_impact.agent.judge import Verdict
from schema_impact.core.changes import Change
from schema_impact.core.impact import Impact
from schema_impact.core.registry import Project


class ReviewState(TypedDict):
    # input
    before: Project
    after: Project

    # filled in by the nodes
    changes: list[Change]
    impacts: Annotated[list[Impact], add]
    judgments: Annotated[list[Verdict], add]
    needs_approval: bool
    approved: bool | None  # None until a human has answered
    report: str


def new_review(before: Project, after: Project) -> ReviewState:
    """The starting state for reviewing the change from `before` to `after`."""
    return ReviewState(
        before=before,
        after=after,
        changes=[],
        impacts=[],
        judgments=[],
        needs_approval=False,
        approved=None,
        report="",
    )

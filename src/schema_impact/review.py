"""A review: what changed between two projects and what it affects."""

from dataclasses import dataclass

from schema_impact.agent.describe import breaking_explanation, breaking_fix
from schema_impact.agent.judge import HIGH, Judge, Judgment
from schema_impact.core.changes import Change, diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact, compute_impact
from schema_impact.core.registry import Project


@dataclass(frozen=True)
class Item:
    """An affected place and, when there is one, what was said about it."""

    impact: Impact
    judgment: Judgment | None


@dataclass(frozen=True)
class Review:
    changes: list[Change]
    items: list[Item]

    def count(self, effect: str) -> int:
        return sum(1 for item in self.items if item.impact.effect == effect)


def review(before: Project, after: Project, judge: Judge | None = None) -> Review:
    """What changed between two projects and what it affects, with the judge's view if given."""
    changes = diff_projects(before, after)
    items = []
    for impact in compute_impact(before, changes):
        judgment = None
        if impact.effect == BREAKING:
            judgment = Judgment(HIGH, breaking_explanation(impact), breaking_fix(impact.change))
        elif judge is not None:
            judgment = judge.judge(impact)
        items.append(Item(impact, judgment))
    return Review(changes, items)


def exit_code(result: Review, fail_on: str) -> int:
    """1 if anything is at or above the threshold, else 0."""
    if fail_on == BREAKING:
        return 1 if result.count(BREAKING) else 0
    if fail_on == NEEDS_REVIEW:
        return 1 if result.items else 0
    return 0

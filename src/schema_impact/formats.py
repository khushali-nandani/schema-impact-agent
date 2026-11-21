"""How a review is written out: plain text, JSON, or Markdown for a pull request comment."""

import json
from dataclasses import asdict

from schema_impact.agent.describe import (
    breaking_fix,
    describe_change,
    describe_place,
    describe_roles,
)
from schema_impact.agent.judge import HIGH
from schema_impact.core.changes import Change
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW
from schema_impact.core.lineage import ColumnRef
from schema_impact.review import Item, Review


def format_text(result: Review) -> str:
    lines = ["# Schema change review", "", "## Changes"]
    lines += [f"- {describe_change(change)}" for change in result.changes] or ["- none"]

    lines += ["", "## Impact"]
    if not result.items:
        lines.append("No downstream impact. This change is safe.")
    for item in result.items:
        impact, judgment = item.impact, item.judgment
        risk = f", risk {judgment.risk}" if judgment else ""
        lines.append(
            f"- {describe_place(impact)}: {impact.effect}{risk} (used in {describe_roles(impact)})"
        )
        if judgment:
            lines.append(f"  - Why: {judgment.explanation}")
            lines.append(f"  - Fix: {judgment.fix}")

    lines += [
        "",
        "## Summary",
        f"{result.count(BREAKING)} breaking, {result.count(NEEDS_REVIEW)} needs review.",
    ]
    return "\n".join(lines)


def format_json(result: Review) -> str:
    data = {
        "changes": [_change_dict(change) for change in result.changes],
        "impacts": [_impact_dict(item) for item in result.items],
        "summary": {
            "breaking": result.count(BREAKING),
            "needs_review": result.count(NEEDS_REVIEW),
        },
    }
    return json.dumps(data, indent=2)


def _change_dict(change: Change) -> dict:
    return {"kind": type(change).__name__, **asdict(change)}


def _impact_dict(item: Item) -> dict:
    impact = item.impact
    return {
        "change": _change_dict(impact.change),
        "model": impact.model,
        "column": impact.column,
        "effect": impact.effect,
        "roles": sorted(impact.roles),
        "path": [_ref(ref) for ref in impact.path],
        "judgment": asdict(item.judgment) if item.judgment else None,
    }


def _ref(ref: ColumnRef) -> str:
    return ref.table if ref.column is None else f"{ref.table}.{ref.column}"


# --- Markdown -----------------------------------------------------------------------------------

MARKER = "<!-- schema-impact-agent -->"  # lets a job find its own comment and update it
MAX_ROWS = 50  # a table is cut here: a comment is at most 65536 characters
MAX_NOTES = 20
MAX_CELL = 300
_TITLES = {BREAKING: "Breaking", NEEDS_REVIEW: "Needs review"}


def format_markdown(result: Review) -> str:
    """The review as a pull request comment, the affected places grouped by model."""
    groups = {effect: _group(result, effect) for effect in (BREAKING, NEEDS_REVIEW)}
    lines = [MARKER, f"## Schema impact: {_headline(groups)}", "", "**Changes**"]
    lines += [f"- {describe_change(change)}" for change in result.changes] or ["- none"]

    if not result.items:
        lines += ["", "No downstream impact."]
    for effect, models in groups.items():
        if models:
            lines += ["", f"### {_TITLES[effect]}", "", *_table(models)]
            if effect == BREAKING:
                lines += ["", "**How to fix**", *_fixes(result)]
            else:
                lines += _notes(result)
    return "\n".join(lines) + "\n"


class _Model:
    """What is affected in one model, for one kind of effect."""

    def __init__(self) -> None:
        self.columns: list[str | None] = []
        self.roles: set[str] = set()
        self.changes: list[str] = []
        self.risks: list[str] = []


def _group(result: Review, effect: str) -> dict[str, _Model]:
    models: dict[str, _Model] = {}
    for item in result.items:
        impact = item.impact
        if impact.effect != effect:
            continue
        model = models.setdefault(impact.model, _Model())
        if impact.column not in model.columns:
            model.columns.append(impact.column)
        model.roles |= impact.roles
        reason = describe_change(impact.change)
        if reason not in model.changes:
            model.changes.append(reason)
        if item.judgment and effect == NEEDS_REVIEW:  # a breaking one is explained by template
            model.risks.append(item.judgment.risk)
    return models


def _headline(groups: dict[str, dict[str, _Model]]) -> str:
    parts = []
    breaking, review = len(groups[BREAKING]), len(groups[NEEDS_REVIEW])
    if breaking:
        parts.append(f"{breaking} {_models(breaking)} breaking")
    if review:
        parts.append(f"{review} {_models(review)} {'needs' if review == 1 else 'need'} review")
    return ", ".join(parts) or "none"


def _models(count: int) -> str:
    return "model" if count == 1 else "models"


def _table(models: dict[str, _Model]) -> list[str]:
    with_risk = any(model.risks for model in models.values())
    header = ["Model", "Columns", "Used in", "Caused by"] + (["Risk"] if with_risk else [])
    lines = [f"| {' | '.join(header)} |", f"|{'---|' * len(header)}"]
    for name, model in list(models.items())[:MAX_ROWS]:
        columns = [
            "whole model" if column is None else f"`{column}`"
            for column in sorted(model.columns, key=lambda c: c is not None)
        ]
        cells = [
            f"`{name}`",
            ", ".join(columns),
            ", ".join(sorted(model.roles)),
            "; ".join(model.changes),
        ]
        if with_risk:
            cells.append(HIGH if HIGH in model.risks else (model.risks[0] if model.risks else "-"))
        lines.append(f"| {' | '.join(_cell(cell) for cell in cells)} |")
    left = len(models) - MAX_ROWS
    if left > 0:
        lines += [
            "",
            f"_... and {left} more {_models(left)}. "
            "Run `schema-impact diff --format json` for the full list._",
        ]
    return lines


def _fixes(result: Review) -> list[str]:
    fixes: list[str] = []
    for item in result.items:
        if item.impact.effect == BREAKING:
            fix = breaking_fix(item.impact.change)
            if fix not in fixes:
                fixes.append(fix)
    return [f"- {fix}" for fix in fixes]


def _notes(result: Review) -> list[str]:
    """What a judge said about each place it was asked about, a few of them."""
    judged = [i for i in result.items if i.impact.effect == NEEDS_REVIEW and i.judgment]
    if not judged:
        return []
    lines = ["", "**Notes**"]
    for item in judged[:MAX_NOTES]:
        judgment = item.judgment
        assert judgment is not None
        text = _clip(f"{judgment.explanation} Fix: {judgment.fix}")
        lines.append(f"- `{_place(item)}` ({judgment.risk}): {text}")
    if len(judged) > MAX_NOTES:
        lines.append(f"- _... and {len(judged) - MAX_NOTES} more._")
    return lines


def _place(item: Item) -> str:
    impact = item.impact
    return impact.model if impact.column is None else f"{impact.model}.{impact.column}"


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 3] + "..."


def _cell(text: str) -> str:
    """Text for a table cell: no line breaks, and a pipe that is not a column border."""
    return _clip(text).replace("|", "\\|")

"""How a review is written out: plain text or JSON."""

import json
from dataclasses import asdict

from schema_impact.agent.describe import describe_change, describe_place, describe_roles
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

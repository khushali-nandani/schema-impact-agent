"""The command line: `schema-impact diff BEFORE AFTER`.

It reads two project folders (plain SQL with a `schema.sql`, or a dbt project's `target/`
artifacts), lists what changed and which models and columns that affects, and exits with a code
a CI job can act on:

- 0  nothing at or above `--fail-on` was found (the default threshold is `breaking`)
- 1  there is an impact at or above the threshold
- 2  the command could not run: a folder or artifact is missing, the arguments are wrong

Everything is deterministic and needs no model. `--judge ollama` asks a local model how risky
each `needs_review` impact is; a breaking impact is never put to a model.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlglot.errors import SqlglotError

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.agent.describe import (
    breaking_explanation,
    breaking_fix,
    describe_change,
    describe_place,
    describe_roles,
)
from schema_impact.agent.judge import HIGH, Judge, Judgment
from schema_impact.core.changes import Change, diff_projects
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact, compute_impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import DEFAULT_DIALECT, Project, load_project

ADAPTERS = ("sql", "dbt")
FORMATS = ("text", "json")
FAIL_ON = (BREAKING, NEEDS_REVIEW, "never")


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


def _load(path: str, adapter: str, dialect: str) -> Project:
    if adapter == "dbt":
        return load_dbt_project(path, dialect)
    return load_project(path, dialect)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema-impact",
        description="Find which models and columns a schema or SQL change affects.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    diff = commands.add_parser("diff", help="compare two versions of a project")
    diff.add_argument("before", help="the project folder as it was")
    diff.add_argument("after", help="the project folder as it is now")
    diff.add_argument("--adapter", choices=ADAPTERS, default="sql", help="default: sql")
    diff.add_argument("--dialect", default=DEFAULT_DIALECT, help=f"default: {DEFAULT_DIALECT}")
    diff.add_argument("--format", choices=FORMATS, default="text", dest="output")
    diff.add_argument(
        "--fail-on",
        choices=FAIL_ON,
        default=BREAKING,
        help="exit 1 when there is an impact at this level or worse (default: breaking)",
    )
    diff.add_argument(
        "--judge",
        choices=("none", "ollama"),
        default="none",
        help="ask a local model how risky each needs_review impact is (default: none)",
    )
    return parser


def main(argv: Sequence[str] | None = None, judge: Judge | None = None) -> int:
    """Run the command. `judge` lets a caller (or a test) pass one in instead of `--judge`."""
    args = _parser().parse_args(argv)
    try:
        before = _load(args.before, args.adapter, args.dialect)
        after = _load(args.after, args.adapter, args.dialect)
        if judge is None and args.judge == "ollama":
            from schema_impact.agent.ollama_judge import OllamaJudge

            judge = OllamaJudge(model_sql={name: m.sql for name, m in after.models.items()})
        result = review(before, after, judge)
    except (OSError, ValueError, SqlglotError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(format_json(result) if args.output == "json" else format_text(result))
    return exit_code(result, args.fail_on)

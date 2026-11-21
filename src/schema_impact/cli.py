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
import sys
from collections.abc import Sequence

from sqlglot.errors import SqlglotError

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.agent.judge import Judge
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW
from schema_impact.core.registry import DEFAULT_DIALECT, Project, load_project
from schema_impact.formats import format_json, format_text
from schema_impact.review import exit_code, review

ADAPTERS = ("sql", "dbt")
FORMATS = ("text", "json")
FAIL_ON = (BREAKING, NEEDS_REVIEW, "never")


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

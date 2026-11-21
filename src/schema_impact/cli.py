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
import tempfile
from collections.abc import Sequence
from pathlib import Path

from sqlglot.errors import SqlglotError

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.agent.judge import Judge
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW
from schema_impact.core.registry import DEFAULT_DIALECT, load_project
from schema_impact.formats import format_json, format_markdown, format_text
from schema_impact.gitrefs import export_ref
from schema_impact.review import exit_code, review

ADAPTERS = ("sql", "dbt")
FORMATS = ("text", "json", "markdown")
FAIL_ON = (BREAKING, NEEDS_REVIEW, "never")


def _load(path: str | Path, adapter: str, dialect: str, catalog_from: str | Path | None = None):
    if adapter == "dbt":
        return load_dbt_project(path, dialect, catalog_from)
    return load_project(path, dialect)


def _dbt_catalog_from(before: str, after: str) -> str | None:
    """The base's catalog, when the head has none (`dbt compile` writes no catalog)."""
    return None if (Path(after) / "target" / "catalog.json").is_file() else before


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema-impact",
        description="Find which models and columns a schema or SQL change affects.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    diff = commands.add_parser("diff", help="compare two versions of a project")
    diff.add_argument("before", nargs="?", help="the project folder as it was")
    diff.add_argument("after", nargs="?", help="the project folder as it is now")
    diff.add_argument("--base", help="instead of two folders: the git ref to compare from")
    diff.add_argument("--head", default="HEAD", help="the git ref to compare to (default: HEAD)")
    diff.add_argument("--repo", default=".", help="the git repository (default: this folder)")
    diff.add_argument("--project-dir", help="the project's folder inside the repository")
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
    parser = _parser()
    args = parser.parse_args(argv)
    if args.base and (args.before or args.after):
        parser.error("give two folders or --base, not both")
    if not args.base and not (args.before and args.after):
        parser.error("give two folders (BEFORE AFTER), or --base REF")
    if args.base and args.adapter == "dbt":
        print(
            "error: the artifacts of a dbt project (target/) are not in git; "
            "pass the BEFORE and AFTER folder that hold them",
            file=sys.stderr,
        )
        return 2
    try:
        if args.base:
            with tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch)
                before_dir = export_ref(args.repo, args.base, args.project_dir, root / "before")
                after_dir = export_ref(args.repo, args.head, args.project_dir, root / "after")
                before = _load(before_dir, args.adapter, args.dialect)
                after = _load(after_dir, args.adapter, args.dialect)
        else:
            catalog_from = _dbt_catalog_from(args.before, args.after)
            before = _load(args.before, args.adapter, args.dialect)
            after = _load(args.after, args.adapter, args.dialect, catalog_from)
        if judge is None and args.judge == "ollama":
            from schema_impact.agent.ollama_judge import OllamaJudge

            judge = OllamaJudge(model_sql={name: m.sql for name, m in after.models.items()})
        result = review(before, after, judge)
    except (OSError, ValueError, SqlglotError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    text = {"json": format_json, "markdown": format_markdown}.get(args.output, format_text)(result)
    print(text, end="" if text.endswith("\n") else "\n")
    return exit_code(result, args.fail_on)

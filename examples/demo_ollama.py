"""Review a schema change on the toy project with a real local model.

Needs Ollama running and the model pulled first:

    ollama pull qwen2.5-coder:7b
    uv run python examples/demo_ollama.py

The change: orders.amount goes from DECIMAL(10, 2) to INTEGER. Nothing fails, but customer_totals
sums that column, so the totals may silently change. That is the kind of impact the model judges.
(Turning it into VARCHAR would not do: summing text fails, which the tool reports as breaking.)
This script is a demo and is not part of the tests or CI.
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

import ollama
from langgraph.types import Command

from schema_impact.agent.checkpoint import review_config
from schema_impact.agent.graph import build_graph
from schema_impact.agent.ollama_judge import DEFAULT_MODEL, OllamaJudge
from schema_impact.agent.state import new_review
from schema_impact.core.registry import load_project

TOY_PROJECT = Path(__file__).resolve().parent / "toy_project"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    client = ollama.Client()
    try:
        installed = {m.model for m in client.list().models}
    except Exception as error:  # connection refused, server not running, ...
        print(f"Could not reach Ollama ({error}). Is it running?")
        return 1
    if args.model not in installed:
        print(f"Model {args.model} is not installed. Run: ollama pull {args.model}")
        return 1

    before = load_project(TOY_PROJECT)
    with tempfile.TemporaryDirectory() as tmp:
        changed = Path(tmp) / "toy_project"
        shutil.copytree(TOY_PROJECT, changed)
        schema = changed / "schema.sql"
        # change the type of the "amount" column only (products.price is also a DECIMAL)
        text, replaced = re.subn(r"(\bamount\s+)DECIMAL\(10, 2\)", r"\1INTEGER", schema.read_text())
        assert replaced == 1, "expected to change exactly one column"
        schema.write_text(text)
        after = load_project(changed)

    judge = OllamaJudge(
        client, model=args.model, model_sql={n: m.sql for n, m in before.models.items()}
    )
    app = build_graph(judge)
    config = review_config("demo")

    print(f"Reviewing with {args.model} ...\n")
    result = app.invoke(new_review(before, after), config)

    if "__interrupt__" in result:
        question = result["__interrupt__"][0].value
        print(question["question"])
        for place in question["high_risk"]:
            print(f"  - {place}")
        answer = input("Approve? [y/N] ").strip().lower() == "y"
        result = app.invoke(Command(resume=answer), config)

    print()
    print(result["report"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

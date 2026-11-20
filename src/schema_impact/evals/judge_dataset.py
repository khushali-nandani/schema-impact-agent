"""Ask a local LLM judge about the needs_review impacts of the benchmark and keep its answers.

    ollama pull qwen2.5-coder:7b
    uv run python -m schema_impact.evals.judge_dataset

About ten minutes for the jaffle_shop dataset. Safe to interrupt and run again: the answers are
saved after every impact and the ones already there are not asked again.
"""

import argparse
from datetime import date
from pathlib import Path

from schema_impact.agent.ollama_judge import DEFAULT_MODEL, OllamaJudge
from schema_impact.evals.dataset import load_dataset
from schema_impact.evals.llm_eval import (
    collect_review_items,
    judge_items,
    load_judgments,
    save_judgments,
)

REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect an LLM judge's answers on the benchmark.")
    parser.add_argument(
        "--dataset", type=Path, default=REPO / "benchmarks" / "dataset" / "jaffle_shop.json"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "benchmarks" / "results" / f"{date.today().isoformat()}-llm-judgments.json",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--limit", type=int, default=None, help="ask about the first N impacts only"
    )
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    items = collect_review_items(dataset)[: args.limit]
    already = load_judgments(args.out)[1] if args.out.exists() else []
    print(f"{len(items)} impacts to judge, {len(already)} already answered in {args.out}")

    judge = OllamaJudge(
        model=args.model,
        model_sql={name: model.sql for name, model in dataset.baseline_project.models.items()},
    )
    answers = judge_items(
        items,
        judge,
        already=already,
        on_item=lambda so_far: save_judgments(args.out, so_far, args.model),
    )
    save_judgments(args.out, answers, args.model)
    print(f"\n{len(answers)} answers saved to {args.out}")


if __name__ == "__main__":
    main()

"""Build the jaffle_shop benchmark dataset by running dbt on every mutation.

    uv sync --group bench
    uv run python -m schema_impact.evals.build_dataset

Takes about ten minutes. Safe to interrupt and run again: finished cases are kept.
"""

import argparse
from pathlib import Path

from schema_impact.evals.dataset import build_dataset
from schema_impact.evals.jaffle_recipes import JAFFLE_RECIPES

REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the benchmark dataset with dbt.")
    parser.add_argument("--project", type=Path, default=REPO / "benchmarks" / "jaffle_shop")
    parser.add_argument(
        "--out", type=Path, default=REPO / "benchmarks" / "dataset" / "jaffle_shop.json"
    )
    parser.add_argument("--limit", type=int, default=None, help="build only the first N cases")
    args = parser.parse_args()

    dataset = build_dataset(args.project, args.out, recipes=JAFFLE_RECIPES, limit=args.limit)

    errors = [case for case in dataset.cases if case.error]
    print(f"\n{len(dataset.cases)} cases written to {args.out}, {len(errors)} with an error")
    for case in errors:
        print(f"  {case.mutation.id}: {case.error.splitlines()[0]}")


if __name__ == "__main__":
    main()

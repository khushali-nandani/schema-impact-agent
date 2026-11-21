"""Build a benchmark dataset by running dbt on every mutation.

    uv sync --group bench
    uv run --no-sync python -m schema_impact.evals.build_dataset                 # jaffle_shop
    uv run --no-sync python -m schema_impact.evals.build_dataset --benchmark olist

jaffle_shop takes about ten minutes. Olist needs its CSV files in benchmarks/olist/data and
`dbt deps` run there (see benchmarks/olist/DATA.md), and takes about an hour: leave the machine
awake. Safe to interrupt and run again: finished cases are kept.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from schema_impact.evals.dataset import build_dataset
from schema_impact.evals.jaffle_recipes import JAFFLE_RECIPES
from schema_impact.evals.mutations import Recipe
from schema_impact.evals.olist_recipes import OLIST_RECIPES

REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Benchmark:
    project: Path
    out: Path
    recipes: list[Recipe]


BENCHMARKS = {
    "jaffle_shop": Benchmark(
        REPO / "benchmarks" / "jaffle_shop",
        REPO / "benchmarks" / "dataset" / "jaffle_shop.json",
        JAFFLE_RECIPES,
    ),
    "olist": Benchmark(
        REPO / "benchmarks" / "olist",
        REPO / "benchmarks" / "dataset" / "olist.json",
        OLIST_RECIPES,
    ),
}
DEFAULT_BENCHMARK = "jaffle_shop"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a benchmark dataset with dbt.")
    parser.add_argument("--benchmark", choices=sorted(BENCHMARKS), default=DEFAULT_BENCHMARK)
    parser.add_argument("--project", type=Path, default=None, help="default: the benchmark's own")
    parser.add_argument("--out", type=Path, default=None, help="default: the benchmark's own")
    parser.add_argument("--limit", type=int, default=None, help="build only the first N cases")
    args = parser.parse_args()
    benchmark = BENCHMARKS[args.benchmark]

    out = args.out or benchmark.out
    dataset = build_dataset(
        args.project or benchmark.project, out, recipes=benchmark.recipes, limit=args.limit
    )

    errors = [case for case in dataset.cases if case.error]
    print(f"\n{len(dataset.cases)} cases written to {out}, {len(errors)} with an error")
    for case in errors:
        print(f"  {case.mutation.id}: {case.error.splitlines()[0]}")


if __name__ == "__main__":
    main()

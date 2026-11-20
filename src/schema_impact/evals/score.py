"""Score the tool against the ground-truth dataset and print (or save) a report.

    uv run python -m schema_impact.evals.score --title "My run" --out benchmarks/results/run.md

Needs no dbt: it reads the committed dataset.
"""

import argparse
from pathlib import Path

from schema_impact.evals.dataset import load_dataset
from schema_impact.evals.scorer import build_report, score_dataset

REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the tool on the benchmark dataset.")
    parser.add_argument(
        "--dataset", type=Path, default=REPO / "benchmarks" / "dataset" / "jaffle_shop.json"
    )
    parser.add_argument("--title", default="Benchmark results")
    parser.add_argument("--out", type=Path, default=None, help="also write the report here")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    report = build_report(dataset, score_dataset(dataset), args.title)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)


if __name__ == "__main__":
    main()

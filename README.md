# schema-impact-agent

[![CI](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml)

Given a SQL/dbt change (rename, drop, type change, logic change), find every downstream model and column it affects, classify it as breaking, silent, or safe, and pause for human approval on risky changes.

## Why

- **Breaking** changes error out, but only after the pipeline runs.
- **Silent** changes are worse: nothing fails, the numbers are just wrong.
- Existing tools work at model level or miss columns used only in `JOIN` and `WHERE`, which is where silent breakage hides.

See [docs/architecture.md](docs/architecture.md) for the full problem statement and design, and [docs/DECISIONS.md](docs/DECISIONS.md) for why things are built the way they are.

## Status

Early development. L0 (column lineage), L1 (change detection and impact), L2 (LangGraph agent with human approval) and L3 (benchmark) are done; adapters and packaging come next.

| Level | Scope | Status |
|---|---|---|
| L0 | Column-level lineage from SQL (incl. JOIN/WHERE/GROUP BY/HAVING/ORDER BY) | Done |
| L1 | Change detection and impact analysis | Done |
| L2 | LangGraph agent with human approval | Done |
| L3 | Benchmark on jaffle_shop with mutation testing | Done |
| L4 | Adapters: SQL folder, dbt manifest, dialects | Planned |
| L5 | CLI and GitHub Action | Planned |

## Results

Scored against what dbt really does on [jaffle_shop](benchmarks/jaffle_shop/NOTICE.md): 103 mutations (renamed, dropped or retyped columns, changed logic), 441 (case, model) pairs labelled breaking, silent or safe by running dbt. No LLM involved.

| | Baseline | Now |
|---|---|---|
| Affected models: F1 | 88.8% | **97.1%** |
| Breaking models found | 49.5% (46/93) | **96.8%** (90/93) |
| Models called breaking that are not | 0 | **0** |
| Silent models flagged | 24/24 | **24/24** |
| False alarms | 12 | **7** |

Caveats: one small project (5 models), the fixes that took the tool from the baseline to "now" were chosen by looking at this dataset's errors (so "now" is not a held-out estimate), and "silent" rests on 24 examples. A local 7B LLM judge on the cases left for review judged everything high and changed nothing. Details, every intermediate step and the limits: [docs/architecture.md](docs/architecture.md#benchmark-l3) and [benchmarks/results](benchmarks/results/README.md).

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync                       # create .venv and install dependencies
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format --check .  # format check
```

### Try the agent with a local model

```bash
ollama pull qwen2.5-coder:7b
uv run python examples/demo_ollama.py   # changes orders.amount to VARCHAR on the toy project
```

The tests never need a model: they use a fake judge.

### Run the benchmark

```bash
uv sync --group bench                                   # adds dbt-core, dbt-duckdb, duckdb
uv run python -m schema_impact.evals.score              # score the tool on the committed dataset
uv run pytest -m bench                                  # tests that run dbt (slow)
uv run python -m schema_impact.evals.build_dataset      # rebuild the ground truth, ~10 min
uv run python -m schema_impact.evals.judge_dataset      # ask a local LLM judge, ~10 min
```

Plain `uv sync` removes the bench packages again. Scoring needs no dbt: it reads the committed dataset.


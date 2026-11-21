# schema-impact-agent

[![CI](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml)

Given a SQL/dbt change (rename, drop, type change, logic change), find every downstream model and column it affects, classify it as breaking, silent, or safe, and pause for human approval on risky changes.

## Why

- **Breaking** changes error out, but only after the pipeline runs.
- **Silent** changes are worse: nothing fails, the numbers are just wrong.
- Existing tools work at model level or miss columns used only in `JOIN` and `WHERE`, which is where silent breakage hides.

See [docs/architecture.md](docs/architecture.md) for the full problem statement and design, and [docs/DECISIONS.md](docs/DECISIONS.md) for why things are built the way they are.

## Status

Early development. L0 (column lineage), L1 (change detection and impact), L2 (LangGraph agent with human approval) and L3 (benchmark) are done. L4 is in progress: the tool was scored once, untouched, on a second project (see Results), and one fix has been made from what that showed; the numbers after it are labelled as tuned. The CLI and packaging come after.

| Level | Scope | Status |
|---|---|---|
| L0 | Column-level lineage from SQL (incl. JOIN/WHERE/GROUP BY/HAVING/ORDER BY) | Done |
| L1 | Change detection and impact analysis | Done |
| L2 | LangGraph agent with human approval | Done |
| L3 | Benchmark on jaffle_shop with mutation testing | Done |
| L4 | Adapters (dbt manifest, CSV sources) and a second, held-out benchmark project | In progress |
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

### Held-out check: Olist

The numbers above are on the project the fixes were made on. To see how much of that carries over, the tool was run, once and unchanged, on a second dbt project it had never seen: [Olist](benchmarks/olist/NOTICE.md), 22 models, vendored unmodified. 420 mutations, 8948 (case, model) pairs labelled by running dbt: 1358 breaking, 78 silent, 7512 safe. `core/`, `agent/` and the scorer are the code of v0.4.0; only the dbt adapter and the benchmark harness were extended so that Olist could be loaded and built. The report was committed before it was read.

| | jaffle_shop (v0.4.0, after the L3 fixes) | Olist (v0.4.0, held-out) |
|---|---|---|
| Affected models: F1 | 97.1% | **97.4%** |
| Breaking models found | 96.8% (90/93) | **96.9%** (1316/1358) |
| Models called breaking that are not | 0 | **0** |
| Silent models flagged | 24/24 | **78/78** |
| False alarms | 7 | 59 |

The overall figure is flattered by 268 of the 420 cases, which rename or drop an output column of a model and are easy. By kind, the weak spot was a column whose **type** changes (fixed afterwards, see below): 62 of 78 affected pairs are found (79.5%), 16 models that dbt fails are missed outright (predicted `none`), and no breaking pair is called breaking (a type change is reported as "needs review" by design, but dbt fails 42 of those pairs). Most of the 59 false alarms are logic edits that turned out not to change any output. Per-kind table, every miss and every false alarm: [benchmarks/results/2026-11-21-olist-held-out.md](benchmarks/results/2026-11-21-olist-held-out.md).

Caveats: two projects are not a sample of dbt projects, silent rests on 78 pairs (42 from 24 hand-written edits), and any fix after this run is made by reading these errors, so the numbers after it are tuned and labelled so. The Olist data (CC BY-NC-SA 4.0) is not in the repository: [benchmarks/olist/DATA.md](benchmarks/olist/DATA.md).

### After the first fix (tuned, not held-out)

The 16 misses were all one thing: a column that becomes text. A model that only selects it is fine, but `SUM`, `AVG`, `date_diff` or arithmetic on text fail on DuckDB, so the model cannot be built and dbt skips everything that reads it. The tool now follows the text type along columns that are passed on as they are, makes the model that sums or divides it breaking, and takes its readers down with it; a cast on the way stops it. It applies on DuckDB only, where it was measured.

| | Olist held-out | Olist after the fix | jaffle_shop v0.4.0 | jaffle_shop after the fix |
|---|---|---|---|---|
| Affected models: F1 | 97.4% | 98.0% | 97.1% | 97.1% |
| Breaking models found | 96.9% (1316/1358) | **100%** (1358/1358) | 96.8% (90/93) | **100%** (93/93) |
| Models called breaking that are not | 0 | **0** | 0 | **0** |
| Missed (predicted none) | 16 | 0 | 0 | 0 |
| False alarms | 59 | 59 | 7 | 7 |

The fix was found by reading the Olist errors, so the Olist column is not a held-out estimate. What it does show is that the rule did not cost precision (no model is called breaking that is not) and changed no other kind of case. jaffle_shop improved too, but the rule's arithmetic part rests on one jaffle case, and both projects only retype to `VARCHAR`, so any other type change still just needs review. The 59 false alarms are untouched: most come from edits that change one output column or only refactor, where the tool reports the whole model as changed. Reports: [benchmarks/results](benchmarks/results/README.md).

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
uv run python -m schema_impact.evals.score --dataset benchmarks/dataset/olist.json   # held-out project
# rebuilding Olist (~1 h) needs its CSV files and `dbt deps`: see benchmarks/olist/DATA.md, then
uv run --no-sync python -m schema_impact.evals.build_dataset --benchmark olist
uv run python -m schema_impact.evals.judge_dataset      # ask a local LLM judge, ~10 min
```

Plain `uv sync` removes the bench packages again. Scoring needs no dbt: it reads the committed dataset.


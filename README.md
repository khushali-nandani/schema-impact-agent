# schema-impact-agent

[![CI](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/khushali-nandani/schema-impact-agent/actions/workflows/ci.yml)

Given a SQL/dbt change (rename, drop, type change, logic change), find every downstream model and column it affects, classify it as breaking, silent, or safe, and pause for human approval on risky changes.

## Why

- **Breaking** changes error out, but only after the pipeline runs.
- **Silent** changes are worse: nothing fails, the numbers are just wrong.
- Existing tools work at model level or miss columns used only in `JOIN` and `WHERE`, which is where silent breakage hides.

See [docs/architecture.md](docs/architecture.md) for the full problem statement and design, and [docs/DECISIONS.md](docs/DECISIONS.md) for why things are built the way they are.

## Status

Early development. L0 (column lineage) and L1 (change detection and impact) are done; the LangGraph agent starts in L2.

| Level | Scope | Status |
|---|---|---|
| L0 | Column-level lineage from SQL (incl. JOIN/WHERE/GROUP BY/HAVING/ORDER BY) | Done |
| L1 | Change detection and impact analysis | Done |
| L2 | LangGraph agent with human approval | Planned |
| L3 | Benchmark on jaffle_shop with mutation testing | Planned |
| L4 | Adapters: SQL folder, dbt manifest, dialects | Planned |
| L5 | CLI and GitHub Action | Planned |

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync                       # create .venv and install dependencies
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format --check .  # format check
```

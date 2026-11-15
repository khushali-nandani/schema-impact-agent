# Decisions

A short log of the larger design decisions and why they were made. Newest entries go at the bottom.

## 1. Use sqlglot for SQL parsing and lineage

sqlglot parses many SQL dialects into an AST and ships column-lineage utilities, so we do not write a SQL parser ourselves. It is pure Python, which keeps installation simple.

## 2. Keep the core deterministic and LLM-free

Dependency facts (what reads which column) must be reproducible and testable without an API key. The LLM is limited to judgment tasks: whether a change alters meaning, explaining impact, suggesting fixes. This also limits hallucination to the part that needs interpretation.

## 3. Three layers: core, agent, adapters

Separating parsing logic, the LangGraph agent and input adapters lets the tool work on any SQL project, not only dbt. Dependencies point one way: `agent` and `adapters` import `core`, never the reverse.

## 4. Track JOIN, WHERE and GROUP BY dependencies, not only SELECT

Columns used only in joins and filters are where silent breakage hides, and projection-only lineage misses them. Capturing these edges is the main differentiator of this project.

## 5. v1 handles SELECT-based models only

sqlglot lineage works on SELECT queries and needs a schema. `MERGE` and incremental models are out of scope for v1 to keep L0 to L2 achievable.

## 6. Add LangGraph and LLM dependencies only at L2

L0 and L1 do not need them. Adding them later keeps early installs small and makes the "LLM-free core" rule visible in `pyproject.toml`.

## 7. Use uv for environments and dependency locking

`uv.lock` gives reproducible installs on every machine and in CI, and uv is fast. CI uses `astral-sh/setup-uv`.

## 8. src layout with hatchling

Code lives in `src/schema_impact`, so tests always import the installed package rather than the repository folder. This avoids "works locally, fails in CI" import bugs.

## 9. Ruff for linting and formatting, pytest for tests

One fast tool covers lint and format. Rule set is deliberately small (`E`, `F`, `I`, `UP`, `B`) to avoid noise early on.

## 10. Ground truth from mutation testing

Benchmark labels (breaking, silent, safe) come from running `dbt build` on mutated projects and diffing output tables, not from hand-labelling. This makes the benchmark large, objective and repeatable.

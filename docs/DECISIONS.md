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

## 11. Clause dependencies point at the whole model (`column=None`)

A column used only in JOIN, WHERE, GROUP BY, HAVING or ORDER BY does not feed any output column, but changing it still changes the model. Those edges therefore target `ColumnRef(model, None)`. `None` cannot collide with a real column name, unlike a placeholder such as `"*"`.

## 12. Qualify the query first and collect clause edges per SELECT scope

`qualify()` expands `SELECT *` and puts a table alias on every column. Clause edges are then collected per SELECT scope (main query, CTEs, subqueries), so the same alias can mean different tables in different SELECTs. A first version looked only at the top-level SELECT and silently missed filters inside CTEs, which is exactly the silent-breakage case this project exists to catch.

## 13. Models are added to the schema as tables, in dependency order

To resolve a model that reads another model, models are processed in topological order (`graphlib`) and each model's output columns are added to a working copy of the schema. Lineage code stays the same because `qualify()` still gets one schema dict, and `Project.schema` keeps meaning "source tables". Output columns get the type `UNKNOWN`, since lineage only needs names. Cycles raise `CircularDependencyError` instead of crashing.

## 14. Rename detection is a heuristic; added columns are ignored

A diff cannot tell a rename from a drop plus an add. A column is treated as renamed when one column disappears and one appears in the same table at the same position with the same type; otherwise it is a drop. Added columns are not changes because they cannot break downstream models. Model SQL is compared after normalising, so formatting and comments do not count.

## 15. Impact effect is deterministic and does not weaken along a chain

Rename, drop and dropped table are `breaking`; type change and changed logic are `needs_review`. No LLM is needed for the first group: a missing column always fails. Every model further down a chain gets the same effect as the original change. `needs_review` is deliberately left for L2 to judge. Impact is computed on the "before" project, because its SQL is what refers to the old columns.

## 16. The judge is an interface and is passed to the graph, not kept in the state

`Judge` is a one-method protocol (`judge(impact) -> Judgment`). The graph receives it in `build_graph(judge)` because it is a live object (a model connection) that has no place in a checkpoint. Tests use a `FakeJudge`, so no model is needed in CI; `OllamaJudge` is the real one, and another model can be swapped in without touching the graph.

## 17. Breaking impacts never go to the LLM

A renamed or dropped column always fails, so the impact is high risk by definition and is explained from templates. Only `needs_review` impacts (type change, changed logic) are sent to the judge. The LLM is used only where the deterministic layer cannot decide.

## 18. A model answer that cannot be understood falls back to high risk; connection errors are raised

`OllamaJudge` asks for JSON, validates it, retries with a correction, and if it still fails returns `high` risk so a person looks at it. Guessing `low` would hide problems. If Ollama is unreachable the error is raised instead: calling that "high risk" would be a lie.

## 19. State holds dataclasses; the checkpointer gets an allow-list

Keeping `Impact`, `Verdict` and `Project` in the state worked, with three consequences found by tests: a dataclass cannot be a dict key (so judgments are a list of `Verdict` pairs), a tuple comes back from a checkpoint as a list (so `Impact.path` is normalised in `__post_init__`), and LangGraph will block unregistered types in future versions (so the saver gets an allow-list built by scanning the modules).

## 20. Approval answers are a boolean; paused reviews live in memory

`Command(resume=True/False)` approves or rejects. A note from the reviewer can be added later (L5). The default checkpointer is in memory, so a paused review survives a restart of the graph object but not of the process; a persistent store (SQLite) comes with the CLI and GitHub Action in L5, where review and resume run in different processes.

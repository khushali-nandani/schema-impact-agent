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

## 21. Ground truth comes from running dbt, on the project's own seed data, without `dbt test`

Hand-labelling would be small and subjective. A model that fails or is skipped is `breaking`; one that builds with a different output (rows, or columns by name, type and order, compared in DuckDB) is `silent`; otherwise `safe`. `dbt test` is left out because a failing test is a different question from a pipeline that breaks or numbers that change. The cost: "same output" is only as strong as the seed data, so a change that leaves it unchanged is labelled safe.

## 22. A mutation is a JSON spec applied to a copy, and every edit asserts its text exists

Specs can be saved in the dataset and replayed. A recipe whose text is not there raises instead of being skipped, so the benchmark cannot silently shrink. The mutated model itself is not scored: its output changes by construction.

## 23. The dataset is committed; scoring never runs dbt

Building the ground truth takes about ten minutes and needs dbt, so it is saved with the project after each mutation (compiled SQL and seed columns). dbt, dbt-duckdb and duckdb live in a `bench` dependency group, not installed by default, so CI stays light; tests that run dbt carry a `bench` marker and are deselected by default. Plain `uv sync` removes the bench packages.

## 24. A breaking impact takes the whole model down

If a model's query fails, dbt skips every model that reads it, whichever of its columns it uses. Following only the columns readers use missed 14 breaking models. A change that only needs review still follows the columns, since there the queries still run.

## 25. Compare a model's own output columns, and report "model changed" only when more than the output shape changed

A renamed or dropped output column of an edited model is a column change, so readers of that column are breaking. If the rest of the query is identical (after `qualify()`) and the remaining columns are computed the same way, the edit is not also a model change, so readers of other columns are not flagged. Whenever this cannot be shown (the new SQL no longer resolves, a `*` cannot be expanded, ORDER BY changes meaning) the model is reported as changed: a false alarm costs less than a silent miss.

## 26. Trace clause columns through CTEs

dbt models are chains of CTEs, so most JOIN / WHERE / GROUP BY columns belong to a CTE. They are followed to the table columns they are made from. This was found only by an interaction: making the diff precise (decision 25) removed a coarse model-level flag that had been hiding the missing edges.

## 27. The LLM judge is not tuned on the benchmark

The 7B model judged every impact high, so it added nothing. Rewriting the prompt until it scores well on the same 34 pairs it is scored on would measure the prompt's fit to the benchmark, not the judge, so it was not done. The result is reported as it is, with its limits (one model, one prompt, 7 safe pairs).

## 28. The reported numbers are not a held-out estimate

The fixes of decisions 24 to 26 were found by reading the errors of this very dataset. The results table says so, and it keeps every intermediate row so the effect of each fix can be seen.


## 29. A second, untouched project is the held-out test, scored once before any fix

The jaffle_shop numbers were tuned on their own errors (decision 28). To measure what carries over, the tool was run on Olist, a project of a different shape, vendored unmodified (MIT; its data, CC BY-NC-SA, stays out of the repository). The tool's `core/`, `agent/` and scorer were not touched between v0.4.0 and the first score, the report was committed before it was read, and every fix after it is reported as tuned on Olist.

## 30. A dbt CSV source is named after its source and its columns are read from the file

With dbt-duckdb, `external_location` makes a source compile to a table named by its file path (or a `read_csv(...)` call), and `catalog.json` lists no columns for it. The adapter names it `<source>__<table>`, matches the table expression whatever its alias, and reads the column names and types with DuckDB. `duckdb` is imported only when such a source exists.

## 31. Ground truth must be reproducible, so DuckDB runs on one thread

Building the unmutated Olist project three times gave three different `customer_rfm` tables: an `ntile` over tied values gets its rows in a different order on each run with several threads. That made 258 pairs "silent" for no reason, about three quarters of all silent labels in the first build. That dataset was discarded; builds now run DuckDB on one thread in the copy (the project's own profile is untouched), and a test builds the project twice and compares every model. jaffle_shop was checked the same way and is deterministic.

## 32. Models are compared through a snapshot taken inside the project folder

A model that reads a CSV is a view holding the relative path `data/x.csv`, looked up when the view is queried, in the folder the process runs in. Attaching two databases from one place would make both views read the same file, or none, and a changed CSV would never be seen. After `dbt run` the rows of every model that built are copied into a plain database while inside the project folder, and models are compared from the two snapshots.

## 33. A mutation never edits an original data file

The 120 MB of CSV files are linked, one by one, into each case copy instead of copied, so editing must replace the link with a new file; writing through it would change the original for every later case. Renaming copies every byte after the header; dropping a column writes every other field back exactly as it was, because Olist mixes quoted and unquoted fields on purpose (a zip code `"01037"` is quoted to keep its zero). A rewrite that drops nothing gives the same bytes for all nine files, and a build stops if an original changes. A column type is changed with `struct_pack(...)` in a `read_csv` location, since dbt renders the location as Jinja and then formats it, and both would take `{...}` for something to fill in.

## 34. The held-out result is published before the fixes

Writing the first Olist result into the README and the architecture notes before any change makes it checkable that the later, tuned numbers are not passed off as held-out ones. The result is mixed on purpose: overall F1 matches jaffle_shop, but type changes (79.5% of affected pairs found, never called breaking) are a real weak spot that the overall number hides.

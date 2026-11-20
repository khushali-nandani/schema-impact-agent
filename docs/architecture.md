# Architecture

> Status: design document. Implementation starts at L0 (see [Roadmap](#roadmap)).

## Problem

Data pipelines are chains: `raw -> staging -> marts -> consumers`. A *consumer* is any SQL that reads a table or column: other tables, views, saved queries, dashboards. One upstream change can break many of them, and often nobody finds out until the pipeline runs, or until someone notices a wrong number.

Mini example:

```sql
-- customers(id, name)
-- orders(id, customer_id, amount)

CREATE VIEW customer_totals AS
SELECT c.name, SUM(o.amount) AS total
FROM orders o JOIN customers c ON o.customer_id = c.id
GROUP BY c.name;
```

| Change to `orders` | Result | Where it hurts |
|---|---|---|
| rename `customer_id` to `user_id` | Breaking (column not found) | `JOIN ... ON o.customer_id` |
| drop `amount` | Breaking | `SUM(o.amount)` |
| `amount` type DECIMAL to VARCHAR | Database dependent: error or odd result | `SUM(o.amount)` |
| `amount` meaning changes (rupees to paise) | Silent: nothing fails, totals are 100x | `SUM(o.amount)` |
| new upstream filter `WHERE status != 'returned'` | Silent: numbers drop | whole `customer_totals` |

## Goal

Given a schema or logic change, the agent:

1. finds every downstream model and column it affects,
2. classifies each impact as **breaking**, **silent**, or **safe**,
3. pauses for human approval on risky changes,
4. reports what it found, with an explanation and a suggested fix.

### Classification

| Class | Definition | Why it matters |
|---|---|---|
| **Breaking** | The downstream query fails (missing column, invalid type). | Loud, but only after the pipeline runs. We want to catch it before. |
| **Silent** | The query still runs but its output changes (new filter, changed meaning, type coercion). | Dangerous: wrong numbers keep getting used. |
| **Safe** | No downstream effect. | Should not generate noise. |

A column is used not only in `SELECT` but also in `JOIN ON`, `WHERE`, `GROUP BY` and window partitions. The agent must trace all of these.

## Why existing tools fall short

- dbt `state:modified+` selects at model level only. It over-reports, gives no column-level detail and no severity.
- dbt's column-level lineage lives in its Catalog, which requires a paid plan.
- Column lineage based on `SELECT` projections misses columns used only in `JOIN` or `WHERE`. That is exactly where silent breakage hides, so this project captures join and filter dependencies as first-class edges.

## Pipeline

```
PR/diff -> parse_diff -> build_lineage -> compute_impact -> classify_risk (LLM)
              |              |                 |                   |
          what changed   sqlglot + schema   where it is used   breaking/silent/safe
                         (incl JOIN/WHERE/                            |
                          GROUP BY edges)                   risky -> human_approval (interrupt) -> report
                                                            safe  -----------------------------> report
```

| Step | Input | Output |
|---|---|---|
| `parse_diff` | before/after SQL or schema | list of changes (rename, drop, type change, logic change) |
| `build_lineage` | SQL models + column schema | column-level dependency graph, including JOIN/WHERE/GROUP BY edges |
| `compute_impact` | graph + changes | every downstream place the change touches |
| `classify_risk` | impacts | breaking / silent / safe, with explanation and fix suggestion |
| `human_approval` | risky impacts | approve or reject (graph pauses via `interrupt`) |
| `report` | all results | final impact report |

## Layers

Three layers, kept separate so the tool works on any SQL project, not only dbt.

| Layer | Package | Job | LLM / LangGraph |
|---|---|---|---|
| Core | `schema_impact.core` | SQL parsing, lineage, diff, impact (pure Python) | No |
| Agent | `schema_impact.agent` | LangGraph state, nodes, checkpointer, interrupt | Yes |
| Adapters | `schema_impact.adapters` | Plain SQL folder, dbt `manifest.json`, dialects | No |

A fourth package, `schema_impact.evals`, holds the benchmark and is not part of the runtime path.

**Design principle:** the core is deterministic and LLM-free. The LLM is used only where judgment is needed: deciding whether a change alters meaning, explaining impact, suggesting fixes. This keeps the factual part (what depends on what) reproducible and limits hallucination to the part that needs interpretation.

Dependencies point one way: `agent` and `adapters` may import `core`; `core` never imports either.

## Roadmap

| Level | What gets built | LLM | How it is tested |
|---|---|---|---|
| L0 | Parser core: SQL files to column-level dependency graph (incl. JOIN/WHERE/GROUP BY) | No | Small toy project |
| L1 | Change detector and impact: before/after diff to rename/drop/type-change, list affected places | No | Hand-made changes on toy project |
| L2 | LangGraph agent: severity, explanation, fix suggestion, approval interrupt, checkpointer | Yes | Toy project |
| L3 | Benchmark: automatic mutations on jaffle_shop_duckdb, ground truth from `dbt build` and output diff, precision/recall | Yes | 100+ cases |
| L4 | Adapters: plain SQL folder, dbt manifest, other dialects; real projects | Yes | Real projects |
| L5 | Packaging: CLI, GitHub Action that comments on PRs, team conventions store | Yes | End-to-end demo |

## Evaluation approach

Ground truth comes from running dbt on mutated copies of a project, not from labelling by hand. What was built and what it measured is described in [Benchmark (L3)](#benchmark-l3).

## v1 scope and limitations

- Lineage needs the SQL plus a column schema, and works on `SELECT` queries only.
- For dbt, compiled SQL and column schema are extracted from `manifest.json`.
- `MERGE` and incremental models are out of scope for v1.
- Data used for development and evaluation is public or synthetic only.

### Lineage edge kinds (implemented in L0)

| Kind | Meaning | Target |
|---|---|---|
| `select` | Source column feeds an output column | that output column |
| `join` | Used in a JOIN condition | whole model |
| `where` | Used in a WHERE filter | whole model |
| `group_by` | Used in GROUP BY | whole model |
| `having` | Used in HAVING | whole model |
| `order_by` | Used in the query's ORDER BY | whole model |

Window `PARTITION BY` / `ORDER BY` columns are `select` edges to the window's output column.

### Known lineage limits

- A JOIN / WHERE / GROUP BY column that belongs to a CTE or a subquery is traced, through any number of CTEs, to the table columns it is made from. A CTE that is a `UNION` is not traced.
- Output columns of a model have no known type (`UNKNOWN`); lineage only needs their names.
- `UNION` queries and dialect-specific syntax have no dedicated tests yet.
- Circular models raise `CircularDependencyError`.

### Change detection and impact (implemented in L1)

`diff_projects(before, after)` lists the changes: `ColumnRenamed`, `ColumnDropped`, `ColumnTypeChanged`, `TableDropped`, `ModelChanged`, `ModelDropped`. `compute_impact(before, changes)` walks the lineage graph from the changed columns and returns the affected models and columns, following chains of models (`orders.amount -> customer_totals.total -> top_customers.total`). Each `Impact` records the roles (select, join, where, ...), the path, and an effect:

| Change | Effect |
|---|---|
| rename, drop, dropped table, dropped model | `breaking` |
| type change, changed model logic | `needs_review` |
| nothing in the lineage graph depends on it | no impact, i.e. safe |

The effect is deterministic and passes unchanged to every model further down the chain. `needs_review` is where a later layer (L2) judges how risky the change is.

A breaking impact means the affected model's query fails, so that model cannot be built and every model that reads it is affected too, whichever of its columns it uses. A change that only needs review (a type change) follows the columns: only what is computed from the changed column is affected.

For a model whose SQL was edited, `diff_projects` also compares its output columns (read from the SQL). A renamed or dropped output column is a `ColumnRenamed` / `ColumnDropped` of that model, so the models reading it are breaking. If the edit only changed the shape of the final select (the rest of the query is identical and the remaining columns are computed the same way), no `ModelChanged` is reported, since readers are then affected only through the columns that changed. Anything else, including a new SQL that no longer resolves, is a `ModelChanged`.

### Known change-detection limits

- A rename is guessed: one column removed and one added in the same table, at the same position, with the same type.
- Added columns are ignored (they cannot break anything downstream).
- Added columns, tables and models are not reported.
- A dropped table or model is traced only through the columns its readers use. A reader that touches no column, such as `SELECT COUNT(*) FROM order_items`, has no lineage edge and is not flagged. This is a known false negative; a table-level "reads" dependency would fix it.
- Model logic changes are detected after normalising the SQL, but not described (which filter changed is not extracted).
- `ModelChanged` is coarse: it marks every column of the model, so a definition changed in one column flags the readers of all its columns. On the benchmark this is 3 of the 7 remaining false alarms.
- A definition changed inside a CTE is a `ModelChanged`, and so is dropping a column the query orders by (ORDER BY then means a table column instead of an output alias). Both err on the side of reporting.

## Agent (L2)

The agent wraps the deterministic core in a LangGraph graph. One review is one checkpoint thread.

```
parse_diff -> compute_impact -> classify_risk -> (needs approval?) -> human_approval -> report
                                                       \-> (no) -----------------------> report
```

| Node | Job | LLM |
|---|---|---|
| `parse_diff` | `diff_projects(before, after)` | No |
| `compute_impact` | `compute_impact(before, changes)` | No |
| `classify_risk` | breaking impacts: high risk, explained from templates. `needs_review` impacts: asked to the judge | Only here |
| `human_approval` | `interrupt()` with a summary; resume with `Command(resume=True/False)` | No |
| `report` | plain-text report, ends with the decision | No |

A review needs approval when any impact is breaking or the judge says `high`. Safe changes finish without a pause and never reach the judge.

**Judge.** `Judge` is a one-method interface. `OllamaJudge` sends the facts of one impact (change, place, roles, path, and the SQL of the affected model) to a local model and asks for JSON (`risk`, `explanation`, `fix`) at temperature 0. The answer is validated and retried with a correction; if it still cannot be understood the result is `high` risk.

**State and checkpointing.** The state holds the project's own dataclasses. `impacts` and `judgments` are appended by reducers; judgments are `Verdict(impact, judgment)` pairs. The checkpointer is given an allow-list of the project's dataclasses. Resuming runs the paused node again from the top, so nothing with a side effect (such as a model call) happens before `interrupt()`; the model is only called in the earlier `classify_risk` node and is not asked again on resume.

### What the local model does in practice

Run on the toy project with `qwen2.5-coder:7b` (`examples/demo_ollama.py`), changing `orders.amount` from DECIMAL to VARCHAR: valid JSON, about 20 seconds for three impacts on a laptop, and all three were judged `high`. The explanations are plausible but not reliable: they state that `SUM` "will fail" although the deterministic layer found that nothing fails, call a type change "data corruption", and every fix is "revert the change". So the model is not discriminating between impacts here and can overstate. Measuring how often it is right is what the L3 benchmark is for.

### Known agent limits

- Paused reviews are kept in memory only: they survive a restart of the graph object, not of the process (SQLite comes with the CLI in L5).
- The report lists every affected column and the whole-model entry separately, so a chain produces a long report. Grouping by model is left for L5.
- Each `needs_review` impact is judged separately, so a chain costs one model call per affected place.
- An approval answer is a boolean; there is no reviewer note yet.

## Benchmark (L3)

The tool is scored against what dbt really does, on the vendored [jaffle_shop](../benchmarks/jaffle_shop/NOTICE.md) project (5 models, 3 seeds).

**Ground truth.** 103 mutations are applied to copies of the project; each copy is built with `dbt seed` and `dbt run`, and every model gets a label:

| Label | Meaning |
|---|---|
| `breaking` | the model failed, or was skipped because something upstream failed |
| `silent` | the model built but its rows or columns differ from the unmutated build (compared in DuckDB) |
| `safe` | the model built and its output is the same |

The model a mutation edits is not scored: its output changes by construction. `dbt test` is not part of the truth.

**Mutations** are JSON specs (`evals/mutations.py`): a seed column renamed, dropped or retyped (29), a model's output column renamed or dropped (54), and 20 hand-written logic edits (a filter, `avg` for `sum`, a join type, a renamed CTE). Every edit checks that its target text exists, so a mutation cannot silently do nothing.

**Dataset.** `benchmarks/dataset/jaffle_shop.json` holds the labels and the project after each mutation (compiled SQL and seed columns, read back from dbt's artifacts), so scoring needs no dbt. 441 (case, model) pairs: 93 breaking, 24 silent, 324 safe. 57 of the 103 cases have a downstream effect; the other 46 only check for false alarms.

**Scoring.** For each case the tool gets what a pull request would contain, the project before and after, and predicts per model: `breaking`, `needs_review` or `none` (worst effect wins). "Affected" means breaking or silent in the truth and breaking or `needs_review` in the prediction. Every ratio is reported with its counts.

### Results

Each row is a measured state of the code; the fixes were made one after another by looking at the errors of the row above.

| | Affected precision | Affected recall | Affected F1 | Breaking precision | Breaking recall | False alarms |
|---|---|---|---|---|---|---|
| Baseline (L0 + L1) | 89.6% | 88.0% | 88.8% | 100% | 49.5% (46/93) | 12 |
| A: a breaking impact takes the whole model down | 90.7% | 100% | 95.1% | 100% | 64.5% (60/93) | 12 |
| B: a model's own output columns are compared | 90.7% | 100% | 95.1% | 100% | 90.3% (84/93) | 12 |
| D: clause columns are traced through CTEs | 88.6% | 100% | 94.0% | 100% | 96.8% (90/93) | 15 |
| C1: an output-shape edit is not a model change | **94.4%** | **100%** | **97.1%** | **100%** | **96.8%** (90/93) | **7** |

All 24 silent models are flagged in every row, and no model was ever predicted breaking that was not. The files are in `benchmarks/results/`.

What the fixes were, and what found them:

- **A.** 14 breaking models were predicted `none`: when a model fails, dbt skips every model that reads it, even readers of columns it does not touch. Propagation followed only the columns readers use.
- **B.** Renaming or dropping an output column of a model was reported only as "the SQL changed", so 30 breaking models were only `needs_review`.
- **D and C1 together.** C1 alone made 6 breaking models disappear: a model-level "changed" flag had been hiding that readers use some columns only in a GROUP BY or JOIN inside a CTE, which the lineage did not trace. D traces them, then C1 stopped flagging readers that never use a renamed or dropped column.

**The LLM judge.** The 34 pairs that stay `needs_review` (3 breaking, 24 silent, 7 safe) were put to `qwen2.5-coder:7b` through `OllamaJudge`. It answered `high` for all 93 impacts: none `low`, none a parse-failure fallback. So it flags every silent model but also all 7 safe ones, and the metrics with the judge equal the deterministic ones. This is one 7B model with one prompt on 7 safe pairs; it says nothing about larger models or other prompts.

### Limits of the benchmark

- **The fixes were chosen by looking at this dataset's errors, so these numbers are not a held-out estimate.** How the tool does on other projects is unknown.
- One small project: 5 models, 3 seeds. 11 of the 20 logic edits are on `customers` or `orders`, which nothing reads.
- "Same output" is judged on the project's own seed data; a change that leaves this small data unchanged is labelled safe.
- Only 24 pairs are silent, and 7 are safe among those needing review, so ratios about them rest on few examples.
- A `SELECT COUNT(*)` reader of a dropped table has no lineage edge and is not flagged (see the change-detection limits).

Reproduce: `uv sync --group bench`, then `python -m schema_impact.evals.build_dataset` (about ten minutes, needs dbt) and `python -m schema_impact.evals.score`.

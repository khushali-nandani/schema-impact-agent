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

Ground truth is produced automatically with mutation testing:

1. A script applies a random mutation (rename, filter change, ...) to a dbt project.
2. Run `dbt build`. Models that fail are **breaking**.
3. Models that build but whose output tables differ from before (compared in DuckDB) are **silent**.
4. Everything else is **safe**.

The agent's predictions are then scored against this ground truth with precision and recall.

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

- Clause edges for a column that comes from a CTE or subquery *output* (rather than a real table) are not traced back to the underlying table yet.
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

### Known change-detection limits

- A rename is guessed: one column removed and one added in the same table, at the same position, with the same type.
- Added columns are ignored (they cannot break anything downstream).
- Added columns, tables and models are not reported.
- A dropped table or model is traced only through the columns its readers use. A reader that touches no column, such as `SELECT COUNT(*) FROM order_items`, has no lineage edge and is not flagged. This is a known false negative; a table-level "reads" dependency would fix it.
- Model logic changes are detected after normalising the SQL, but not described (which filter changed is not extracted).

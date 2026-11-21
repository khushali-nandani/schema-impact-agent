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

`diff_projects(before, after)` lists the changes: `ColumnRenamed`, `ColumnDropped`, `ColumnTypeChanged`, `TableDropped`, `ModelChanged`, `ColumnLogicChanged`, `ModelDropped`. `compute_impact(before, changes)` walks the lineage graph from the changed columns and returns the affected models and columns, following chains of models (`orders.amount -> customer_totals.total -> top_customers.total`). Each `Impact` records the roles (select, join, where, ...), the path, and an effect:

| Change | Effect |
|---|---|
| rename, drop, dropped table, dropped model | `breaking` |
| type change to text where a model gives the column to `SUM`, `AVG`, `date_diff` or arithmetic (DuckDB) | `breaking` |
| any other type change, changed model logic (a whole model or some of its columns) | `needs_review` |
| nothing in the lineage graph depends on it | no impact, i.e. safe |

The effect is deterministic and passes unchanged to every model further down the chain. `needs_review` is where a later layer (L2) judges how risky the change is.

A breaking impact means the affected model's query fails, so that model cannot be built and every model that reads it is affected too, whichever of its columns it uses. A change that only needs review (a type change, or a `ColumnLogicChanged`) follows the columns: only what is computed from the changed column is affected. A `ModelChanged` marks every column of the model.

**A column that becomes text.** A model that only selects it is fine; `SUM(amount)`, `AVG`, `date_diff` or `amount / 100` on text fail on DuckDB, so the model cannot be built and dbt skips everything that reads it. `core/columnuse.py` says, for every (source column, output column) pair of a model, whether the output is the column itself (`PASS`), the column is the direct argument of one of those functions or of plain arithmetic (`STRICT`), or something else (`OTHER`: a cast, `ROUND`, `COUNT`, `MIN`, a comparison, a CASE condition). Across the CTEs of one model the use closest to the table decides unless it is `PASS`. `compute_impact` follows the text type along `PASS` edges; a model that gives it to a `STRICT` use becomes breaking, and so does everything that reads it. A cast stops the trail: `cast(order_estimated_delivery_date as date)` hands a DATE to the models after it.

For a model whose SQL was edited, `diff_projects` also compares its output columns (read from the SQL). A renamed or dropped output column is a `ColumnRenamed` / `ColumnDropped` of that model, so the models reading it are breaking. If the edit only changed the shape of the final select (the rest of the query is identical and the remaining columns are computed the same way), no `ModelChanged` is reported, since readers are then affected only through the columns that changed. Anything else, including a new SQL that no longer resolves, is a `ModelChanged`.

**An edit that changes how some columns are computed.** When the output columns are the same and the SQL differs, `core/logicdiff.py` (`changed_outputs`) says which of them are computed differently, and `diff_projects` reports one `ColumnLogicChanged(model, column)` for each, in output order, so only the readers of those columns are affected (`SUM(x)` changed to `AVG(x)` in one column flags the models that read that column, not those that read the others). It answers with a set of columns, an empty set (the edit changes no result, such as a renamed CTE or different spacing: nothing is reported), or `None` (it cannot narrow the edit down: `ModelChanged`, as before). Both versions are qualified against their schema, so every column says which table or CTE it comes from. Each CTE and the final select is a scope, compared on its projections one by one and on its "frame" (everything but the select list: FROM, JOIN, WHERE, GROUP BY, HAVING, QUALIFY, DISTINCT, ORDER BY, LIMIT), with CTE names made positional so that renaming one is no change. The change is then followed from scope to scope:

- a changed frame changes which rows the scope has, so every column of it;
- a scope that reads one whose rows changed has changed rows too;
- a changed column that a later scope uses in its frame (a join key, a filter, a grouping) changes that scope's rows; one it only selects or aggregates changes only the columns made from it;
- an ORDER BY that reads a changed output column, by alias or by position, is treated as changing the rows (with a LIMIT it does; without one the tool is cautious). Qualify writes an alias used by WHERE, GROUP BY, HAVING or QUALIFY as the expression behind it, so the text of those clauses shows the change, but ORDER BY keeps the alias, so it is checked on its own. Without this, `SELECT -total AS total ... ORDER BY total DESC LIMIT 2` was narrowed to one column although it picks other rows (found by writing the tests, fixed before use).

If the rows of the final select changed, the answer is `None`; so it is for a different set or order of output columns, a CTE added or removed, a `UNION`, and SQL that does not parse or resolve. A rename or drop of an output column in the same edit is not narrowed either: it stays a `ModelChanged` next to the column change.

### Known change-detection limits

- The text rule is measured on DuckDB only (other dialects, Snowflake for one, may convert text to numbers implicitly, so they keep "a type change needs review"; this was not measured), covers a change *to* text only, and knows only `SUM`, `AVG`, `date_diff` and `+ - * /`. Its evidence is 6 `SUM`/`AVG` and 2 `date_diff` failures on Olist and one `/` failure on jaffle_shop, and no case where it fires and dbt succeeds. A text column that reaches `MAX`, a window or a function we do not list stays `needs_review`.
- A CTE that is a `UNION` is not traced by `column_uses`, so a text column behind one is not followed.

- A rename is guessed: one column removed and one added in the same table, at the same position, with the same type.
- Added columns are ignored (they cannot break anything downstream).
- Added columns, tables and models are not reported.
- A dropped table or model is traced only through the columns its readers use. A reader that touches no column, such as `SELECT COUNT(*) FROM order_items`, has no lineage edge and is not flagged. This is a known false negative; a table-level "reads" dependency would fix it.
- Model logic changes are detected after normalising the SQL, but not described (which filter changed is not extracted).
- `ModelChanged` is still coarse where it is reported: it marks every column of the model. That is the case for an edit that changes the rows (a filter, a join, a grouping, a changed column that feeds one, an ORDER BY that reads one), and for anything `changed_outputs` does not understand. Whether such an edit changes any row of the output depends on the data, which the tool does not see, so these stay false alarms when they change nothing.
- A column that is computed differently but whose value is the same on every row (for example `x + 0` for `x`) is still reported as changed: the comparison is of the SQL, not of results.
- The narrowing only reads a model's SQL: a `UNION` makes the answer `None`, and a subquery is handled by being cautious (it sees every change before it), not by tracing it.
- Dropping a column the query orders by is a `ModelChanged` (ORDER BY then means a table column instead of an output alias); it errs on the side of reporting.

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

Run on the toy project with `qwen2.5-coder:7b` (`examples/demo_ollama.py`), changing `orders.amount` from DECIMAL to VARCHAR: valid JSON, about 20 seconds for three impacts on a laptop, and all three were judged `high`. The explanations are plausible but not reliable: they state that `SUM` "will fail" although the deterministic layer then found that nothing fails (it was right: summing text fails on DuckDB, which the tool reports as breaking since the text-type fix, and the demo now retypes `amount` to INTEGER, a change that really does only shift the numbers), call a type change "data corruption", and every fix is "revert the change". So the model is not discriminating between impacts here and can overstate. Measuring how often it is right is what the L3 benchmark is for.

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

**The LLM judge.** The 34 pairs that stay `needs_review` (3 breaking, 24 silent, 7 safe) were put to `qwen2.5-coder:7b` through `OllamaJudge`. (Since the text-type fix described below, 3 of those 34 are breaking and no longer reach the judge.) It answered `high` for all 93 impacts: none `low`, none a parse-failure fallback. So it flags every silent model but also all 7 safe ones, and the metrics with the judge equal the deterministic ones. This is one 7B model with one prompt on 7 safe pairs; it says nothing about larger models or other prompts.

### Limits of the benchmark

- **The fixes were chosen by looking at this dataset's errors, so these numbers are not a held-out estimate.** How the tool does on other projects is unknown.
- One small project: 5 models, 3 seeds. 11 of the 20 logic edits are on `customers` or `orders`, which nothing reads.
- "Same output" is judged on the project's own seed data; a change that leaves this small data unchanged is labelled safe.
- Only 24 pairs are silent, and 7 are safe among those needing review, so ratios about them rest on few examples.
- A `SELECT COUNT(*)` reader of a dropped table has no lineage edge and is not flagged (see the change-detection limits).

Reproduce: `uv sync --group bench`, then `python -m schema_impact.evals.build_dataset` (about ten minutes, needs dbt) and `python -m schema_impact.evals.score`.

### Second project: Olist (held-out)

The jaffle_shop numbers are not a held-out estimate, so the tool was run, unchanged, on a second project: [Olist](../benchmarks/olist/NOTICE.md), an e-commerce dbt project of 22 models (9 staging, 4 intermediate, 9 marts) that reads nine CSV files through dbt-duckdb sources. It was chosen for being a different shape from jaffle_shop (CTE chains, window functions, `dbt_utils`, no seeds) and vendored unmodified.

**What had to be built for it** (none of it in `core/` or `agent/`): the dbt adapter names a CSV source `<source>__<table>` and reads its columns and types from the file with DuckDB, since `catalog.json` lists none; the runner shares the 120 MB of CSV files between case copies by linking them, and a mutation replaces a file instead of writing through the link; and models are compared through a snapshot of their rows taken inside the project folder, because a model that reads a CSV is a view holding a relative path.

**Mutations (420).** 52 source-column renames, 52 drops and 24 type changes (a column read as varchar through `read_csv(..., types=...)`); 134 model-column renames and 134 drops; 24 hand-written logic edits, written from the SQL before the tool was run on the project. 8948 (case, model) pairs: 1358 breaking, 78 silent, 7512 safe.

**The first score** was made with the code of v0.4.0 (`core/`, `agent/` and the scorer unchanged since that tag) and committed before it was read.

| Kind | Pairs | Affected recall | Breaking recall | Silent flagged |
|---|---|---|---|---|
| model rename / drop | 5628 | 100% (644/644) | 100% (644/644) | n/a |
| source rename / drop | 2288 | 100% (672/672) | 100% (672/672) | n/a |
| logic edit | 504 | 100% (42/42) | n/a | 100% (42/42) |
| **source type change** | 528 | **79.5% (62/78)** | **0% (0/42)** | 100% (36/36) |
| All | 8948 | 98.9% (1420/1436) | 96.9% (1316/1358) | 100% (78/78) |

Affected precision is 96.0% (1420/1479) and F1 97.4%; no model was called breaking that was not (1316/1316). The tool does what it was built for on renames, drops and logic edits, including on a project it was not tuned on. Where it is weak:

- **Type changes.** 16 models that dbt fails are predicted `none`, all in the far marts (`category_satisfaction`, `customer_rfm`, `customer_satisfaction`, `seller_performance`); why the change does not reach them has not been analysed yet. Another 26 breaking pairs are predicted `needs_review`: a type change is never called breaking, although dbt fails in these cases. All 36 silent type-change pairs are flagged.
- **False alarms (59).** 56 come from 18 of the 24 logic edits and 3 from one type change: the tool flags readers of an edited model, and in these cases dbt's output for them did not change (for example `upper(order_status)` in `stg_olist__orders`, or an `inner join` where no row was lost). Whether the tool could tell these apart without the data has not been analysed yet.

### After the first fix (tuned, not held-out)

The 16 misses and the 26 type-change pairs called `needs_review` were one cause: in all 8 type-change cases that break something, the tool had flagged the model that really fails, but only as `needs_review`, and `needs_review` follows columns, so the models that read it and do not use the changed column got `none`. dbt skips them. A table of how the retyped column is used against what dbt did (models skipped because something upstream failed left out):

| The reader uses the text column | dbt failed it | dbt built it |
|---|---|---|
| only selects it, compares it, `COUNT`, `MIN`/`MAX` | 0 | 50 |
| `SUM` or `AVG` | 6 | 0 |
| `date_diff` | 2 | 1 (the column had been cast to DATE, so it was not text there) |
| arithmetic on the text column (`amount / 100`, on jaffle_shop) | 1 | 0 |

The columns were matched by name, so one more Olist case that built (a division of an integer made by `date_diff`) does not read the text column and is not counted.

That is the rule described under change detection above. Scored again, with the code at the commit of that fix:

| Kind | Affected recall | Breaking recall |
|---|---|---|
| source type change, held-out | 79.5% (62/78) | 0% (0/42) |
| source type change, after the fix | **100%** (78/78) | **100%** (42/42) |

Every other kind is unchanged to the pair. Overall on Olist: breaking recall 96.9% -> 100% (1358/1358), affected recall 98.9% -> 100% (1436/1436), affected F1 97.4% -> 98.0%, no model called breaking that is not (1358/1358), and the 59 false alarms are as before. On jaffle_shop the 3 breaking pairs that had stayed `needs_review` are now breaking (breaking recall 90/93 -> 93/93, precision 100%).

These are tuned numbers: the rule was found by reading the errors of the held-out run, so Olist can no longer say how well the tool does on a new project. The limits of the rule are listed under the change-detection limits. What is left is the false alarms: 39 of the 56 on logic edits come from 13 edits that change one output column, where the tool reports the whole model as changed, 4 from a pure refactor (a renamed CTE), and 13 from filters and joins that happen to change no rows; the last group cannot be told apart without the data. The first two are the next fix, below.

### After the second fix (tuned, not held-out)

39 of the 56 false alarms on logic edits were edits that change how one output column is computed (`avg` for `sum`, `round(x, 0)` for `round(x, 2)`, `upper(status)`), where the tool reported the whole model as changed, so every model reading any column of it was flagged; 4 were a renamed CTE, which changes nothing. The tool now compares the two versions of the SQL scope by scope and reports only the columns that are computed differently (see "An edit that changes how some columns are computed" under change detection). Scored again, with the code at the commit of that fix:

| | Olist held-out | Olist after fix 1 | Olist after fix 2 | jaffle_shop after fix 1 | jaffle_shop after fix 2 |
|---|---|---|---|---|---|
| Affected models: precision | 96.0% | 96.1% | **98.9%** (1436/1452) | 94.4% | **95.9%** (117/122) |
| Affected models: F1 | 97.4% | 98.0% | **99.4%** | 97.1% | **97.9%** |
| Breaking models found | 96.9% | 100% | **100%** (1358/1358) | 100% | **100%** (93/93) |
| Models called breaking that are not | 0 | 0 | **0** | 0 | **0** |
| Silent models flagged | 78/78 | 78/78 | **78/78** | 24/24 | **24/24** |
| Missed (predicted none) | 16 | 0 | **0** | 0 | **0** |
| False alarms | 59 | 59 | **16** | 7 | **5** |

The false alarms fell from 59 to 16 and nothing that was found before was lost: no silent model is missed and no model is called breaking that is not. Narrowing an edit is the one step of this tool that can hide a real change, so the guard was fixed before scoring: silent models flagged and breaking precision had to stay as they were, or the fix would have been reverted.

The 16 that are left, from the report: 13 are logic edits that change the rows of a model (three `inner join` edits, `only_priced_above_ten`, `drop_vouchers`, `only_sao_paulo`), reported as a whole-model change because the rows may differ and that is not decided by the SQL; whether they really did not change any output here depends on Olist's data. The other 3 are one type change (`order_estimated_delivery_date`, 3 models). The reason these 13 and 3 stay has not been analysed beyond that.

**Checked against data, not only against the tool's own reasoning.** `tests/test_column_logic_soundness.py` builds a small project in DuckDB on made-up rows (nulls, a customer without orders, orders without a customer) and applies 35 edits: a changed column read by a join, a filter, a grouping, an ORDER BY with LIMIT or a window, and refactors that change nothing. For every edit it checks that the columns the tool did not report have exactly the same rows as before, and that every model whose output really changed is flagged. None failed. Removing the ORDER BY check on purpose makes one of them fail (`SELECT -total AS total ... ORDER BY total DESC LIMIT 2` picks other rows), so the test does catch the kind of mistake it is there for. It is 35 edits on 5 models, not a proof.

These are tuned numbers like the ones before: the rule was found by reading the errors of the held-out run.

**Limits.** Two projects are not a sample. 78 pairs are silent. 268 of the 420 cases are the easy model-column edits, so report by kind. "Same output" is judged on Olist's own data, which is not in the repository (CC BY-NC-SA 4.0). The ground truth is built with DuckDB on one thread: with several threads `customer_rfm` (an `ntile` over tied values) differs on every build and made 258 pairs look "silent" in a first build, which was discarded. From the commit after this result, any fix is made by reading these errors, so the later rows are tuned and are labelled so.

Reproduce: put the CSV files in `benchmarks/olist/data` (see its `DATA.md`), `dbt deps` in that folder, then `python -m schema_impact.evals.build_dataset --benchmark olist` (about an hour) and `python -m schema_impact.evals.score --dataset benchmarks/dataset/olist.json`.

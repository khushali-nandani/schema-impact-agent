# jaffle_shop ground-truth dataset

`jaffle_shop.json` says what really happens when each of 103 mutations is applied to the vendored
`benchmarks/jaffle_shop` dbt project. It was produced by running dbt, not by this tool, and is the
answer key the tool is scored against.

```
uv sync --group bench
uv run python -m schema_impact.evals.build_dataset      # about ten minutes
```

Made with dbt-core 1.12.7, dbt-duckdb 1.11.1 and duckdb 2.0.1 (also recorded in the file).

## What each case holds

- the mutation: a seed column renamed, dropped or retyped; a model output column renamed or
  dropped; or a hand-written logic edit (a filter, `avg` instead of `sum`, a join type, ...)
- the dbt status of every model after `dbt seed` and `dbt run`
- a label for every model the mutation is judged on
- the project after the mutation (compiled SQL and seed columns), so the tool can be given the
  same before/after pair a pull request would contain

## Labels

| Label | Meaning |
|---|---|
| `breaking` | the model failed, or was skipped because something upstream failed |
| `silent` | the model built, but its rows or columns are different from before |
| `safe` | the model built and its output is the same |

The model a mutation edits is not scored: its output changes by construction, so it is the
change and not an impact of it.

## Distribution

441 (case, model) pairs: 93 breaking, 24 silent, 324 safe. 57 of the 103 cases have a real
downstream effect; the other 46 only check that the tool raises no false alarm.

## Limits

- One small project (5 models, 3 seeds). Results say how the tool does on jaffle_shop, not on
  every project.
- "Same output" is judged on the project's own seed data. A change that leaves this small data
  unchanged is labelled safe even though other data could expose it.
- `dbt test` is not part of the ground truth.
- Only 24 pairs are silent, so statistics about silent changes rest on few examples.
- 11 of the 20 logic recipes edit `customers` or `orders`, which nothing reads, so they have no
  downstream impact by construction.

---

# olist ground-truth dataset

`olist.json` is the same kind of answer key for the second benchmark project, `benchmarks/olist`
(an Olist dbt project with 22 models, vendored from `michaelf108/ecommerce-analytics-dbt`, see its
`NOTICE.md`). The tool was not changed with this project in view: it is the held-out test.

```
uv sync --group bench
# put the nine Olist CSV files in benchmarks/olist/data (see benchmarks/olist/DATA.md), then:
(cd benchmarks/olist && uv run --no-sync dbt deps --profiles-dir .)
uv run --no-sync python -m schema_impact.evals.build_dataset --benchmark olist   # about an hour
```

The labels depend on the Olist data itself, which is not in this repository (CC BY-NC-SA 4.0).
The file records a SHA-256 of each CSV in `meta.data_files`; a build refuses to resume on other
data. Only compiled SQL, column names and types and labels are stored, never rows.

## Cases

420 mutations: 52 source-column renames, 52 drops and 24 type changes (a column read as varchar);
134 model-column renames and 134 drops (every output column of every model whose last statement is
`select * from <cte>`); and 24 hand-written logic edits, written from the models' SQL before the
tool was run on the project.

## Distribution

8948 (case, model) pairs: 1358 breaking, 78 silent, 7512 safe.

| Kind | Cases | breaking | silent | safe |
|---|---|---|---|---|
| source rename | 52 | 336 | 0 | 808 |
| source drop | 52 | 336 | 0 | 808 |
| source type change | 24 | 42 | 36 | 450 |
| model rename | 134 | 322 | 0 | 2492 |
| model drop | 134 | 322 | 0 | 2492 |
| logic edit | 24 | 0 | 42 | 462 |

## Limits

- One project of one domain. Together with jaffle_shop it is two, not a sample of dbt projects.
- Only 78 pairs are silent, 42 of them from 24 hand-written edits, so statistics about silent
  changes rest on few examples.
- The model-column cases are 268 of 420, and most of their pairs are safe. Report results by kind.
- "Same output" is judged on the project's own data; `dbt test` is not part of the ground truth.
- Builds run DuckDB on one thread. With several threads `customer_rfm` (an `ntile` over tied
  values) gives different output on every build, which made 258 pairs "silent" in a first build
  of this dataset. That build was discarded; this one was checked to be reproducible.
- Built with dbt-core 1.12.7, dbt-duckdb 1.11.1 and duckdb 2.0.1.

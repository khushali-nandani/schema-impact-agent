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

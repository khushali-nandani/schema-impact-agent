# jaffle_shop dbt artifacts (trimmed fixture)

`target/manifest.json` and `target/catalog.json` come from a real run on the vendored
`benchmarks/jaffle_shop` project:

```
dbt seed && dbt run && dbt docs generate     # dbt-core 1.12.7, dbt-duckdb 1.11.1
```

They are trimmed to the fields the adapter reads (models and seeds only, no tests, no docs, no
macros), so the tests can run without dbt. Values are untouched: `compiled_code` is exactly what
dbt produced, with the Jinja expanded and tables written as `"jaffle_shop"."main"."stg_orders"`.

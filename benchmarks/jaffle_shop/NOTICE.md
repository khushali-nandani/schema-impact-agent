# jaffle_shop (vendored)

This folder is a copy of the dbt project [`dbt-labs/jaffle_shop_duckdb`](https://github.com/dbt-labs/jaffle_shop_duckdb)
(branch `duckdb`), used as the base project of this repository's benchmark.

| | |
|---|---|
| Source | https://github.com/dbt-labs/jaffle_shop_duckdb |
| Commit | `20cc904370818ceab08cf19f8fd196e9e81def88` (2026-09-28), also in the `SHA` file |
| License | Apache License 2.0, see `LICENSE` in this folder |
| Copyright | dbt Labs, Inc. |
| Data | Fictional (a sandwich shop); the seeds are synthetic |

## What was copied

The dbt project files only: `dbt_project.yml`, `profiles.yml`, `models/`, `seeds/` and `LICENSE`.
Documentation, images, the Dockerfile and Python packaging files were left out.

## Changes

None. The files are byte-for-byte what the commit above contains. The benchmark applies its
mutations to *temporary copies* of this project and never edits these files.

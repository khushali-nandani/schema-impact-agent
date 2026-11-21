# Olist e-commerce dbt project (vendored)

The dbt project in this folder is a copy of
[`michaelf108/ecommerce-analytics-dbt`](https://github.com/michaelf108/ecommerce-analytics-dbt),
used as the second project of this repository's benchmark.

| | |
|---|---|
| Source | https://github.com/michaelf108/ecommerce-analytics-dbt |
| Commit | `0e10734d7b7a591dd6e45d2c9dd9b03df831661b` (2026-07-18), also in the `SHA` file |
| License | MIT, see `LICENSE` in this folder |
| Copyright | michaelf108 (2026) |

## What was copied

The dbt project itself: `dbt_project.yml`, `profiles.yml`, `packages.yml`, `package-lock.yml`,
`LICENSE`, and every file under `models/`, `tests/` and `analyses/`. The README, `pyproject.toml`
and the Python lock file were left out.

## Changes

None. The files are byte-for-byte what the commit above contains. The benchmark applies its
mutations to *temporary copies* of this project and never edits these files.

## The data is not here

The models read nine CSV files from the
[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
published on Kaggle under **CC BY-NC-SA 4.0** (attribution, non-commercial, share-alike).
That data is not part of this repository. See `DATA.md` for how to get it.

What this repository does keep from running the benchmark: the compiled SQL of the models, the
column names and types of the source tables, and labels (`breaking`, `silent`, `safe`) computed by
running dbt. It keeps no row of the dataset.

Data: Olist, Brazilian E-Commerce Public Dataset by Olist, https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce,
CC BY-NC-SA 4.0.

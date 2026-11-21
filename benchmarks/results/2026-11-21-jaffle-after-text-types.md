# jaffle_shop after fix 1: a column that becomes text (Olist errors were read; tuned)

103 cases, 441 (case, model) pairs. 0 cases skipped. Ground truth from dbt-core 1.12.7, dbt-duckdb 1.11.1.

## Metrics

| Metric | Value |
|---|---|
| Affected models: precision | 94.4% (117/124) |
| Affected models: recall | 100.0% (117/117) |
| Affected models: F1 | 97.1% |
| Breaking: precision | 100.0% (93/93) |
| Breaking: recall | 100.0% (93/93) |
| Silent models flagged (anything but none) | 100.0% (24/24) |
| needs_review turned out to be | 7 safe, 24 silent (of 31) |

## Confusion matrix

| truth \ predicted | breaking | needs_review | none |
|---|---|---|---|
| breaking | 93 | 0 | 0 |
| silent | 0 | 24 | 0 |
| safe | 0 | 7 | 317 |

## By mutation kind

| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |
|---|---|---|---|---|
| model_drop_column | 108 | 100.0% (15/15) | 100.0% (15/15) | n/a (0/0) |
| model_logic | 80 | 100.0% (13/13) | n/a (0/0) | 100.0% (13/13) |
| model_rename_column | 108 | 100.0% (15/15) | 100.0% (15/15) | n/a (0/0) |
| seed_change_type | 35 | 100.0% (14/14) | 100.0% (3/3) | 100.0% (11/11) |
| seed_drop_column | 55 | 100.0% (30/30) | 100.0% (30/30) | n/a (0/0) |
| seed_rename_column | 55 | 100.0% (30/30) | 100.0% (30/30) | n/a (0/0) |

## Errors

### Missed (breaking or silent, predicted none): 0

- none

### False alarms (safe, but flagged): 7

- model_logic:stg_orders.upper_status: customers
- model_logic:stg_payments.lower_payment_method: customers, orders
- seed_change_type:raw_orders.id: customers
- seed_change_type:raw_orders.user_id: customers
- seed_change_type:raw_payments.order_id: customers, orders

# Deterministic baseline, before any fix (L0 + L1, no LLM)

103 cases, 441 (case, model) pairs. 0 cases skipped. Ground truth from dbt-core 1.12.7, dbt-duckdb 1.11.1.

## Metrics

| Metric | Value |
|---|---|
| Affected models: precision | 89.6% (103/115) |
| Affected models: recall | 88.0% (103/117) |
| Affected models: F1 | 88.8% |
| Breaking: precision | 100.0% (46/46) |
| Breaking: recall | 49.5% (46/93) |
| Silent models flagged (anything but none) | 100.0% (24/24) |
| needs_review turned out to be | 33 breaking, 12 safe, 24 silent (of 69) |

## Confusion matrix

| truth \ predicted | breaking | needs_review | none |
|---|---|---|---|
| breaking | 46 | 33 | 14 |
| silent | 0 | 24 | 0 |
| safe | 0 | 12 | 312 |

## By mutation kind

| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |
|---|---|---|---|---|
| model_drop_column | 108 | 100.0% (15/15) | 0.0% (0/15) | n/a (0/0) |
| model_logic | 80 | 100.0% (13/13) | n/a (0/0) | 100.0% (13/13) |
| model_rename_column | 108 | 100.0% (15/15) | 0.0% (0/15) | n/a (0/0) |
| seed_change_type | 35 | 100.0% (14/14) | 0.0% (0/3) | 100.0% (11/11) |
| seed_drop_column | 55 | 76.7% (23/30) | 76.7% (23/30) | n/a (0/0) |
| seed_rename_column | 55 | 76.7% (23/30) | 76.7% (23/30) | n/a (0/0) |

## Errors

### Missed (breaking or silent, predicted none): 14

- seed_drop_column:raw_orders.status: customers
- seed_drop_column:raw_orders.user_id: customers
- seed_drop_column:raw_payments.id: customers, orders
- seed_drop_column:raw_payments.order_id: customers, orders
- seed_drop_column:raw_payments.payment_method: customers
- seed_rename_column:raw_orders.status: customers
- seed_rename_column:raw_orders.user_id: customers
- seed_rename_column:raw_payments.id: customers, orders
- seed_rename_column:raw_payments.order_id: customers, orders
- seed_rename_column:raw_payments.payment_method: customers

### False alarms (safe, but flagged): 12

- model_drop_column:stg_orders.status: customers
- model_drop_column:stg_payments.payment_id: customers, orders
- model_drop_column:stg_payments.payment_method: customers
- model_logic:stg_orders.upper_status: customers
- model_logic:stg_payments.lower_payment_method: customers, orders
- model_rename_column:stg_orders.status: customers
- model_rename_column:stg_payments.payment_id: customers, orders
- model_rename_column:stg_payments.payment_method: customers
- seed_change_type:raw_orders.id: customers

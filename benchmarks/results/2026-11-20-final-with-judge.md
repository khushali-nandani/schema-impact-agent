# Deterministic, and with a local LLM judge (qwen2.5-coder:7b)

103 cases, 441 (case, model) pairs. 0 cases skipped. Ground truth from dbt-core 1.12.7, dbt-duckdb 1.11.1.

## Metrics

| Metric | Value |
|---|---|
| Affected models: precision | 94.4% (117/124) |
| Affected models: recall | 100.0% (117/117) |
| Affected models: F1 | 97.1% |
| Breaking: precision | 100.0% (90/90) |
| Breaking: recall | 96.8% (90/93) |
| Silent models flagged (anything but none) | 100.0% (24/24) |
| needs_review turned out to be | 3 breaking, 7 safe, 24 silent (of 34) |

## Confusion matrix

| truth \ predicted | breaking | needs_review | none |
|---|---|---|---|
| breaking | 90 | 3 | 0 |
| silent | 0 | 24 | 0 |
| safe | 0 | 7 | 317 |

## By mutation kind

| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |
|---|---|---|---|---|
| model_drop_column | 108 | 100.0% (15/15) | 100.0% (15/15) | n/a (0/0) |
| model_logic | 80 | 100.0% (13/13) | n/a (0/0) | 100.0% (13/13) |
| model_rename_column | 108 | 100.0% (15/15) | 100.0% (15/15) | n/a (0/0) |
| seed_change_type | 35 | 100.0% (14/14) | 0.0% (0/3) | 100.0% (11/11) |
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

## With the LLM judge

Judge: qwen2.5-coder:7b (answers saved 2026-11-20). 93 impacts of 34 models were put to it; every other prediction is unchanged. A model stays flagged if any of its impacts was judged high.

| Metric | Deterministic | With the judge |
|---|---|---|
| Affected models: precision | 94.4% (117/124) | 94.4% (117/124) |
| Affected models: recall | 100.0% (117/117) | 100.0% (117/117) |
| Affected models: F1 | 97.1% | 97.1% |
| Breaking: precision | 100.0% (90/90) | 100.0% (90/90) |
| Breaking: recall | 96.8% (90/93) | 96.8% (90/93) |
| Silent models flagged | 100.0% (24/24) | 100.0% (24/24) |

What the judge said about the models that needed review:

| truth \ judge | high | low |
|---|---|---|
| breaking | 3 | 0 |
| silent | 24 | 0 |
| safe | 7 | 0 |

## Where the judge was wrong

### Judged low but not safe: 0

- none

### Judged high but safe: 7

- seed_change_type:raw_orders.id: customers
- seed_change_type:raw_orders.user_id: customers
- seed_change_type:raw_payments.order_id: customers
- seed_change_type:raw_payments.order_id: orders
- model_logic:stg_orders.upper_status: customers
- model_logic:stg_payments.lower_payment_method: customers
- model_logic:stg_payments.lower_payment_method: orders

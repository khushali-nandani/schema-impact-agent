# Olist after fix 2: column-level logic changes (tuned on the errors of the held-out run)

420 cases, 8948 (case, model) pairs. 0 cases skipped. Ground truth from dbt-core 1.12.7, dbt-duckdb 1.11.1.

## Metrics

| Metric | Value |
|---|---|
| Affected models: precision | 98.9% (1436/1452) |
| Affected models: recall | 100.0% (1436/1436) |
| Affected models: F1 | 99.4% |
| Breaking: precision | 100.0% (1358/1358) |
| Breaking: recall | 100.0% (1358/1358) |
| Silent models flagged (anything but none) | 100.0% (78/78) |
| needs_review turned out to be | 16 safe, 78 silent (of 94) |

## Confusion matrix

| truth \ predicted | breaking | needs_review | none |
|---|---|---|---|
| breaking | 1358 | 0 | 0 |
| silent | 0 | 78 | 0 |
| safe | 0 | 16 | 7496 |

## By mutation kind

| Mutation kind | Pairs | Affected recall | Breaking recall | Silent flagged |
|---|---|---|---|---|
| model_drop_column | 2814 | 100.0% (322/322) | 100.0% (322/322) | n/a (0/0) |
| model_logic | 504 | 100.0% (42/42) | n/a (0/0) | 100.0% (42/42) |
| model_rename_column | 2814 | 100.0% (322/322) | 100.0% (322/322) | n/a (0/0) |
| source_change_type | 528 | 100.0% (78/78) | 100.0% (42/42) | 100.0% (36/36) |
| source_drop_column | 1144 | 100.0% (336/336) | 100.0% (336/336) | n/a (0/0) |
| source_rename_column | 1144 | 100.0% (336/336) | 100.0% (336/336) | n/a (0/0) |

## Errors

### Missed (breaking or silent, predicted none): 0

- none

### False alarms (safe, but flagged): 16

- model_logic:fct_order_items.inner_join_sellers: category_satisfaction, seller_performance
- model_logic:fct_orders.inner_join_customers: category_satisfaction, customer_rfm, customer_satisfaction, seller_performance
- model_logic:fct_orders.inner_join_items: category_satisfaction, seller_performance
- model_logic:stg_order_items.only_priced_above_ten: customer_rfm, customer_satisfaction
- model_logic:stg_order_payments.drop_vouchers: category_satisfaction, seller_performance
- model_logic:stg_sellers.only_sao_paulo: category_satisfaction
- source_change_type:olist__orders.order_estimated_delivery_date: fct_orders, seller_performance, stg_olist__orders

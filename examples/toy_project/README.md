# Toy project

A small synthetic SQL project used as the test fixture for L0 and L1. All names are made up; dialect is DuckDB.

## Layout

```
schema.sql                 source tables (DDL)
models/customer_totals.sql revenue per customer
models/product_sales.sql   units and revenue per product, excluding returned orders
models/top_customers.sql   customers above a revenue threshold, reads from customer_totals
```

A *model* is one `.sql` file containing a single `SELECT`. The model name is the file name without extension. A model can read a source table or another model: `top_customers` reads `customer_totals`, which gives the chain `orders.amount -> customer_totals.total -> top_customers.total`.

## Source tables

| Table | Columns |
|---|---|
| `customers` | id, name, country |
| `products` | id, name, price |
| `orders` | id, customer_id, status, amount |
| `order_items` | order_id, product_id, quantity |

## Expected dependencies

This is the hand-written ground truth the L0 tests assert against. Each row says: an upstream column is used by a model in a given role.

Roles: `select` feeds an output column, `join` is used in a JOIN condition, `where` is used in a filter, `group_by` is used in GROUP BY. `join`, `where` and `group_by` affect the whole model rather than one output column.

### customer_totals

| Upstream column | Role | Affects |
|---|---|---|
| `customers.name` | select | `name` |
| `customers.name` | group_by | model |
| `orders.amount` | select | `total` |
| `orders.customer_id` | join | model |
| `customers.id` | join | model |

### product_sales

| Upstream column | Role | Affects |
|---|---|---|
| `products.name` | select | `product_name` |
| `products.name` | group_by | model |
| `order_items.quantity` | select | `units_sold`, `revenue` |
| `products.price` | select | `revenue` |
| `order_items.order_id` | join | model |
| `orders.id` | join | model |
| `order_items.product_id` | join | model |
| `products.id` | join | model |
| `orders.status` | where | model |

### top_customers

Upstream columns here are columns of the model `customer_totals`, not of a source table.

| Upstream column | Role | Affects |
|---|---|---|
| `customer_totals.name` | select | `name` |
| `customer_totals.total` | select | `total` |
| `customer_totals.total` | where | model |
| `customer_totals.total` | order_by | model |

Because `total` is filtered with `WHERE total > 1000`, a change in the meaning of `customer_totals.total` (for example rupees to paise) changes `top_customers` without any error. That is a silent case.

## Safe and partial cases

- `customers.country` is used by no model. It exists on purpose, so that changes to it can be classified as **safe**.
- Some columns are used by only one model. Dropping `orders.amount` breaks `customer_totals` but not `product_sales`; dropping `orders.status` breaks `product_sales` but not `customer_totals`.

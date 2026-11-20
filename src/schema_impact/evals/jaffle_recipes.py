"""Hand-written logic mutations for the vendored jaffle_shop project.

Each recipe is a text replacement in one model's Jinja source. The right answer for each one
(breaking, silent or safe) is not written down here: it is found by running dbt on the mutated
project, so a recipe that turns out to change nothing is simply a "safe" case.

Some are meant to change the numbers (a new filter, avg instead of sum), some only refactor (a
renamed CTE, `lower()` on a column that is already lower case) and should not.
"""

from schema_impact.evals.mutations import Recipe

JAFFLE_RECIPES: list[Recipe] = [
    # --- stg_customers
    Recipe(
        "stg_customers.keep_first_fifty",
        "stg_customers",
        "Keep only customers with id <= 50",
        "from source",
        "from source\n    where id <= 50",
    ),
    Recipe(
        "stg_customers.upper_first_name",
        "stg_customers",
        "Upper-case first_name",
        "first_name,",
        "upper(first_name) as first_name,",
    ),
    # --- stg_orders
    Recipe(
        "stg_orders.drop_returned_orders",
        "stg_orders",
        "Filter out returned orders",
        "from source",
        "from source\n    where status != 'returned'",
    ),
    Recipe(
        "stg_orders.upper_status",
        "stg_orders",
        "Upper-case status",
        "order_date,\n        status",
        "order_date,\n        upper(status) as status",
    ),
    Recipe(
        "stg_orders.shift_order_ids",
        "stg_orders",
        "Add 1000 to every order_id",
        "id as order_id",
        "id + 1000 as order_id",
    ),
    # --- stg_payments
    Recipe(
        "stg_payments.divide_by_1000",
        "stg_payments",
        "Convert cents to dollars with 1000 instead of 100",
        "amount / 100 as amount",
        "amount / 1000 as amount",
    ),
    Recipe(
        "stg_payments.no_cents_conversion",
        "stg_payments",
        "Stop converting cents to dollars",
        "amount / 100 as amount",
        "amount as amount",
    ),
    Recipe(
        "stg_payments.only_big_payments",
        "stg_payments",
        "Keep only payments above 1000 cents",
        "from source",
        "from source\n    where amount > 1000",
    ),
    Recipe(
        "stg_payments.lower_payment_method",
        "stg_payments",
        "Lower-case payment_method (already lower case)",
        "payment_method,",
        "lower(payment_method) as payment_method,",
    ),
    # --- customers
    Recipe(
        "customers.latest_first_order",
        "customers",
        "Use max instead of min for first_order",
        "min(order_date) as first_order",
        "max(order_date) as first_order",
    ),
    Recipe(
        "customers.count_distinct_orders",
        "customers",
        "Count distinct order ids (ids are already unique)",
        "count(order_id) as number_of_orders",
        "count(distinct order_id) as number_of_orders",
    ),
    Recipe(
        "customers.inner_join_orders_for_payments",
        "customers",
        "Inner join instead of left join when attaching orders to payments",
        "left join orders on",
        "inner join orders on",
    ),
    Recipe(
        "customers.inner_join_customer_orders",
        "customers",
        "Inner join instead of left join to customer_orders",
        "left join customer_orders",
        "inner join customer_orders",
    ),
    Recipe(
        "customers.rename_cte",
        "customers",
        "Rename the customer_orders CTE (pure refactor)",
        "customer_orders",
        "cust_orders",
        count=None,
    ),
    Recipe(
        "customers.double_payment_total",
        "customers",
        "Double the summed payments",
        "sum(amount) as total_amount",
        "sum(amount) * 2 as total_amount",
    ),
    # --- orders
    Recipe(
        "orders.average_not_sum",
        "orders",
        "Average the payments instead of summing them",
        "sum(amount) as total_amount",
        "avg(amount) as total_amount",
    ),
    Recipe(
        "orders.drop_gift_cards",
        "orders",
        "Remove gift_card from the list of payment methods",
        ", 'gift_card'",
        "",
    ),
    Recipe(
        "orders.inner_join_payments",
        "orders",
        "Inner join instead of left join to order_payments",
        "left join order_payments",
        "inner join order_payments",
    ),
    Recipe(
        "orders.upper_status",
        "orders",
        "Upper-case status",
        "orders.status,",
        "upper(orders.status) as status,",
    ),
    Recipe(
        "orders.double_amounts",
        "orders",
        "Double every per-method amount",
        "then amount else 0 end",
        "then amount * 2 else 0 end",
    ),
]

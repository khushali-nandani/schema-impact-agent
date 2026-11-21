"""Hand-written logic mutations for the vendored Olist project.

Same idea as the jaffle_shop recipes: each one is a text replacement in one model's Jinja source,
and its label (breaking, silent or safe) is found by running dbt, not decided here. The kinds are
the same too: a new filter, a join made stricter, one aggregate swapped for another, a cast or a
rounding, a constant, and pure refactors (a renamed CTE) that should change nothing.

These were written from the models' SQL alone, before the tool had been run on this project, so
that they are not chosen to suit what it can or cannot do.
"""

from schema_impact.evals.mutations import Recipe

OLIST_RECIPES: list[Recipe] = [
    # --- staging
    Recipe(
        "stg_orders.upper_status",
        "stg_olist__orders",
        "Upper-case order_status",
        "            order_status,",
        "            upper(order_status) as order_status,",
    ),
    Recipe(
        "stg_orders.only_delivered",
        "stg_olist__orders",
        "Keep only delivered orders",
        "from source",
        "from source\n        where order_status = 'delivered'",
    ),
    Recipe(
        "stg_order_items.only_priced_above_ten",
        "stg_olist__order_items",
        "Keep only items that cost more than 10",
        "from source",
        "from source\n        where price > 10",
    ),
    Recipe(
        "stg_order_items.price_in_cents",
        "stg_olist__order_items",
        "Express price in cents",
        "            price,",
        "            price * 100 as price,",
    ),
    Recipe(
        "stg_order_payments.at_least_one_installment",
        "stg_olist__order_payments",
        "Count at least one installment",
        "            payment_installments,",
        "            greatest(payment_installments, 1) as payment_installments,",
    ),
    Recipe(
        "stg_order_payments.drop_vouchers",
        "stg_olist__order_payments",
        "Leave out voucher payments",
        "from source",
        "from source\n        where payment_type != 'voucher'",
    ),
    Recipe(
        "stg_order_reviews.lower_title",
        "stg_olist__order_reviews",
        "Lower-case the review title",
        "review_comment_title,",
        "lower(review_comment_title) as review_comment_title,",
    ),
    Recipe(
        "stg_customers.upper_city",
        "stg_olist__customers",
        "Upper-case customer_city",
        "customer_city,",
        "upper(customer_city) as customer_city,",
    ),
    Recipe(
        "stg_sellers.only_sao_paulo",
        "stg_olist__sellers",
        "Keep only sellers from SP",
        "from source",
        "from source where seller_state = 'SP'",
    ),
    Recipe(
        "stg_products.photos_as_double",
        "stg_olist__products",
        "Cast product_photos_qty to double",
        "product_photos_qty,",
        "cast(product_photos_qty as double) as product_photos_qty,",
    ),
    Recipe(
        "stg_geolocation.round_latitude",
        "stg_olist__geolocation",
        "Round the latitude to two decimals",
        "geolocation_lat,",
        "round(geolocation_lat, 2) as geolocation_lat,",
    ),
    # --- intermediate
    Recipe(
        "int_order_items.average_price",
        "int_order_items_aggregated",
        "Average the item prices instead of summing them",
        "sum(price) as total_item_price",
        "avg(price) as total_item_price",
    ),
    Recipe(
        "int_order_payments.distinct_payment_types",
        "int_order_payments_aggregated",
        "Count distinct payment types instead of payments",
        "count(*) as payment_count",
        "count(distinct payment_type) as payment_count",
    ),
    Recipe(
        "int_order_reviews.best_score",
        "int_order_reviews_aggregated",
        "Take the best review score instead of the average",
        "avg(review_score) as avg_review_score",
        "max(review_score) as avg_review_score",
    ),
    Recipe(
        "int_geolocation.min_latitude",
        "int_geolocation_deduped",
        "Take the smallest latitude instead of the average",
        "avg(geolocation_lat) as latitude",
        "min(geolocation_lat) as latitude",
    ),
    # --- marts
    Recipe(
        "fct_orders.inner_join_items",
        "fct_orders",
        "Inner join instead of left join to the item totals",
        "left join items on",
        "inner join items on",
    ),
    Recipe(
        "fct_orders.inner_join_customers",
        "fct_orders",
        "Inner join instead of left join to the customers",
        "left join customers on",
        "inner join customers on",
    ),
    Recipe(
        "fct_orders.rename_cte",
        "fct_orders",
        "Rename the final CTE (pure refactor)",
        "final",
        "result",
        count=None,
    ),
    Recipe(
        "fct_order_items.inner_join_sellers",
        "fct_order_items",
        "Inner join instead of left join to the sellers",
        "left join sellers on",
        "inner join sellers on",
    ),
    Recipe(
        "dim_products.inner_join_categories",
        "dim_products",
        "Inner join instead of left join to the category translation",
        "left join\n            categories",
        "inner join\n            categories",
    ),
    Recipe(
        "customer_rfm.rename_cte",
        "customer_rfm",
        "Rename the rfm_base CTE (pure refactor)",
        "rfm_base",
        "rfm_basis",
        count=None,
    ),
    Recipe(
        "customer_satisfaction.whole_spend",
        "customer_satisfaction",
        "Round total spent to whole numbers",
        "round(sum(total_payment_value), 2)",
        "round(sum(total_payment_value), 0)",
    ),
    Recipe(
        "seller_performance.late_by_three_days",
        "seller_performance",
        "Count an item as late only when it is more than 3 days late",
        "delivery_vs_estimate_days > 0",
        "delivery_vs_estimate_days > 3",
    ),
    Recipe(
        "category_satisfaction.keep_unreviewed",
        "category_satisfaction",
        "Stop leaving out orders that have no review",
        "        where o.avg_review_score is not null\n",
        "",
    ),
]

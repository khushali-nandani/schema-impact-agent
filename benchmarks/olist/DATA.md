# Getting the Olist data

The models read CSV files from `data/` (this folder's `data/` directory, which git ignores).
Download the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
from Kaggle (a free account is needed), unzip it, and put these nine files in `benchmarks/olist/data/`:

```
olist_customers_dataset.csv        olist_order_reviews_dataset.csv
olist_geolocation_dataset.csv      olist_orders_dataset.csv
olist_order_items_dataset.csv      olist_products_dataset.csv
olist_order_payments_dataset.csv   olist_sellers_dataset.csv
product_category_name_translation.csv
```

The dataset is licensed CC BY-NC-SA 4.0. Do not commit it.

Scoring the tool on this project does not need the data: the committed dataset in
`benchmarks/dataset/` already holds the answers. The data is only needed to rebuild that dataset
with dbt.

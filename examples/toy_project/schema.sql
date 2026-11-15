-- Source tables for the toy project. Synthetic data model, no real company schema.
-- Dialect: DuckDB.

CREATE TABLE customers (
    id      INTEGER,
    name    VARCHAR,
    country VARCHAR
);

CREATE TABLE products (
    id    INTEGER,
    name  VARCHAR,
    price DECIMAL(10, 2)
);

CREATE TABLE orders (
    id          INTEGER,
    customer_id INTEGER,
    status      VARCHAR,
    amount      DECIMAL(10, 2)
);

CREATE TABLE order_items (
    order_id   INTEGER,
    product_id INTEGER,
    quantity   INTEGER
);

"""Narrowing an edit to some columns must never hide a change that really happens.

`ColumnLogicChanged` says "only these columns of the model are computed differently". If that is
wrong the tool misses a silent change, the worst kind of error, so it is checked against data
instead of against the tool's own reasoning: a small project is built in DuckDB on fabricated rows
(nulls, a customer without orders, orders without a customer, a customer who is not in the other
table), once as it was and once with each edit, and what the edit really changed is compared with
what the tool says.

- the edited model: the rows it gives, leaving out the columns reported as changed, are identical.
  Nothing reported means all of it is identical, and a whole-model change allows any difference.
- every model that reads it: if its output differs, the tool lists it as affected.

The edits are chosen to be awkward: a changed column that is read by a join, a filter, a grouping,
an ORDER BY with LIMIT, or a window; one that is a pure refactor and so changes nothing.
"""

from pathlib import Path

import duckdb
import pytest

from project_edits import change_model_sql
from schema_impact.core.changes import ColumnLogicChanged, ModelChanged, diff_projects
from schema_impact.core.impact import compute_impact
from schema_impact.core.registry import Model, Project

SCHEMA = {
    "orders": {
        "id": "INTEGER",
        "customer_id": "INTEGER",
        "status": "VARCHAR",
        "amount": "DOUBLE",
    },
    "customers": {"id": "INTEGER", "name": "VARCHAR", "region": "VARCHAR"},
}

ORDERS = [
    (1, 1, "paid", 10.0),
    (2, 1, "paid", 20.0),
    (3, 2, "refunded", 5.0),
    (4, 2, "paid", None),
    (5, 3, "paid", 7.5),
    (6, 3, "paid", 7.5),
    (7, 4, "pending", 100.0),  # customer 4 is not in customers
    (8, None, "paid", 1.0),  # no customer
]
CUSTOMERS = [(1, "ann", "north"), (2, "bob", "south"), (3, "cy", "north"), (5, "eve", "south")]

MODELS = {
    "stg_orders": "SELECT id, customer_id, status, amount, amount * 0.2 AS tax FROM orders",
    "totals": (
        "SELECT customer_id, SUM(amount) AS total, COUNT(*) AS n FROM stg_orders "
        "GROUP BY customer_id"
    ),
    "report": (
        "WITH joined AS (SELECT c.name AS name, c.region AS region, t.total AS total, t.n AS n "
        "FROM customers AS c JOIN totals AS t ON c.id = t.customer_id) "
        "SELECT name, region, total, n FROM joined"
    ),
    "top": "SELECT name, total FROM report ORDER BY total DESC LIMIT 2",
    "ranks": "SELECT name, total, RANK() OVER (ORDER BY total DESC) AS r FROM report",
}

# (id, model, old text, new text): each replaces one piece of one model's SQL
EDITS = [
    ("stg-tax-rate", "stg_orders", "0.2", "0.3"),
    ("stg-upper-status", "stg_orders", "status,", "UPPER(status) AS status,"),
    ("stg-filter", "stg_orders", "FROM orders", "FROM orders WHERE status <> 'pending'"),
    ("stg-coalesce-amount", "stg_orders", "amount,", "COALESCE(amount, 0) AS amount,"),
    ("stg-comment", "stg_orders", "FROM orders", "FROM orders -- the raw table"),
    ("stg-round-amount", "stg_orders", "amount,", "ROUND(amount) AS amount,"),
    ("stg-null-customer", "stg_orders", "customer_id,", "COALESCE(customer_id, 0) AS customer_id,"),
    ("totals-avg", "totals", "SUM(amount)", "AVG(amount)"),
    ("totals-max", "totals", "SUM(amount)", "MAX(amount)"),
    ("totals-count-amount", "totals", "COUNT(*)", "COUNT(amount)"),
    ("totals-count-status", "totals", "COUNT(*)", "COUNT(DISTINCT status)"),
    (
        "totals-having",
        "totals",
        "GROUP BY customer_id",
        "GROUP BY customer_id HAVING SUM(amount) > 6",
    ),
    (
        "totals-having-count",
        "totals",
        "GROUP BY customer_id",
        "GROUP BY customer_id HAVING COUNT(*) > 1",
    ),
    ("totals-group-by-status", "totals", "GROUP BY customer_id", "GROUP BY customer_id, status"),
    ("report-rename-cte", "report", "joined", "merged"),
    ("report-spacing", "report", "SELECT name, region", "SELECT   name,   region"),
    ("report-cte-n", "report", "t.n AS n", "t.n * 10 AS n"),
    ("report-cte-total", "report", "t.total AS total", "t.total * 2 AS total"),
    ("report-cte-name", "report", "c.name AS name", "UPPER(c.name) AS name"),
    (
        "report-cte-filter",
        "report",
        "ON c.id = t.customer_id",
        "ON c.id = t.customer_id WHERE c.region = 'north'",
    ),
    ("report-left-join", "report", "JOIN totals", "LEFT JOIN totals"),
    ("report-right-join", "report", "JOIN totals", "RIGHT JOIN totals"),
    ("report-final-filter", "report", "FROM joined", "FROM joined WHERE total > 10"),
    ("report-final-n", "report", "n FROM joined", "n + 1 AS n FROM joined"),
    ("report-final-region", "report", "region, total", "LOWER(region) AS region, total"),
    ("top-ascending", "top", "DESC", "ASC"),
    ("top-limit", "top", "LIMIT 2", "LIMIT 3"),
    ("top-negated-total", "top", "SELECT name, total", "SELECT name, -total AS total"),
    ("top-scaled-total", "top", "SELECT name, total", "SELECT name, total * 2 AS total"),
    ("top-upper-name", "top", "SELECT name,", "SELECT UPPER(name) AS name,"),
    ("top-order-by-name", "top", "ORDER BY total DESC", "ORDER BY name"),
    ("ranks-ascending", "ranks", "ORDER BY total DESC", "ORDER BY total ASC"),
    ("ranks-dense", "ranks", "RANK()", "DENSE_RANK()"),
    ("ranks-scaled-total", "ranks", "SELECT name, total,", "SELECT name, total * 2 AS total,"),
    ("ranks-negated-total", "ranks", "SELECT name, total,", "SELECT name, -total AS total,"),
]


def project() -> Project:
    return Project(
        models={name: Model(name, sql, Path(f"{name}.sql")) for name, sql in MODELS.items()},
        schema=SCHEMA,
        dialect="duckdb",
    )


def build(project: Project) -> duckdb.DuckDBPyConnection:
    """The data and one view per model, in the order they are defined."""
    con = duckdb.connect()
    con.execute("SET threads = 1")
    con.execute(
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, status VARCHAR, amount DOUBLE)"
    )
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", ORDERS)
    con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, region VARCHAR)")
    con.executemany("INSERT INTO customers VALUES (?, ?, ?)", CUSTOMERS)
    for name, model in project.models.items():
        con.execute(f"CREATE VIEW {name} AS {model.sql}")
    return con


def rows(con: duckdb.DuckDBPyConnection, model: str, columns: list[str]) -> list[tuple]:
    """The rows of some columns of a model, as a sorted list (a multiset, no order)."""
    selected = ", ".join(columns)
    return sorted(con.execute(f"SELECT {selected} FROM {model}").fetchall(), key=repr)


def columns_of(con: duckdb.DuckDBPyConnection, model: str) -> list[str]:
    return [row[0] for row in con.execute(f"DESCRIBE {model}").fetchall()]


def edited(model: str, old: str, new: str) -> Project:
    before = project()
    sql = before.models[model].sql
    assert old in sql, f"{old!r} is not in {model}"
    return change_model_sql(before, model, sql.replace(old, new))


@pytest.mark.parametrize(("model", "old", "new"), [e[1:] for e in EDITS], ids=[e[0] for e in EDITS])
def test_what_really_changed_is_always_reported(model, old, new):
    before, after = project(), edited(model, old, new)
    changes = diff_projects(before, after)
    impacts = compute_impact(after, changes)
    old_db, new_db = build(before), build(after)

    columns = columns_of(old_db, model)
    assert columns == columns_of(new_db, model), "these edits keep the output columns"
    if ModelChanged(model) not in changes:
        reported = {c.column for c in changes if isinstance(c, ColumnLogicChanged)}
        assert reported <= set(columns)
        unchanged = [c for c in columns if c not in reported]
        if unchanged:
            assert rows(old_db, model, unchanged) == rows(new_db, model, unchanged), (
                f"{model}: {unchanged} changed but only {sorted(reported)} was reported"
            )

    affected = {i.model for i in impacts}
    for downstream in MODELS:
        if downstream == model:
            continue
        cols = columns_of(old_db, downstream)
        if rows(old_db, downstream, cols) != rows(new_db, downstream, cols):
            assert downstream in affected, f"{downstream} changed but the tool did not flag it"


def test_the_edits_are_not_all_trivial():
    """The check above is only worth something if edits really change data and some are narrowed."""
    narrowed = whole = nothing = really_changed = 0
    for _, model, old, new in EDITS:
        before, after = project(), edited(model, old, new)
        changes = diff_projects(before, after)
        narrowed += any(isinstance(c, ColumnLogicChanged) for c in changes)
        whole += ModelChanged(model) in changes
        nothing += not changes
        old_db, new_db = build(before), build(after)
        columns = columns_of(old_db, model)
        really_changed += rows(old_db, model, columns) != rows(new_db, model, columns)
    assert narrowed >= 8
    assert whole >= 8
    assert nothing >= 2
    assert really_changed >= 20

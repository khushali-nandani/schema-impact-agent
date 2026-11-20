"""Compare one table or view between two DuckDB files, as returned (not as defined).

Used by the ground truth: a model whose output is not the same after a mutation changed
silently. Needs `duckdb` (the bench dependency group).
"""

from pathlib import Path

import duckdb


def outputs_changed(before_db: str | Path, after_db: str | Path, table: str) -> bool:
    """True if `table` returns different columns or rows in the two databases.

    Columns are compared by name, type and order. Rows are compared as a multiset, so their
    order does not matter but a duplicated row does.

    Raises ValueError if the table is missing from either database.
    """
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH {_literal(before_db)} AS before_db (READ_ONLY)")
        con.execute(f"ATTACH {_literal(after_db)} AS after_db (READ_ONLY)")

        before_columns = _columns(con, "before_db", table)
        after_columns = _columns(con, "after_db", table)
        if not before_columns or not after_columns:
            side = "before" if not before_columns else "after"
            raise ValueError(f"Table {table} not found in the {side} database")
        if before_columns != after_columns:
            return True

        name = _identifier(table)
        before_rows, after_rows = f"before_db.main.{name}", f"after_db.main.{name}"
        differing = con.execute(
            f"SELECT count(*) FROM ("
            f"(SELECT * FROM {before_rows} EXCEPT ALL SELECT * FROM {after_rows}) UNION ALL "
            f"(SELECT * FROM {after_rows} EXCEPT ALL SELECT * FROM {before_rows}))"
        ).fetchone()
        return differing[0] > 0
    finally:
        con.close()


def _columns(con: duckdb.DuckDBPyConnection, catalog: str, table: str) -> list[tuple[str, str]]:
    return con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_catalog = ? AND table_schema = 'main' AND table_name = ? "
        "ORDER BY ordinal_position",
        [catalog, table],
    ).fetchall()


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(path: str | Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"

from schema_impact.core.lineage import ColumnRef, build_lineage


def edges_of(project, kind=None):
    return {
        (e.source, e.target.column, e.kind)
        for e in build_lineage(project)
        if kind is None or e.kind == kind
    }


def col(table: str, column: str) -> ColumnRef:
    return ColumnRef(table, column)


# --- SELECT * ---------------------------------------------------------------


def test_select_star_expands_to_every_column(make_project):
    project = make_project("SELECT * FROM customers")
    assert edges_of(project, "select") == {
        (col("customers", "id"), "id", "select"),
        (col("customers", "name"), "name", "select"),
        (col("customers", "country"), "country", "select"),
    }


def test_select_star_with_a_join_keeps_each_table(make_project):
    project = make_project(
        "SELECT o.* FROM orders AS o JOIN customers AS c ON o.customer_id = c.id"
    )
    tables = {e.source.table for e in build_lineage(project) if e.kind == "select"}
    assert tables == {"orders"}

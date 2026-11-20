from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from schema_impact.evals.tablediff import outputs_changed  # noqa: E402

pytestmark = pytest.mark.bench


def database(path: Path, *statements: str) -> Path:
    con = duckdb.connect(str(path))
    for statement in statements:
        con.execute(statement)
    con.close()
    return path


@pytest.fixture
def make(tmp_path):
    """Two database files, each with the statements it is given."""

    def build(before: list[str], after: list[str]) -> tuple[Path, Path]:
        return database(tmp_path / "before.duckdb", *before), database(
            tmp_path / "after.duckdb", *after
        )

    return build


T = "create table t as select * from (values (1, 'a'), (2, 'b')) v(id, name)"


def test_identical_tables_are_unchanged(make):
    before, after = make([T], [T])
    assert outputs_changed(before, after, "t") is False


def test_row_order_does_not_matter(make):
    reordered = "create table t as select * from (values (2, 'b'), (1, 'a')) v(id, name)"
    before, after = make([T], [reordered])
    assert outputs_changed(before, after, "t") is False


def test_a_changed_value_is_a_change(make):
    other = "create table t as select * from (values (1, 'a'), (2, 'X')) v(id, name)"
    before, after = make([T], [other])
    assert outputs_changed(before, after, "t") is True


def test_a_missing_row_is_a_change(make):
    fewer = "create table t as select * from (values (1, 'a')) v(id, name)"
    before, after = make([T], [fewer])
    assert outputs_changed(before, after, "t") is True


def test_an_extra_row_is_a_change(make):
    more = "create table t as select * from (values (1, 'a'), (2, 'b'), (3, 'c')) v(id, name)"
    before, after = make([T], [more])
    assert outputs_changed(before, after, "t") is True


def test_a_duplicated_row_is_a_change(make):
    twice = "create table t as select * from (values (1, 'a'), (2, 'b'), (2, 'b')) v(id, name)"
    before, after = make([T], [twice])
    assert outputs_changed(before, after, "t") is True


def test_a_renamed_column_is_a_change(make):
    renamed = "create table t as select * from (values (1, 'a'), (2, 'b')) v(id, label)"
    before, after = make([T], [renamed])
    assert outputs_changed(before, after, "t") is True


def test_a_dropped_column_is_a_change(make):
    dropped = "create table t as select * from (values (1), (2)) v(id)"
    before, after = make([T], [dropped])
    assert outputs_changed(before, after, "t") is True


def test_a_changed_column_type_is_a_change(make):
    typed = "create table t as select * from (values ('1', 'a'), ('2', 'b')) v(id, name)"
    before, after = make([T], [typed])
    assert outputs_changed(before, after, "t") is True


def test_a_changed_column_order_is_a_change(make):
    swapped = "create table t as select * from (values ('a', 1), ('b', 2)) v(name, id)"
    before, after = make([T], [swapped])
    assert outputs_changed(before, after, "t") is True


def test_views_are_compared_by_what_they_return(make):
    view = "create view v as select id from t"
    before, after = make([T, view], [T, view])
    assert outputs_changed(before, after, "v") is False


def test_a_table_missing_from_one_side_is_an_error_not_a_guess(make):
    before, after = make([T], ["create table other as select 1 as x"])
    with pytest.raises(ValueError, match="t"):
        outputs_changed(before, after, "t")

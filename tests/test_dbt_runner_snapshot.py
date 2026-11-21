"""Comparing a model before and after a mutation when the model is a view over a CSV file.

In dbt-duckdb a model that reads a CSV source is a view whose SQL holds the relative path
(`data/orders.csv`). DuckDB looks that path up when the view is queried, in the folder the process
runs in, so two databases cannot be compared directly: both views would read the same file, or
none. The runner therefore copies the rows of every built model into a plain `snapshot.db` while
it is in the project folder.
"""

import subprocess
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from schema_impact.evals.dbt_runner import SNAPSHOT_FILE, _snapshot  # noqa: E402
from schema_impact.evals.tablediff import outputs_changed  # noqa: E402


def make_project(root: Path, csv: str) -> Path:
    """A project folder with data/x.csv and a database holding a view over it and a table."""
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.csv").write_text(csv)
    # create the objects from inside the folder, like dbt does, so the view keeps the relative path
    script = (
        "import duckdb\n"
        "con = duckdb.connect('p.duckdb')\n"
        "con.execute(\"create view v as select * from 'data/x.csv'\")\n"
        "con.execute('create table t as select 1 as n')\n"
    )
    subprocess.run([sys.executable, "-I", "-c", script], cwd=root, check=True)
    return root


def rows(database: Path, table: str) -> list[tuple]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        return con.execute(f'select * from main."{table}" order by 1').fetchall()
    finally:
        con.close()


def test_the_snapshot_holds_the_rows_a_view_returns(tmp_path, monkeypatch):
    root = make_project(tmp_path / "p", "id,v\n1,a\n2,b\n")
    monkeypatch.chdir(tmp_path)  # not the project folder, as when the runner compares
    snapshot = _snapshot(root, ["v", "t"])
    assert snapshot == root / SNAPSHOT_FILE
    assert rows(snapshot, "v") == [(1, "a"), (2, "b")]
    assert rows(snapshot, "t") == [(1,)]


def test_only_the_named_models_are_in_the_snapshot(tmp_path, monkeypatch):
    root = make_project(tmp_path / "p", "id,v\n1,a\n")
    monkeypatch.chdir(tmp_path)
    snapshot = _snapshot(root, ["v"])
    con = duckdb.connect(str(snapshot), read_only=True)
    try:
        tables = con.execute("select table_name from information_schema.tables").fetchall()
        names = {row[0] for row in tables}
    finally:
        con.close()
    assert names == {"v"}


def test_each_snapshot_reads_the_csv_of_its_own_project(tmp_path, monkeypatch):
    before = _snapshot_of(tmp_path / "before", "id,v\n1,a\n2,b\n", monkeypatch)
    after = _snapshot_of(tmp_path / "after", "id,v\n1,a\n2,CHANGED\n", monkeypatch)
    assert outputs_changed(before, after, "v") is True


def test_an_unchanged_csv_gives_equal_snapshots(tmp_path, monkeypatch):
    before = _snapshot_of(tmp_path / "before", "id,v\n1,a\n", monkeypatch)
    after = _snapshot_of(tmp_path / "after", "id,v\n1,a\n", monkeypatch)
    assert outputs_changed(before, after, "v") is False


def _snapshot_of(root: Path, csv: str, monkeypatch) -> Path:
    make_project(root, csv)
    monkeypatch.chdir(root.parent)
    return _snapshot(root, ["v"])

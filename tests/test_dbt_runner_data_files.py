"""How the runner makes a copy of a dbt project for one case, without running dbt.

A project like Olist has 120 MB of CSV files in `data/`. Copying them for every case would be
slow, so the copies share them. The danger of sharing is that a mutation which edits a CSV in the
copy would edit the original, so editing must replace the file and never write through the link.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

from schema_impact.evals.dbt_runner import DbtError, _copy_project, _needs_seed
from schema_impact.evals.mutations import (
    SEED_DROP_COLUMN,
    SEED_RENAME_COLUMN,
    Mutation,
    apply_mutation,
)

ORDERS = 'order_id,status\no1,paid\no2,"two\nlines"\n'
ITEMS = "order_id,price\no1,1.5\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(tmp_path) -> Path:
    root = tmp_path / "source"
    (root / "models").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "dbt_project.yml").write_text("name: p\n")
    (root / "models" / "m.sql").write_text("select 1 as x\n")
    (root / "data" / "orders.csv").write_text(ORDERS)
    (root / "data" / "items.csv").write_text(ITEMS)
    return root


@pytest.fixture
def copy(source, tmp_path) -> Path:
    destination = tmp_path / "copy"
    _copy_project(source, destination)
    return destination


def edit(kind: str, path: str, **params) -> Mutation:
    return Mutation(f"{kind}:x", kind, "t", "d", {"path": path, **params})


# --- the data files are shared ---------------------------------------------------


def test_data_files_are_links_to_the_originals_not_copies(source, copy):
    for name in ("orders.csv", "items.csv"):
        assert (copy / "data" / name).is_symlink()
        assert (copy / "data" / name).resolve() == (source / "data" / name).resolve()


def test_the_data_folder_itself_is_a_real_folder(copy):
    assert (copy / "data").is_dir() and not (copy / "data").is_symlink()


def test_everything_else_is_a_real_copy(source, copy):
    for name in ("dbt_project.yml", "models/m.sql"):
        assert not (copy / name).is_symlink()
        assert (copy / name).read_text() == (source / name).read_text()


def test_removing_a_copy_leaves_the_original_files(source, copy):
    before = {p.name: sha256(p) for p in (source / "data").glob("*.csv")}
    shutil.rmtree(copy)
    assert {p.name: sha256(p) for p in (source / "data").glob("*.csv")} == before


# --- a mutation never writes through the link -------------------------------------------


def test_renaming_a_column_changes_the_copy_and_not_the_original(source, copy):
    original = sha256(source / "data" / "orders.csv")
    apply_mutation(
        edit(SEED_RENAME_COLUMN, "data/orders.csv", column="status", new_name="state"), copy
    )
    assert sha256(source / "data" / "orders.csv") == original
    changed = copy / "data" / "orders.csv"
    assert not changed.is_symlink()
    assert changed.read_text().splitlines()[0] == "order_id,state"


def test_dropping_a_column_changes_the_copy_and_not_the_original(source, copy):
    original = sha256(source / "data" / "orders.csv")
    apply_mutation(edit(SEED_DROP_COLUMN, "data/orders.csv", column="status"), copy)
    assert sha256(source / "data" / "orders.csv") == original
    assert not (copy / "data" / "orders.csv").is_symlink()
    assert "status" not in (copy / "data" / "orders.csv").read_text()


def test_the_files_that_were_not_edited_are_still_links(copy):
    apply_mutation(
        edit(SEED_RENAME_COLUMN, "data/orders.csv", column="status", new_name="state"), copy
    )
    assert (copy / "data" / "items.csv").is_symlink()


# --- dbt packages ---------------------------------------------------------------------------


def test_installed_dbt_packages_are_copied(source, tmp_path):
    (source / "packages.yml").write_text("packages: []\n")
    (source / "dbt_packages" / "dbt_utils").mkdir(parents=True)
    (source / "dbt_packages" / "dbt_utils" / "dbt_project.yml").write_text("name: dbt_utils\n")
    destination = tmp_path / "copy"
    _copy_project(source, destination)
    assert (destination / "dbt_packages" / "dbt_utils" / "dbt_project.yml").is_file()


def test_packages_that_were_never_installed_are_reported(source, tmp_path):
    (source / "packages.yml").write_text("packages: []\n")
    with pytest.raises(DbtError, match="dbt deps"):
        _copy_project(source, tmp_path / "copy")


def test_a_project_without_packages_needs_no_dbt_packages_folder(copy):
    assert not (copy / "dbt_packages").exists()


# --- build products are not copied -----------------------------------------------------------


def test_build_products_are_not_copied(source, tmp_path):
    for name in ("target", "logs"):
        (source / name).mkdir()
    (source / "p.duckdb").write_text("")
    (source / "p.duckdb.wal").write_text("")
    (source / ".user.yml").write_text("id: x\n")
    destination = tmp_path / "copy"
    _copy_project(source, destination)
    assert sorted(p.name for p in destination.iterdir()) == ["data", "dbt_project.yml", "models"]


# --- dbt seed only when there is something to seed ----------------------------------------------


def test_a_project_without_seeds_is_not_seeded(source):
    assert not _needs_seed(source)


def test_a_seeds_folder_with_only_properties_is_not_seeded(source):
    (source / "seeds").mkdir()
    (source / "seeds" / "_props.yml").write_text("version: 2\n")
    assert not _needs_seed(source)


def test_a_seed_csv_means_dbt_seed_is_run(source):
    (source / "seeds").mkdir()
    (source / "seeds" / "raw").mkdir()
    (source / "seeds" / "raw" / "orders.csv").write_text("id\n1\n")
    assert _needs_seed(source)

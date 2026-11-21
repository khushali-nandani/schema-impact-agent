"""The runner on the real Olist project. Slow: `uv run pytest -m bench`.

Needs the Olist CSV files in benchmarks/olist/data (see its DATA.md) and `dbt deps` run there.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

from schema_impact.evals.dbt_runner import run_baseline, run_case
from schema_impact.evals.mutations import (
    MODEL_DROP_COLUMN,
    MODEL_LOGIC,
    MODEL_RENAME_COLUMN,
    SOURCE_CHANGE_TYPE,
    SOURCE_DROP_COLUMN,
    SOURCE_RENAME_COLUMN,
    Recipe,
    _rewrite_csv,
    generate_mutations,
)
from schema_impact.evals.olist_recipes import OLIST_RECIPES

pytestmark = pytest.mark.bench

OLIST = Path(__file__).resolve().parent.parent / "benchmarks" / "olist"

UPPER_STATUS = Recipe(
    "stg_orders.upper_status",
    "stg_olist__orders",
    "Upper-case order_status",
    "            order_status,",
    "            upper(order_status) as order_status,",
)


def csv_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((OLIST / "data").glob("*.csv"))
    }


@pytest.fixture(scope="module", autouse=True)
def olist_is_ready():
    if len(csv_hashes()) != 9 or not (OLIST / "dbt_packages").is_dir():
        pytest.skip("put the Olist CSV files in benchmarks/olist/data and run `dbt deps` there")


@pytest.fixture(scope="module")
def workdir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("olist_runs")


@pytest.fixture(scope="module")
def baseline(workdir):
    return run_baseline(OLIST, workdir)


@pytest.fixture(scope="module")
def mutations():
    return generate_mutations(OLIST, [UPPER_STATUS])


@pytest.fixture(scope="module")
def case_and_hashes(baseline, mutations, workdir):
    before = csv_hashes()
    mutation = next(m for m in mutations if m.id.endswith(UPPER_STATUS.id))
    result = run_case(OLIST, baseline, mutation, workdir)
    return result, before


def test_the_unmutated_project_builds_every_model(baseline):
    assert len(baseline.statuses) == 22
    assert set(baseline.statuses.values()) == {"success"}


def test_the_baseline_has_a_snapshot_of_the_models(baseline):
    assert baseline.database.is_file()


def test_model_column_mutations_are_generated_for_the_project(mutations):
    kinds = [m.kind for m in mutations]
    assert kinds.count(MODEL_RENAME_COLUMN) > 50
    assert kinds.count(MODEL_DROP_COLUMN) > 50


def test_a_change_of_logic_is_run_and_every_scored_model_is_labelled(case_and_hashes):
    case, _ = case_and_hashes
    assert case.error is None
    assert set(case.labels) == set(case.statuses) - {"stg_olist__orders"}


def test_upper_casing_the_status_changes_the_orders_fact_silently(case_and_hashes):
    case, _ = case_and_hashes
    assert case.labels["fct_orders"] == "silent"  # it reads the status; the CSV is the same
    assert case.labels["dim_sellers"] == "safe"  # unrelated to orders


def test_the_original_csv_files_are_not_touched(case_and_hashes):
    _, before = case_and_hashes
    assert csv_hashes() == before


def test_the_case_folder_is_removed_afterwards(case_and_hashes, workdir):
    assert [p.name for p in workdir.iterdir()] == ["baseline"]


# --- the mutations of the sources and the recipes -------------------------------------------------


@pytest.fixture(scope="module")
def all_mutations():
    return generate_mutations(OLIST, OLIST_RECIPES)


def test_every_recipe_applies_to_the_project(all_mutations):
    logic = [m for m in all_mutations if m.kind == MODEL_LOGIC]
    assert len(logic) == len(OLIST_RECIPES) >= 20


def test_every_source_column_has_a_rename_and_a_drop(all_mutations):
    kinds = [m.kind for m in all_mutations]
    assert kinds.count(SOURCE_RENAME_COLUMN) == kinds.count(SOURCE_DROP_COLUMN) == 52
    assert kinds.count(SOURCE_CHANGE_TYPE) > 0


def test_the_ids_of_all_mutations_are_unique(all_mutations):
    ids = [m.id for m in all_mutations]
    assert len(ids) == len(set(ids))


def test_rewriting_a_csv_file_without_a_change_gives_the_same_bytes(tmp_path):
    """The check that a dropped column is the only difference: any other byte that moved would
    show up as a model whose output changed for the wrong reason."""
    for original in sorted((OLIST / "data").glob("*.csv")):
        copy = tmp_path / original.name
        shutil.copyfile(original, copy)
        _rewrite_csv(copy, set())
        assert copy.read_bytes() == original.read_bytes(), original.name


def case_for(baseline, all_mutations, workdir, mutation_id):
    mutation = next(m for m in all_mutations if m.id == mutation_id)
    return run_case(OLIST, baseline, mutation, workdir)


def test_renaming_a_source_column_breaks_its_staging_model_and_what_reads_it(
    baseline, all_mutations, workdir
):
    case = case_for(
        baseline, all_mutations, workdir, "source_rename_column:olist__orders.order_status"
    )
    assert case.error is None
    assert case.labels["stg_olist__orders"] == "breaking"
    assert case.labels["fct_orders"] == "breaking"  # skipped because its input failed
    assert case.labels["dim_sellers"] == "safe"
    assert case.after.schema["olist__orders"].keys() >= {"order_status_v2"}


def test_dropping_a_source_column_breaks_its_staging_model(baseline, all_mutations, workdir):
    case = case_for(
        baseline, all_mutations, workdir, "source_drop_column:olist__sellers.seller_city"
    )
    assert case.error is None
    assert case.labels["stg_olist__sellers"] == "breaking"
    assert "seller_city" not in case.after.schema["olist__sellers"]


def test_reading_a_column_as_varchar_shows_up_in_the_schema_of_the_project(
    baseline, all_mutations, workdir
):
    case = case_for(baseline, all_mutations, workdir, "source_change_type:olist__order_items.price")
    assert case.error is None
    assert case.after.schema["olist__order_items"]["price"] == "VARCHAR"
    assert baseline.project.schema["olist__order_items"]["price"] == "DOUBLE"
    assert set(case.labels) == set(case.statuses)  # nothing is left out: no model is edited


def test_the_original_csv_files_are_still_untouched_after_all_of_it(all_mutations):
    assert len(csv_hashes()) == 9


def test_building_the_unmutated_project_twice_gives_the_same_output(baseline, workdir):
    """customer_rfm does not, with DuckDB on several threads: see test_dbt_runner_threads.py."""
    from schema_impact.evals.tablediff import outputs_changed

    again = run_baseline(OLIST, workdir / "again")
    differing = [
        model
        for model in baseline.statuses
        if outputs_changed(baseline.database, again.database, model)
    ]
    assert differing == []

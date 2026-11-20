"""The runner on the real Olist project. Slow: `uv run pytest -m bench`.

Needs the Olist CSV files in benchmarks/olist/data (see its DATA.md) and `dbt deps` run there.
"""

import hashlib
from pathlib import Path

import pytest

from schema_impact.evals.dbt_runner import run_baseline, run_case
from schema_impact.evals.mutations import (
    MODEL_DROP_COLUMN,
    MODEL_RENAME_COLUMN,
    Recipe,
    generate_mutations,
)

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

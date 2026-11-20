"""Run real dbt on mutated copies of jaffle_shop. Slow: `uv run pytest -m bench`."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("dbt")

from schema_impact.evals.dbt_runner import run_baseline, run_case  # noqa: E402

from schema_impact.evals.groundtruth import BREAKING, SAFE, SILENT  # noqa: E402
from schema_impact.evals.jaffle_recipes import JAFFLE_RECIPES  # noqa: E402
from schema_impact.evals.mutations import generate_mutations  # noqa: E402

pytestmark = pytest.mark.bench

REPO = Path(__file__).resolve().parent.parent
JAFFLE = REPO / "benchmarks" / "jaffle_shop"
MODELS = {"customers", "orders", "stg_customers", "stg_orders", "stg_payments"}


@pytest.fixture(scope="module")
def workdir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("groundtruth")


@pytest.fixture(scope="module")
def baseline(workdir):
    return run_baseline(JAFFLE, workdir)


@pytest.fixture(scope="module")
def mutations(workdir):
    root = workdir / "for_generation"
    shutil.copytree(JAFFLE, root)
    shutil.copytree(REPO / "tests" / "fixtures" / "jaffle_shop_dbt" / "target", root / "target")
    return {m.id: m for m in generate_mutations(root, JAFFLE_RECIPES)}


def case(baseline, mutations, workdir, mutation_id):
    return run_case(JAFFLE, baseline, mutations[mutation_id], workdir)


# --- the baseline -----------------------------------------------------------


def test_the_unmutated_project_builds_every_model(baseline):
    assert set(baseline.statuses) == MODELS
    assert set(baseline.statuses.values()) == {"success"}


def test_the_baseline_project_is_read_back_from_dbt_artifacts(baseline):
    assert set(baseline.project.models) == MODELS
    assert set(baseline.project.schema) == {"raw_customers", "raw_orders", "raw_payments"}
    assert baseline.database.exists()


# --- cases ------------------------------------------------------------------


def test_a_renamed_seed_column_breaks_everything_that_depends_on_it(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "seed_rename_column:raw_orders.user_id")
    assert result.error is None
    assert result.labels == {
        "stg_orders": BREAKING,
        "customers": BREAKING,
        "orders": BREAKING,
        "stg_customers": SAFE,
        "stg_payments": SAFE,
    }


def test_a_change_of_logic_that_changes_numbers_is_silent_downstream(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "model_logic:stg_payments.divide_by_1000")
    assert result.error is None
    # stg_payments is the edited model, so it is not scored
    assert result.labels == {
        "customers": SILENT,
        "orders": SILENT,
        "stg_customers": SAFE,
        "stg_orders": SAFE,
    }


def test_a_pure_refactor_is_safe_everywhere(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "model_logic:customers.rename_cte")
    assert result.error is None
    assert set(result.labels.values()) == {SAFE}


def test_dropping_a_column_nobody_reads_affects_no_other_model(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "model_drop_column:customers.first_name")
    assert result.error is None
    assert set(result.labels.values()) == {SAFE}


def test_a_type_change_labels_every_scored_model(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "seed_change_type:raw_orders.id")
    assert result.error is None
    assert set(result.labels) == MODELS
    assert set(result.labels.values()) <= {BREAKING, SILENT, SAFE}


def test_the_project_after_the_mutation_is_kept_for_scoring(baseline, mutations, workdir):
    seed = case(baseline, mutations, workdir, "seed_rename_column:raw_orders.user_id")
    assert "user_id_v2" in seed.after.schema["raw_orders"]
    assert "user_id" not in seed.after.schema["raw_orders"]

    logic = case(baseline, mutations, workdir, "model_logic:stg_payments.divide_by_1000")
    assert "1000" in logic.after.models["stg_payments"].sql


def test_a_failed_model_still_has_its_compiled_sql_after_the_mutation(baseline, mutations, workdir):
    result = case(baseline, mutations, workdir, "seed_rename_column:raw_orders.user_id")
    assert result.statuses["orders"] == "skipped"
    assert "orders" in result.after.models


def test_the_baseline_is_not_disturbed_by_cases(baseline, mutations, workdir):
    case(baseline, mutations, workdir, "seed_drop_column:raw_payments.amount")
    assert baseline.database.exists()
    assert set(baseline.statuses.values()) == {"success"}

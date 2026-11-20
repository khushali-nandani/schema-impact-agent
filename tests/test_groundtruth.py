import pytest

from schema_impact.evals.groundtruth import (
    BREAKING,
    SAFE,
    SILENT,
    label_model,
    mutated_model,
    scored_models,
    statuses_from_run_results,
)
from schema_impact.evals.mutations import Mutation

MODELS = ["customers", "orders", "stg_customers", "stg_orders", "stg_payments"]


def mutation(kind: str, target: str) -> Mutation:
    return Mutation(id=f"{kind}:{target}", kind=kind, target=target, description="d", params={})


# --- the label of one model --------------------------------------------------


@pytest.mark.parametrize("changed", [True, False])
def test_a_model_that_failed_is_breaking_whatever_its_output(changed):
    assert label_model("error", output_changed=changed) == BREAKING


@pytest.mark.parametrize("changed", [True, False])
def test_a_model_skipped_because_something_upstream_failed_is_breaking(changed):
    assert label_model("skipped", output_changed=changed) == BREAKING


def test_a_model_that_built_with_a_different_output_is_silent():
    assert label_model("success", output_changed=True) == SILENT


def test_a_model_that_built_with_the_same_output_is_safe():
    assert label_model("success", output_changed=False) == SAFE


def test_an_unknown_status_is_not_guessed():
    with pytest.raises(ValueError, match="no-op"):
        label_model("no-op", output_changed=False)


# --- reading dbt's run_results.json -------------------------------------------


def test_statuses_are_read_per_model_and_other_nodes_are_ignored():
    # the shape of a real run_results.json: results[].unique_id and .status
    run_results = {
        "metadata": {},
        "results": [
            {"unique_id": "model.jaffle_shop.stg_orders", "status": "error", "message": "boom"},
            {"unique_id": "model.jaffle_shop.orders", "status": "skipped"},
            {"unique_id": "model.jaffle_shop.stg_customers", "status": "success"},
            {"unique_id": "seed.jaffle_shop.raw_orders", "status": "success"},
            {"unique_id": "test.jaffle_shop.unique_orders_order_id.fed79b3a6e", "status": "pass"},
        ],
    }
    assert statuses_from_run_results(run_results) == {
        "stg_orders": "error",
        "orders": "skipped",
        "stg_customers": "success",
    }


def test_no_results_means_no_statuses():
    assert statuses_from_run_results({"results": []}) == {}


# --- which models a mutation is scored on -------------------------------------


def test_a_seed_mutation_is_scored_on_every_model():
    assert scored_models(mutation("seed_drop_column", "raw_orders.id"), MODELS) == MODELS


@pytest.mark.parametrize(
    ("kind", "target", "model"),
    [
        ("model_rename_column", "stg_orders.customer_id", "stg_orders"),
        ("model_drop_column", "customers.first_name", "customers"),
        ("model_logic", "orders", "orders"),
    ],
)
def test_a_model_mutation_is_not_scored_on_the_model_it_edits(kind, target, model):
    # that model's output changes by construction: it is the change, not an impact of it
    assert mutated_model(mutation(kind, target)) == model
    assert scored_models(mutation(kind, target), MODELS) == [m for m in MODELS if m != model]


def test_a_seed_mutation_edits_no_model():
    assert mutated_model(mutation("seed_change_type", "raw_orders.id")) is None

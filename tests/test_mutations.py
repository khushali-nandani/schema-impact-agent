import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from schema_impact.evals.jaffle_recipes import JAFFLE_RECIPES
from schema_impact.evals.mutations import (
    Mutation,
    MutationError,
    Recipe,
    apply_mutation,
    generate_mutations,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def jaffle(tmp_path) -> Path:
    """A copy of the vendored jaffle_shop with the dbt artifacts of a real run next to it."""
    root = tmp_path / "jaffle_shop"
    shutil.copytree(REPO / "benchmarks" / "jaffle_shop", root)
    shutil.copytree(REPO / "tests" / "fixtures" / "jaffle_shop_dbt" / "target", root / "target")
    return root


@pytest.fixture
def mutations(jaffle):
    return generate_mutations(jaffle, JAFFLE_RECIPES)


def find(mutations: list[Mutation], mutation_id: str) -> Mutation:
    return next(m for m in mutations if m.id == mutation_id)


def snapshot(root: Path) -> dict[str, str]:
    """A hash of every source file, to see whether a mutation changed anything."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "target" not in path.relative_to(root).parts
    }


# --- what gets generated ----------------------------------------------------


def test_there_are_more_than_a_hundred_mutations(mutations):
    assert len(mutations) >= 100


def test_the_number_of_mutations_per_kind(mutations):
    assert Counter(m.kind for m in mutations) == {
        "seed_rename_column": 11,
        "seed_drop_column": 11,
        "seed_change_type": 7,
        "model_rename_column": 27,
        "model_drop_column": 27,
        "model_logic": 20,
    }


def test_ids_are_unique_and_the_same_on_every_run(jaffle, mutations):
    ids = [m.id for m in mutations]
    assert len(ids) == len(set(ids))
    assert ids == [m.id for m in generate_mutations(jaffle, JAFFLE_RECIPES)]


def test_a_mutation_survives_a_round_trip_through_json(mutations):
    for mutation in mutations:
        assert Mutation.from_dict(json.loads(json.dumps(mutation.to_dict()))) == mutation


def test_every_mutation_has_a_readable_description(mutations):
    assert all(m.description for m in mutations)


# --- applying seed mutations -------------------------------------------------


def read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="") as handle:
        return list(csv.reader(handle))


def test_renaming_a_seed_column_changes_only_the_header(jaffle, mutations):
    before = read_csv(jaffle / "seeds" / "raw_orders.csv")
    apply_mutation(find(mutations, "seed_rename_column:raw_orders.user_id"), jaffle)
    after = read_csv(jaffle / "seeds" / "raw_orders.csv")
    assert after[0] == ["id", "user_id_v2", "order_date", "status"]
    assert after[1:] == before[1:]


def test_dropping_a_seed_column_removes_it_from_every_row(jaffle, mutations):
    before = read_csv(jaffle / "seeds" / "raw_orders.csv")
    apply_mutation(find(mutations, "seed_drop_column:raw_orders.user_id"), jaffle)
    after = read_csv(jaffle / "seeds" / "raw_orders.csv")
    assert after[0] == ["id", "order_date", "status"]
    assert len(after) == len(before)
    assert all(len(row) == 3 for row in after)
    assert [row[0] for row in after] == [row[0] for row in before]


def test_changing_a_seed_type_writes_a_seed_properties_file(jaffle, mutations):
    apply_mutation(find(mutations, "seed_change_type:raw_orders.user_id"), jaffle)
    text = (jaffle / "seeds" / "_mutation_types.yml").read_text()
    assert "name: raw_orders" in text
    assert "user_id: varchar" in text


def test_text_columns_get_no_type_change(mutations):
    assert not [m for m in mutations if m.id == "seed_change_type:raw_orders.status"]


# --- applying model mutations ------------------------------------------------


def test_renaming_a_model_column_rewrites_the_final_select(jaffle, mutations):
    apply_mutation(find(mutations, "model_rename_column:stg_orders.customer_id"), jaffle)
    text = (jaffle / "models" / "staging" / "stg_orders.sql").read_text()
    assert text.rstrip().endswith(
        "select order_id, customer_id as customer_id_v2, order_date, status from renamed"
    )


def test_dropping_a_model_column_leaves_it_out_of_the_final_select(jaffle, mutations):
    apply_mutation(find(mutations, "model_drop_column:stg_orders.status"), jaffle)
    text = (jaffle / "models" / "staging" / "stg_orders.sql").read_text()
    assert text.rstrip().endswith("select order_id, customer_id, order_date from renamed")


def test_a_model_that_does_not_end_in_a_star_select_gets_no_column_mutations(jaffle):
    path = jaffle / "models" / "staging" / "stg_orders.sql"
    path.write_text(
        path.read_text().rstrip().replace("select * from renamed", "select order_id from renamed")
    )
    targets = {m.target for m in generate_mutations(jaffle, JAFFLE_RECIPES)}
    assert not [t for t in targets if t.startswith("stg_orders.")]


# --- applying logic recipes --------------------------------------------------


def test_a_logic_recipe_edits_the_model_source(jaffle, mutations):
    apply_mutation(find(mutations, "model_logic:stg_orders.drop_returned_orders"), jaffle)
    text = (jaffle / "models" / "staging" / "stg_orders.sql").read_text()
    assert "where status != 'returned'" in text


def test_a_recipe_that_replaces_every_occurrence(jaffle, mutations):
    path = jaffle / "models" / "customers.sql"
    count = path.read_text().count("customer_orders")
    assert count > 1
    apply_mutation(find(mutations, "model_logic:customers.rename_cte"), jaffle)
    text = path.read_text()
    assert "customer_orders" not in text
    assert text.count("cust_orders") == count


# --- safety -----------------------------------------------------------------


def test_every_mutation_applies_and_changes_something(jaffle, mutations, tmp_path):
    for i, mutation in enumerate(mutations):
        copy = tmp_path / f"copy{i}"
        shutil.copytree(jaffle, copy)
        before = snapshot(copy)
        apply_mutation(mutation, copy)
        assert snapshot(copy) != before, mutation.id
        shutil.rmtree(copy)


def test_applying_never_touches_the_original_project_files(jaffle, mutations, tmp_path):
    original = snapshot(REPO / "benchmarks" / "jaffle_shop")
    copy = tmp_path / "copy"
    shutil.copytree(jaffle, copy)
    for mutation in mutations[:10]:
        apply_mutation(mutation, copy)
    assert snapshot(REPO / "benchmarks" / "jaffle_shop") == original


def test_a_mutation_whose_text_is_not_there_raises(jaffle):
    mutation = Mutation(
        id="model_logic:x",
        kind="model_logic",
        target="stg_orders",
        description="x",
        params={
            "path": "models/staging/stg_orders.sql",
            "old": "text that is not in the file",
            "new": "y",
            "count": 1,
        },
    )
    with pytest.raises(MutationError, match="not found"):
        apply_mutation(mutation, jaffle)


def test_a_mutation_whose_text_is_ambiguous_raises(jaffle):
    mutation = Mutation(
        id="model_logic:x",
        kind="model_logic",
        target="customers",
        description="x",
        params={"path": "models/customers.sql", "old": "customer_orders", "new": "y", "count": 1},
    )
    with pytest.raises(MutationError, match="expected 1"):
        apply_mutation(mutation, jaffle)


def test_a_recipe_that_does_not_apply_stops_generation(jaffle):
    bad = Recipe("stg_orders.bad", "stg_orders", "never matches", "no such text", "y")
    with pytest.raises(MutationError, match="stg_orders.bad"):
        generate_mutations(jaffle, [bad])


def test_an_unknown_kind_raises(jaffle):
    mutation = Mutation("x", "no_such_kind", "t", "d", {})
    with pytest.raises(MutationError, match="no_such_kind"):
        apply_mutation(mutation, jaffle)

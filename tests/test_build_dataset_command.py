"""The command that builds a benchmark dataset knows both projects."""

from schema_impact.evals import build_dataset as command
from schema_impact.evals.jaffle_recipes import JAFFLE_RECIPES
from schema_impact.evals.olist_recipes import OLIST_RECIPES


def test_jaffle_shop_stays_the_default():
    benchmark = command.BENCHMARKS[command.DEFAULT_BENCHMARK]
    assert command.DEFAULT_BENCHMARK == "jaffle_shop"
    assert benchmark.project == command.REPO / "benchmarks" / "jaffle_shop"
    assert benchmark.out == command.REPO / "benchmarks" / "dataset" / "jaffle_shop.json"
    assert benchmark.recipes == JAFFLE_RECIPES


def test_olist_has_its_own_project_recipes_and_dataset():
    benchmark = command.BENCHMARKS["olist"]
    assert benchmark.project == command.REPO / "benchmarks" / "olist"
    assert benchmark.out == command.REPO / "benchmarks" / "dataset" / "olist.json"
    assert benchmark.recipes == OLIST_RECIPES

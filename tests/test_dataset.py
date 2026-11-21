import json
from pathlib import Path

import pytest

from schema_impact.core.registry import Model, Project
from schema_impact.evals.dataset import (
    build_dataset,
    case_from_dict,
    case_to_dict,
    load_dataset,
    project_from_dict,
    project_to_dict,
)
from schema_impact.evals.mutations import Mutation
from schema_impact.evals.results import Baseline, CaseResult


def project(sql: str = "select 1 as x") -> Project:
    return Project(
        models={"m": Model("m", sql, Path("models/m.sql"))},
        schema={"raw": {"id": "INTEGER", "name": "VARCHAR"}},
        dialect="duckdb",
    )


def mutation(n: int) -> Mutation:
    return Mutation(f"seed_drop_column:raw.c{n}", "seed_drop_column", f"raw.c{n}", "d", {"n": n})


def result(m: Mutation, error: str | None = None) -> CaseResult:
    if error:
        return CaseResult(m, {}, {}, None, error)
    return CaseResult(m, {"m": "success"}, {"m": "safe"}, project("select 2 as x"), None)


class FakeRunner:
    """Stands in for run_baseline and run_case, and records what it was asked."""

    def __init__(self, fail_on: str | None = None, errors: set[str] | None = None):
        self.cases: list[str] = []
        self.fail_on = fail_on
        self.errors = errors or set()

    def baseline(self, project_dir, workdir):
        return Baseline({"m": "success"}, project(), Path("unused.duckdb"))

    def case(self, project_dir, baseline, mutation, workdir):
        self.cases.append(mutation.id)
        if mutation.id == self.fail_on:
            raise RuntimeError("the machine fell over")
        return result(mutation, "boom" if mutation.id in self.errors else None)


def build(tmp_path, runner, mutations, **kwargs):
    return build_dataset(
        tmp_path / "project",
        tmp_path / "out" / "dataset.json",
        mutations=mutations,
        workdir=tmp_path / "work",
        run_baseline=runner.baseline,
        run_case=runner.case,
        progress=lambda line: None,
        **kwargs,
    )


# --- serialising ------------------------------------------------------------


def test_a_project_survives_a_round_trip_through_json():
    original = project()
    assert project_from_dict(json.loads(json.dumps(project_to_dict(original)))) == original


def test_a_case_survives_a_round_trip_through_json():
    for case in (result(mutation(1)), result(mutation(2), error="dbt fell over")):
        assert case_from_dict(json.loads(json.dumps(case_to_dict(case)))) == case


# --- building ---------------------------------------------------------------


def test_every_mutation_becomes_a_case_in_order(tmp_path):
    mutations = [mutation(n) for n in range(4)]
    build(tmp_path, FakeRunner(), mutations)
    dataset = load_dataset(tmp_path / "out" / "dataset.json")
    assert [c.mutation for c in dataset.cases] == mutations
    assert dataset.baseline_project == project()
    assert dataset.baseline_statuses == {"m": "success"}


def test_the_file_records_where_the_data_came_from(tmp_path):
    build(tmp_path, FakeRunner(), [mutation(0)])
    meta = load_dataset(tmp_path / "out" / "dataset.json").meta
    assert meta["mutations"] == 1
    assert {"dbt_core", "dbt_duckdb", "duckdb", "generated"} <= set(meta)


def test_limit_builds_only_the_first_cases(tmp_path):
    runner = FakeRunner()
    build(tmp_path, runner, [mutation(n) for n in range(5)], limit=2)
    assert runner.cases == ["seed_drop_column:raw.c0", "seed_drop_column:raw.c1"]
    assert len(load_dataset(tmp_path / "out" / "dataset.json").cases) == 2


# --- resuming ---------------------------------------------------------------


def test_a_second_run_only_does_the_cases_that_are_missing(tmp_path):
    mutations = [mutation(n) for n in range(5)]
    build(tmp_path, FakeRunner(), mutations, limit=2)

    second = FakeRunner()
    build(tmp_path, second, mutations)
    assert second.cases == [m.id for m in mutations[2:]]
    dataset = load_dataset(tmp_path / "out" / "dataset.json")
    assert [c.mutation for c in dataset.cases] == mutations


def test_a_case_that_ended_in_an_error_is_tried_again(tmp_path):
    mutations = [mutation(n) for n in range(3)]
    build(tmp_path, FakeRunner(errors={mutations[1].id}), mutations)

    second = FakeRunner()
    build(tmp_path, second, mutations)
    assert second.cases == [mutations[1].id]
    assert all(c.error is None for c in load_dataset(tmp_path / "out" / "dataset.json").cases)


def test_progress_is_saved_after_every_case_so_a_crash_loses_little(tmp_path):
    mutations = [mutation(n) for n in range(4)]
    with pytest.raises(RuntimeError, match="fell over"):
        build(tmp_path, FakeRunner(fail_on=mutations[2].id), mutations)

    saved = load_dataset(tmp_path / "out" / "dataset.json")
    assert [c.mutation for c in saved.cases] == mutations[:2]

    resumed = FakeRunner()
    build(tmp_path, resumed, mutations)
    assert resumed.cases == [m.id for m in mutations[2:]]


def test_the_file_is_never_left_half_written(tmp_path):
    build(tmp_path, FakeRunner(), [mutation(0), mutation(1)])
    assert not list((tmp_path / "out").glob("*.tmp"))
    json.loads((tmp_path / "out" / "dataset.json").read_text())  # valid JSON


def test_changed_baseline_statuses_are_not_mixed_with_old_cases(tmp_path):
    # a dataset built from another project must not be extended silently
    build(tmp_path, FakeRunner(), [mutation(0)])
    path = tmp_path / "out" / "dataset.json"
    data = json.loads(path.read_text())
    data["baseline"]["project"]["models"]["m"]["sql"] = "select 99 as x"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="different project"):
        build(tmp_path, FakeRunner(), [mutation(0), mutation(1)])


# --- the data files the labels depend on -------------------------------------------
#
# A label says what dbt did with the project's own data, so a dataset is only meaningful for that
# data. The file records a hash of every file in the project's data/ folder, and a build refuses
# to go on if the data is not the one it started with.


def sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def project_with_data(tmp_path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "project"
    (root / "data").mkdir(parents=True)
    for name, content in files.items():
        (root / "data" / name).write_bytes(content)
    return root


def test_the_file_records_a_hash_of_every_data_file(tmp_path):
    project_with_data(tmp_path, {"a.csv": b"x,y\n1,2\n", "b.csv": b"z\n3\n"})
    build(tmp_path, FakeRunner(), [mutation(0)])
    meta = load_dataset(tmp_path / "out" / "dataset.json").meta
    assert meta["data_files"] == {"a.csv": sha256(b"x,y\n1,2\n"), "b.csv": sha256(b"z\n3\n")}


def test_a_project_without_a_data_folder_records_no_data_files(tmp_path):
    build(tmp_path, FakeRunner(), [mutation(0)])
    assert load_dataset(tmp_path / "out" / "dataset.json").meta["data_files"] == {}


def test_a_build_goes_on_when_the_data_is_the_same(tmp_path):
    root = project_with_data(tmp_path, {"a.csv": b"x\n1\n"})
    mutations = [mutation(n) for n in range(3)]
    build(tmp_path, FakeRunner(), mutations, limit=1)
    second = FakeRunner()
    build(tmp_path, second, mutations)
    assert second.cases == [m.id for m in mutations[1:]]
    assert (root / "data" / "a.csv").read_bytes() == b"x\n1\n"


def test_a_build_refuses_to_go_on_with_different_data(tmp_path):
    root = project_with_data(tmp_path, {"a.csv": b"x\n1\n"})
    mutations = [mutation(n) for n in range(3)]
    build(tmp_path, FakeRunner(), mutations, limit=1)
    (root / "data" / "a.csv").write_bytes(b"x\n2\n")
    with pytest.raises(ValueError, match="data"):
        build(tmp_path, FakeRunner(), mutations)


def test_a_build_stops_when_a_case_changes_the_original_data(tmp_path):
    root = project_with_data(tmp_path, {"a.csv": b"x\n1\n"})

    class Careless(FakeRunner):
        def case(self, project_dir, baseline, mutation, workdir):
            (root / "data" / "a.csv").write_bytes(b"x\n999\n")  # writes through to the original
            return super().case(project_dir, baseline, mutation, workdir)

    runner = Careless()
    with pytest.raises(ValueError, match="changed"):
        build(tmp_path, runner, [mutation(n) for n in range(3)])
    assert len(runner.cases) == 1  # it did not go on to the next one


def test_a_dataset_without_data_hashes_can_still_be_resumed(tmp_path):
    """The jaffle_shop file was written before hashes were recorded."""
    mutations = [mutation(n) for n in range(3)]
    build(tmp_path, FakeRunner(), mutations, limit=1)
    path = tmp_path / "out" / "dataset.json"
    data = json.loads(path.read_text())
    del data["meta"]["data_files"]
    path.write_text(json.dumps(data))
    project_with_data(tmp_path, {"a.csv": b"x\n1\n"})
    second = FakeRunner()
    build(tmp_path, second, mutations)
    assert second.cases == [m.id for m in mutations[1:]]

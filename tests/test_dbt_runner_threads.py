"""Builds must give the same output every time, or a "silent" label could be pure noise.

Olist's customer_rfm uses `ntile(5) over (order by ...)` on data with ties. With DuckDB on several
threads the rows reach the window in a different order from one build to the next, so the same
project built twice gives different output. The runner therefore runs DuckDB on one thread, in the
copy it builds; the project's own files are not touched.
"""

import pytest

from schema_impact.evals.dbt_runner import _copy_project

yaml = pytest.importorskip("yaml")  # comes with dbt, part of the `bench` dependency group

PROFILES = """\
p:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: p.duckdb
      threads: 4
"""


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "dbt_project.yml").write_text("name: p\n")
    (root / "profiles.yml").write_text(PROFILES)
    return root


def profile_of(root):
    return yaml.safe_load((root / "profiles.yml").read_text())


def test_duckdb_runs_on_one_thread_in_the_copy(source, tmp_path):
    _copy_project(source, tmp_path / "copy")
    output = profile_of(tmp_path / "copy")["p"]["outputs"]["dev"]
    assert output["settings"] == {"threads": 1}
    assert output["threads"] == 4  # how many models dbt builds at once is left alone


def test_the_original_profile_is_not_touched(source, tmp_path):
    _copy_project(source, tmp_path / "copy")
    assert (source / "profiles.yml").read_text() == PROFILES


def test_other_settings_are_kept_and_a_higher_thread_count_is_replaced(source, tmp_path):
    (source / "profiles.yml").write_text(
        PROFILES + "      settings:\n        threads: 8\n        memory_limit: 1GB\n"
    )
    _copy_project(source, tmp_path / "copy")
    settings = profile_of(tmp_path / "copy")["p"]["outputs"]["dev"]["settings"]
    assert settings == {"threads": 1, "memory_limit": "1GB"}


def test_an_output_that_is_not_duckdb_is_left_alone(source, tmp_path):
    (source / "profiles.yml").write_text(
        PROFILES + "    other:\n      type: postgres\n      threads: 2\n"
    )
    _copy_project(source, tmp_path / "copy")
    other = profile_of(tmp_path / "copy")["p"]["outputs"]["other"]
    assert "settings" not in other


def test_a_project_without_a_profile_is_copied_as_it_is(source, tmp_path):
    (source / "profiles.yml").unlink()
    _copy_project(source, tmp_path / "copy")
    assert not (tmp_path / "copy" / "profiles.yml").exists()

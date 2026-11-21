"""`schema-impact diff --base REF --head REF`: compare two commits of a repository.

A pull request is a pair of commits, not a pair of folders, so the command can read the project
as it was at each ref. Nothing is checked out and the working tree is not touched: the files are
exported from git into temporary folders. It works for plain SQL projects; the artifacts of a dbt
project (`target/`) are not in git, so for dbt the two folders are passed as before.

A dbt PR job usually has the artifacts of the base (from the last production run) and compiles the
head with `dbt compile`, which writes a manifest but no catalog. The columns of the source tables
do not change in a PR, so the catalog of the base is used for both.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from schema_impact.adapters.dbt_manifest import load_dbt_project
from schema_impact.cli import main

REPO = Path(__file__).resolve().parent.parent
TOY = REPO / "examples" / "toy_project"
JAFFLE = REPO / "tests" / "fixtures" / "jaffle_shop_dbt"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository whose history is: the toy project, then SUM -> AVG, then a dropped column."""
    root = tmp_path / "repo"
    shutil.copytree(TOY, root)
    git(root, "init", "-q", "-b", "main")
    commit_all(root, "toy project")
    git(root, "tag", "v1")
    edit(root / "models" / "customer_totals.sql", "SUM(o.amount)", "AVG(o.amount)")
    commit_all(root, "average instead of sum")
    git(root, "tag", "v2")
    edit(root / "schema.sql", "    name    VARCHAR,\n", "")
    commit_all(root, "drop customers.name")
    return root


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- two refs -----------------------------------------------------------------------------------


def test_two_commits_are_compared(capsys, repo):
    code, out, _ = run(capsys, "diff", "--repo", str(repo), "--base", "v1", "--head", "v2")
    assert code == 0
    assert "The logic of customer_totals.total changed" in out


def test_the_head_defaults_to_head(capsys, repo):
    code, out, _ = run(capsys, "diff", "--repo", str(repo), "--base", "v2")
    assert code == 1
    assert "Column customers.name was dropped" in out


def test_a_branch_or_a_relative_ref_works(capsys, repo):
    code, out, _ = run(capsys, "diff", "--repo", str(repo), "--base", "main~1", "--head", "main")
    assert code == 1
    assert "customers.name" in out


def test_the_whole_history_is_the_sum_of_both_changes(capsys, repo):
    code, out, _ = run(
        capsys,
        "diff",
        "--repo",
        str(repo),
        "--base",
        "v1",
        "--format",
        "json",
        "--fail-on",
        "never",
    )
    kinds = {c["kind"] for c in json.loads(out)["changes"]}
    assert code == 0
    assert kinds == {"ColumnLogicChanged", "ColumnDropped"}


def test_the_repository_defaults_to_the_current_folder(capsys, repo, monkeypatch):
    monkeypatch.chdir(repo)
    code, _, _ = run(capsys, "diff", "--base", "v2")
    assert code == 1


def test_the_working_tree_and_the_checkout_are_left_alone(capsys, repo):
    git(repo, "checkout", "-q", "v1")
    (repo / "notes.txt").write_text("not committed")
    before = git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD")
    run(capsys, "diff", "--repo", str(repo), "--base", "v1", "--head", "main")
    assert (git(repo, "status", "--porcelain"), git(repo, "rev-parse", "HEAD")) == before


def test_a_project_in_a_subfolder_of_the_repository(capsys, tmp_path):
    root = tmp_path / "mono"
    shutil.copytree(TOY, root / "analytics" / "warehouse")
    (root / "README.md").write_text("a monorepo")
    git(root, "init", "-q", "-b", "main")
    commit_all(root, "start")
    edit(root / "analytics" / "warehouse" / "models" / "customer_totals.sql", "SUM", "AVG")
    commit_all(root, "average")
    code, out, _ = run(
        capsys,
        "diff", "--repo", str(root), "--base", "HEAD~1",
        "--project-dir", "analytics/warehouse",
    )  # fmt: skip
    assert code == 0
    assert "The logic of customer_totals.total changed" in out


# --- when it cannot run -------------------------------------------------------------------------


def test_an_unknown_ref_says_which(capsys, repo):
    code, out, err = run(capsys, "diff", "--repo", str(repo), "--base", "no-such-branch")
    assert code == 2
    assert out == ""
    assert "no-such-branch" in err
    assert "Traceback" not in err


def test_a_folder_that_is_not_a_repository_is_an_error(capsys, tmp_path):
    code, _, err = run(capsys, "diff", "--repo", str(tmp_path), "--base", "main")
    assert code == 2
    assert "git" in err.lower()


def test_a_project_folder_that_is_not_in_that_commit_is_an_error(capsys, repo):
    code, _, err = run(
        capsys, "diff", "--repo", str(repo), "--base", "v1", "--project-dir", "not/there"
    )
    assert code == 2
    assert "not/there" in err


def test_folders_and_refs_cannot_be_mixed(repo):
    with pytest.raises(SystemExit) as stopped:
        main(["diff", str(TOY), str(TOY), "--base", "v1"])
    assert stopped.value.code == 2


def test_there_must_be_two_folders_or_a_base(repo):
    with pytest.raises(SystemExit) as stopped:
        main(["diff", str(TOY)])
    assert stopped.value.code == 2
    with pytest.raises(SystemExit) as stopped:
        main(["diff"])
    assert stopped.value.code == 2


def test_dbt_artifacts_are_not_in_git_so_refs_are_refused_for_dbt(capsys, repo):
    code, _, err = run(capsys, "diff", "--repo", str(repo), "--base", "v1", "--adapter", "dbt")
    assert code == 2
    assert "target" in err and "folder" in err


# --- dbt: the head has a manifest but no catalog ------------------------------------------------


def compiled_head(tmp_path) -> Path:
    """What `dbt compile` leaves: the manifest of the edited project, and no catalog."""
    head = tmp_path / "head"
    shutil.copytree(JAFFLE, head)
    manifest = head / "target" / "manifest.json"
    data = json.loads(manifest.read_text())
    node = data["nodes"]["model.jaffle_shop.orders"]
    old = "sum(case when payment_method = 'credit_card' then amount else 0 end)"
    assert old in node["compiled_code"]
    node["compiled_code"] = node["compiled_code"].replace(old, old.replace("sum", "avg"))
    manifest.write_text(json.dumps(data))
    (head / "target" / "catalog.json").unlink()
    return head


def test_the_catalog_of_the_base_is_used_when_the_head_has_none(capsys, tmp_path):
    head = compiled_head(tmp_path)
    code, out, _ = run(capsys, "diff", str(JAFFLE), str(head), "--adapter", "dbt")
    assert code == 0
    assert "The logic of orders.credit_card_amount changed" in out


def test_the_adapter_can_take_its_catalog_from_another_project(tmp_path):
    head = compiled_head(tmp_path)
    project = load_dbt_project(head, catalog_from=JAFFLE)
    assert project.schema == load_dbt_project(JAFFLE).schema


def test_without_a_catalog_anywhere_it_still_says_what_to_run(capsys, tmp_path):
    head = compiled_head(tmp_path)
    broken = tmp_path / "base"
    shutil.copytree(JAFFLE, broken)
    (broken / "target" / "catalog.json").unlink()
    code, _, err = run(capsys, "diff", str(broken), str(head), "--adapter", "dbt")
    assert code == 2
    assert "docs generate" in err


def test_a_head_with_its_own_catalog_keeps_it(tmp_path):
    head = tmp_path / "head"
    shutil.copytree(JAFFLE, head)
    catalog = head / "target" / "catalog.json"
    data = json.loads(catalog.read_text())
    some = next(v for k, v in data["nodes"].items() if k.startswith("seed."))
    some["columns"] = {}
    catalog.write_text(json.dumps(data))
    assert load_dbt_project(head).schema != load_dbt_project(JAFFLE).schema

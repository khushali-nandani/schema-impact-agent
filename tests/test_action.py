"""The GitHub Action (`action.yml`) and the workflow that uses it.

An Action's YAML can only really be run by GitHub, so the shell of each step is taken out of the
file and run here, in bash, with the environment the runner would give it, against a real
temporary git repository and the fake GitHub of the comment tests. What stays unchecked until it
runs on GitHub is the runner itself: `setup-python`, `github.action_path` and the permissions of
the token.

The steps are: install the tool, make the review (never fails by itself: it hands the exit code on),
comment (skipped when there is no review to comment on), and finally fail the job if the review
says so. That order lets a failing check still show its comment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_cli_git import JAFFLE, git, repo  # noqa: F401
from test_github_comment import REPO as FAKE_REPO
from test_github_comment import TOKEN, github  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())
WORKFLOW = yaml.safe_load(
    (ROOT / "examples" / "pr_demo" / ".github" / "workflows" / "schema-impact.yml").read_text(),
)
STEPS = {step["id"]: step for step in ACTION["runs"]["steps"]}


# --- the file -----------------------------------------------------------------------------------


def test_it_is_a_composite_action_with_a_name_and_a_description():
    assert ACTION["runs"]["using"] == "composite"
    assert ACTION["name"]
    assert ACTION["description"]


def test_the_steps_run_in_the_order_install_review_comment_finish():
    assert list(STEPS) == ["python", "install", "review", "comment", "finish"]


def test_the_inputs_have_defaults_that_work_for_a_plain_sql_project():
    inputs = ACTION["inputs"]
    assert inputs["adapter"]["default"] == "sql"
    assert inputs["dialect"]["default"] == "duckdb"
    assert inputs["fail-on"]["default"] == "breaking"
    assert inputs["project-dir"]["default"] == "."
    assert inputs["comment"]["default"] == "true"
    assert inputs["github-token"]["default"] == "${{ github.token }}"
    assert inputs["before-dir"]["default"] == ""
    assert inputs["after-dir"]["default"] == ""
    assert all(spec["description"] for spec in inputs.values())


def test_no_shell_step_puts_an_expression_in_its_script():
    """`${{ }}` in a script is pasted in before the shell runs it: a pull request could write
    shell into a branch name or an input. Values go through `env:` and are quoted."""
    for step in ACTION["runs"]["steps"]:
        if "run" in step:
            assert "${{" not in step["run"], step["id"]
            assert step["shell"] == "bash"


def test_the_review_step_hands_its_exit_code_on_instead_of_failing():
    assert STEPS["review"]["run"].rstrip().endswith("exit 0")


def test_the_comment_step_is_skipped_when_the_review_could_not_be_made_or_is_turned_off():
    condition = STEPS["comment"]["if"]
    assert "inputs.comment" in condition
    assert "exit-code" in condition and "'2'" in condition


def test_the_last_step_runs_even_if_the_comment_failed():
    assert STEPS["finish"]["if"] == "always()"


# --- running the steps --------------------------------------------------------------------------


def bash(step: str, env: dict[str, str], tmp_path: Path) -> tuple[int, dict[str, str]]:
    """Run one step's script as the runner would. Returns its exit status and its step outputs."""
    output = tmp_path / "github_output"
    output.write_text("")
    environment = {
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        **env,
    }
    done = subprocess.run(
        ["bash", "-e", "-c", STEPS[step]["run"]], env=environment, capture_output=True, text=True
    )
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return done.returncode, outputs


def refs_env(repo_path: Path, base: str, head: str, **extra: str) -> dict[str, str]:
    return {
        "ADAPTER": "sql",
        "DIALECT": "duckdb",
        "FAIL_ON": "breaking",
        "PROJECT_DIR": ".",
        "BEFORE_DIR": "",
        "AFTER_DIR": "",
        "REPO_DIR": str(repo_path),
        "BASE_SHA": git(repo_path, "rev-parse", base).strip(),
        "HEAD_SHA": git(repo_path, "rev-parse", head).strip(),
        **extra,
    }


def test_a_breaking_change_gives_exit_code_one_and_a_report(repo, tmp_path):  # noqa: F811
    status, outputs = bash("review", refs_env(repo, "v2", "HEAD"), tmp_path)
    assert status == 0
    assert outputs["exit-code"] == "1"
    report = Path(outputs["report"]).read_text()
    assert report.startswith("<!-- schema-impact-agent -->")
    assert "### Breaking" in report


def test_a_change_that_only_needs_review_passes_by_default(repo, tmp_path):  # noqa: F811
    status, outputs = bash("review", refs_env(repo, "v1", "v2"), tmp_path)
    assert (status, outputs["exit-code"]) == (0, "0")
    assert "Needs review" in Path(outputs["report"]).read_text()


def test_fail_on_is_passed_on(repo, tmp_path):  # noqa: F811
    env = refs_env(repo, "v1", "v2", FAIL_ON="needs_review")
    assert bash("review", env, tmp_path)[1]["exit-code"] == "1"


def test_a_ref_that_does_not_exist_is_exit_code_two(repo, tmp_path):  # noqa: F811
    env = refs_env(repo, "v1", "v2", BASE_SHA="0" * 40)
    status, outputs = bash("review", env, tmp_path)
    assert status == 0
    assert outputs["exit-code"] == "2"


def test_a_dbt_project_is_given_as_two_folders(tmp_path):
    env = {
        "ADAPTER": "dbt",
        "DIALECT": "duckdb",
        "FAIL_ON": "breaking",
        "PROJECT_DIR": ".",
        "BEFORE_DIR": str(JAFFLE),
        "AFTER_DIR": str(JAFFLE),
        "REPO_DIR": str(tmp_path),
        "BASE_SHA": "",
        "HEAD_SHA": "",
    }
    status, outputs = bash("review", env, tmp_path)
    assert (status, outputs["exit-code"]) == (0, "0")


def test_a_branch_name_with_shell_in_it_is_not_run(repo, tmp_path):  # noqa: F811
    marker = tmp_path / "pwned"
    env = refs_env(repo, "v1", "v2", BASE_SHA=f"v1; touch {marker}")
    status, outputs = bash("review", env, tmp_path)
    assert not marker.exists()
    assert outputs["exit-code"] == "2"


def comment_env(github, report: Path) -> dict[str, str]:  # noqa: F811
    event = report.parent / "event.json"
    event.write_text('{"pull_request": {"number": 7}}')
    return {
        "REPORT": str(report),
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_REPOSITORY": FAKE_REPO,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_API_URL": github.api_url,
    }


def test_the_comment_step_posts_the_report(github, tmp_path):  # noqa: F811
    report = tmp_path / "report.md"
    report.write_text("<!-- schema-impact-agent -->\n## Schema impact: 1 model breaking\n")
    status, _ = bash("comment", comment_env(github, report), tmp_path)
    assert status == 0
    assert [c["body"] for c in github.comments] == [report.read_text()]


@pytest.mark.parametrize("code", ["0", "1", "2"])
def test_the_last_step_fails_the_job_with_the_exit_code_of_the_review(code, tmp_path):
    status, _ = bash("finish", {"EXIT_CODE": code}, tmp_path)
    assert status == int(code)


# --- the workflow that uses it ------------------------------------------------------------------


def test_the_workflow_runs_on_pull_requests_and_never_on_pull_request_target():
    triggers = WORKFLOW[True]  # YAML reads the key `on` as True
    assert "pull_request" in triggers
    assert "pull_request_target" not in triggers


def test_the_workflow_asks_for_no_more_than_it_needs():
    assert WORKFLOW["permissions"] == {"contents": "read", "pull-requests": "write"}


def test_the_workflow_fetches_the_whole_history_so_the_base_commit_is_there():
    steps = next(iter(WORKFLOW["jobs"].values()))["steps"]
    checkout = next(s for s in steps if s["uses"].startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0


def test_the_workflow_uses_this_action():
    steps = next(iter(WORKFLOW["jobs"].values()))["steps"]
    assert any(s["uses"].startswith("khushali-nandani/schema-impact-agent@") for s in steps)

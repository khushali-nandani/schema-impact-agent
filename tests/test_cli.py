"""The command line: `schema-impact diff BEFORE AFTER`.

It reads two project folders (plain SQL, or a dbt project's `target/` artifacts), says what
changed and what that affects, and exits with a code a CI job can act on:

- 0  nothing at or above `--fail-on` was found (the default threshold is `breaking`)
- 1  there is an impact at or above the threshold
- 2  the command could not run (a folder is missing, the arguments are wrong)

No model is involved unless a judge is passed in (`--judge ollama` on the command line).
"""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from schema_impact.agent.judge import HIGH, FakeJudge, Judgment
from schema_impact.cli import main

REPO = Path(__file__).resolve().parent.parent
TOY = REPO / "examples" / "toy_project"
JAFFLE = REPO / "tests" / "fixtures" / "jaffle_shop_dbt"


@pytest.fixture
def before(tmp_path) -> Path:
    path = tmp_path / "before"
    shutil.copytree(TOY, path)
    return path


@pytest.fixture
def after(tmp_path) -> Path:
    path = tmp_path / "after"
    shutil.copytree(TOY, path)
    return path


def edit_model(project: Path, model: str, old: str, new: str) -> None:
    path = project / "models" / f"{model}.sql"
    sql = path.read_text()
    assert old in sql
    path.write_text(sql.replace(old, new))


def drop_source_column(project: Path, line: str) -> None:
    path = project / "schema.sql"
    text = path.read_text()
    assert line in text
    path.write_text(text.replace(line, "", 1))


def run(capsys, *argv: str, judge=None) -> tuple[int, str, str]:
    code = main(list(argv), judge=judge)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- nothing changed ----------------------------------------------------------------------------


def test_two_identical_projects_have_no_impact_and_exit_zero(capsys, before, after):
    code, out, _ = run(capsys, "diff", str(before), str(after))
    assert code == 0
    assert "No downstream impact" in out


# --- what a change looks like -------------------------------------------------------------------


def test_a_column_whose_logic_changed_is_listed_with_the_models_that_read_it(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    code, out, _ = run(capsys, "diff", str(before), str(after))
    assert "The logic of customer_totals.total changed" in out
    assert "top_customers.total" in out
    assert "needs_review" in out
    assert code == 0  # the default threshold is breaking, and this only needs review


def test_a_dropped_column_is_breaking_and_exits_one(capsys, before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    code, out, _ = run(capsys, "diff", str(before), str(after))
    assert code == 1
    assert "breaking" in out
    assert "customers.name was dropped" in out
    assert "Fix:" in out  # a breaking impact comes with a suggestion, no model needed


def test_the_summary_counts_both_kinds(capsys, before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    _, out, _ = run(capsys, "diff", str(before), str(after))
    assert "breaking" in out.split("## Summary")[1]


# --- the exit code ------------------------------------------------------------------------------


def test_fail_on_needs_review_makes_a_logic_change_fail(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    code, _, _ = run(capsys, "diff", str(before), str(after), "--fail-on", "needs_review")
    assert code == 1


def test_fail_on_never_exits_zero_even_when_something_breaks(capsys, before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    code, out, _ = run(capsys, "diff", str(before), str(after), "--fail-on", "never")
    assert code == 0
    assert "breaking" in out  # it is still reported


def test_fail_on_needs_review_still_passes_when_nothing_is_affected(capsys, before, after):
    code, _, _ = run(capsys, "diff", str(before), str(after), "--fail-on", "needs_review")
    assert code == 0


# --- json ---------------------------------------------------------------------------------------


def test_json_output_has_the_changes_the_impacts_and_a_summary(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    code, out, _ = run(capsys, "diff", str(before), str(after), "--format", "json")
    data = json.loads(out)
    assert code == 0
    assert data["changes"] == [
        {"kind": "ColumnLogicChanged", "model": "customer_totals", "column": "total"}
    ]
    places = {(i["model"], i["column"]) for i in data["impacts"]}
    assert ("top_customers", "total") in places
    impact = data["impacts"][0]
    assert impact["effect"] == "needs_review"
    assert impact["change"]["kind"] == "ColumnLogicChanged"
    assert isinstance(impact["roles"], list)
    assert impact["path"][0] == "customer_totals.total"
    assert impact["judgment"] is None
    assert data["summary"] == {"breaking": 0, "needs_review": len(data["impacts"])}


def test_json_output_of_a_whole_model_has_a_null_column(capsys, before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    _, out, _ = run(capsys, "diff", str(before), str(after), "--format", "json")
    data = json.loads(out)
    assert data["summary"]["breaking"] >= 1
    assert all(i["effect"] in {"breaking", "needs_review"} for i in data["impacts"])
    assert any(i["column"] is None for i in data["impacts"])


def test_json_output_is_the_same_every_time(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    first = run(capsys, "diff", str(before), str(after), "--format", "json")[1]
    second = run(capsys, "diff", str(before), str(after), "--format", "json")[1]
    assert first == second


# --- the judge ----------------------------------------------------------------------------------


def test_a_judge_is_asked_about_needs_review_impacts_only(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    judge = FakeJudge(Judgment(HIGH, "the total is now an average", "check the readers"))
    code, out, _ = run(capsys, "diff", str(before), str(after), judge=judge)
    assert judge.calls
    assert "the total is now an average" in out
    assert code == 0  # a judge does not change the exit code unless --fail-on asks for it


def test_a_judge_is_not_asked_about_breaking_impacts(capsys, before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    judge = FakeJudge(Judgment(HIGH, "x", "y"))
    run(capsys, "diff", str(before), str(after), judge=judge)
    assert judge.calls == []


def test_the_judgment_is_in_the_json(capsys, before, after):
    edit_model(after, "customer_totals", "SUM(o.amount)", "AVG(o.amount)")
    judge = FakeJudge(Judgment(HIGH, "the total is now an average", "check the readers"))
    _, out, _ = run(capsys, "diff", str(before), str(after), "--format", "json", judge=judge)
    impact = json.loads(out)["impacts"][0]
    assert impact["judgment"] == {
        "risk": "high",
        "explanation": "the total is now an average",
        "fix": "check the readers",
    }


# --- dbt ----------------------------------------------------------------------------------------


@pytest.fixture
def jaffle_after(tmp_path) -> Path:
    path = tmp_path / "jaffle_after"
    shutil.copytree(JAFFLE, path)
    return path


def edit_compiled(project: Path, model: str, old: str, new: str) -> None:
    manifest = project / "target" / "manifest.json"
    data = json.loads(manifest.read_text())
    node = data["nodes"][f"model.jaffle_shop.{model}"]
    assert old in node["compiled_code"]
    node["compiled_code"] = node["compiled_code"].replace(old, new)
    manifest.write_text(json.dumps(data))


def test_a_dbt_project_with_no_change_exits_zero(capsys, jaffle_after):
    code, out, _ = run(capsys, "diff", str(JAFFLE), str(jaffle_after), "--adapter", "dbt")
    assert code == 0
    assert "No downstream impact" in out


def test_a_dbt_change_of_one_column_is_read_from_the_compiled_sql(capsys, jaffle_after):
    edit_compiled(
        jaffle_after,
        "orders",
        "sum(case when payment_method = 'credit_card' then amount else 0 end)",
        "avg(case when payment_method = 'credit_card' then amount else 0 end)",
    )
    code, out, _ = run(capsys, "diff", str(JAFFLE), str(jaffle_after), "--adapter", "dbt")
    assert code == 0
    assert "The logic of orders.credit_card_amount changed" in out


# --- when it cannot run -------------------------------------------------------------------------


def test_a_missing_folder_is_an_error_not_a_traceback(capsys, before, tmp_path):
    code, out, err = run(capsys, "diff", str(before), str(tmp_path / "nowhere"))
    assert code == 2
    assert out == ""
    assert "nowhere" in err
    assert "Traceback" not in err


def test_a_dbt_project_without_artifacts_says_what_to_run(capsys, before, tmp_path):
    code, _, err = run(capsys, "diff", str(tmp_path), str(tmp_path), "--adapter", "dbt")
    assert code == 2
    assert "dbt" in err


def test_a_wrong_option_value_is_refused(before, after):
    with pytest.raises(SystemExit) as stopped:
        main(["diff", str(before), str(after), "--fail-on", "sometimes"])
    assert stopped.value.code == 2


# --- as a program -------------------------------------------------------------------------------


def test_it_runs_as_a_module_and_passes_the_exit_code_on(before, after):
    drop_source_column(after, "    name    VARCHAR,\n")
    result = subprocess.run(
        [sys.executable, "-m", "schema_impact", "diff", str(before), str(after)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "breaking" in result.stdout


def test_the_package_declares_the_command():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["schema-impact"] == "schema_impact.cli:main"

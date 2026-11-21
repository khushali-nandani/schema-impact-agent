"""The review as a pull request comment.

It is Markdown for a person reading a PR, so the affected places are grouped by model (a breaking
change in a staging table is one row per model, not one line per column), the fixes are listed once
each, and the first line is a hidden marker so a job can find its own comment and update it instead
of adding a new one on every push. A comment is at most 65536 characters, so long tables are cut.
"""

from pathlib import Path

import pytest

from project_edits import change_model_sql, drop_column
from schema_impact.agent.judge import HIGH, LOW, FakeJudge, Judgment
from schema_impact.cli import main
from schema_impact.core.changes import ColumnDropped
from schema_impact.core.impact import BREAKING, NEEDS_REVIEW, Impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.core.registry import load_project
from schema_impact.formats import MARKER, format_markdown
from schema_impact.review import Item, Review, review

TOY = Path(__file__).resolve().parent.parent / "examples" / "toy_project"


@pytest.fixture
def before():
    return load_project(TOY)


def impact(model, column, effect=BREAKING, roles=("select",), change=None):
    change = change or ColumnDropped("customers", "name")
    path = (ColumnRef("customers", "name"), ColumnRef(model, column))
    return Impact(change, model, column, frozenset(roles), effect, path)


# --- two whole reviews, written out by hand -----------------------------------------------------


def test_a_dropped_column_is_a_row_per_model_with_one_fix(before):
    after = drop_column(before, "customers", "name")
    expected = """\
<!-- schema-impact-agent -->
## Schema impact: 2 models breaking

**Changes**
- Column customers.name was dropped

### Breaking

| Model | Columns | Used in | Caused by |
|---|---|---|---|
| `customer_totals` | whole model, `name` | group_by, select | Column customers.name was dropped |
| `top_customers` | whole model, `name`, `total` | order_by, select, where | Column customers.name was dropped |

**How to fix**
- Restore customers.name, or change the SQL that uses it.
"""  # noqa: E501
    assert format_markdown(review(before, after)) == expected


def test_a_changed_column_is_a_model_to_review(before):
    sql = before.models["customer_totals"].sql.replace("SUM(o.amount)", "AVG(o.amount)")
    after = change_model_sql(before, "customer_totals", sql)
    expected = """\
<!-- schema-impact-agent -->
## Schema impact: 1 model needs review

**Changes**
- The logic of customer_totals.total changed

### Needs review

| Model | Columns | Used in | Caused by |
|---|---|---|---|
| `top_customers` | whole model, `total` | order_by, select, where | The logic of customer_totals.total changed |
"""  # noqa: E501
    assert format_markdown(review(before, after)) == expected


# --- the parts ----------------------------------------------------------------------------------


def test_the_first_line_is_the_marker_a_job_finds_its_comment_by(before):
    assert format_markdown(review(before, before)).splitlines()[0] == MARKER
    assert MARKER == "<!-- schema-impact-agent -->"


def test_no_impact_says_so_and_has_no_table(before):
    text = format_markdown(review(before, before))
    assert "## Schema impact: none" in text
    assert "No downstream impact." in text
    assert "|" not in text
    assert "- none" in text  # no change at all


def test_a_change_nobody_reads_is_listed_but_has_no_impact(before):
    sql = before.models["top_customers"].sql.replace("1000", "2000")
    text = format_markdown(review(before, change_model_sql(before, "top_customers", sql)))
    assert "## Schema impact: none" in text
    assert "The SQL of model top_customers changed" in text


def test_breaking_comes_before_needs_review():
    result = Review(
        [ColumnDropped("customers", "name")],
        [
            Item(impact("a", "x", NEEDS_REVIEW), None),
            Item(impact("b", "y", BREAKING), Judgment(HIGH, "e", "Restore it")),
        ],
    )
    text = format_markdown(result)
    assert text.index("### Breaking") < text.index("### Needs review")
    assert "## Schema impact: 1 model breaking, 1 model needs review" in text


def test_several_models_say_need_review():
    result = Review(
        [ColumnDropped("customers", "name")],
        [Item(impact(m, "x", NEEDS_REVIEW), None) for m in ("a", "b")],
    )
    assert "## Schema impact: 2 models need review" in format_markdown(result)


def test_the_same_fix_is_listed_once_however_many_models_it_is_for():
    result = Review(
        [ColumnDropped("customers", "name")],
        [
            Item(impact(m, "x"), Judgment(HIGH, "e", "Restore customers.name"))
            for m in ("a", "b", "c")
        ],
    )
    assert format_markdown(result).count("Restore customers.name") == 1


# --- a judge ------------------------------------------------------------------------------------


def test_without_a_judge_the_review_table_has_no_risk_column(before):
    sql = before.models["customer_totals"].sql.replace("SUM(o.amount)", "AVG(o.amount)")
    text = format_markdown(review(before, change_model_sql(before, "customer_totals", sql)))
    assert "Risk" not in text


def test_a_judges_view_is_in_a_risk_column_and_a_note(before):
    sql = before.models["customer_totals"].sql.replace("SUM(o.amount)", "AVG(o.amount)")
    after = change_model_sql(before, "customer_totals", sql)
    judge = FakeJudge(Judgment(HIGH, "an average is not a total", "Check who reads it"))
    text = format_markdown(review(before, after, judge))
    assert "| Model | Columns | Used in | Caused by | Risk |" in text
    assert "high |" in text
    assert "an average is not a total" in text
    assert "Check who reads it" in text


def test_the_highest_risk_of_a_models_columns_is_the_one_shown():
    answers = iter([Judgment(LOW, "fine", "none"), Judgment(HIGH, "bad", "fix it")])
    result = Review(
        [ColumnDropped("customers", "name")],
        [
            Item(impact("a", "x", NEEDS_REVIEW), next(answers)),
            Item(impact("a", "y", NEEDS_REVIEW), next(answers)),
        ],
    )
    assert "| high |" in format_markdown(result)


# --- text that would break the table ------------------------------------------------------------


def test_a_pipe_or_a_line_break_in_a_name_or_a_judgment_does_not_break_the_table():
    change = ColumnDropped("t", "a|b")
    result = Review(
        [change],
        [
            Item(
                impact("m", "x", NEEDS_REVIEW, change=change),
                Judgment(HIGH, "line one\nline two | three", "do this"),
            )
        ],
    )
    text = format_markdown(result)
    rows = [line for line in text.splitlines() if line.startswith("| `m`")]
    assert len(rows) == 1
    assert "a\\|b" in rows[0]
    assert rows[0].replace("\\|", "").count("|") == 6  # 5 cells
    assert "line one line two | three" in text  # in a note, where a pipe is harmless


# --- long reviews -------------------------------------------------------------------------------


def test_a_long_table_is_cut_and_says_how_many_are_left_out():
    result = Review(
        [ColumnDropped("customers", "name")],
        [Item(impact(f"model_{i:03}", "x"), None) for i in range(120)],
    )
    text = format_markdown(result)
    assert "## Schema impact: 120 models breaking" in text
    assert sum(1 for line in text.splitlines() if line.startswith("| `model_")) == 50
    assert "model_049" in text
    assert "model_050" not in text
    assert "and 70 more models" in text
    assert "--format json" in text  # where the rest is


def test_a_comment_always_fits_the_limit():
    long_text = "x" * 3000
    result = Review(
        [ColumnDropped("customers", "name")],
        [
            Item(impact(f"m{i}", "c", NEEDS_REVIEW), Judgment(HIGH, long_text, long_text))
            for i in range(400)
        ],
    )
    assert len(format_markdown(result)) < 65536


# --- on the command line ------------------------------------------------------------------------


def test_the_command_prints_the_markdown_and_keeps_its_exit_code(capsys, tmp_path):
    import shutil

    after = tmp_path / "after"
    shutil.copytree(TOY, after)
    schema = after / "schema.sql"
    schema.write_text(schema.read_text().replace("    name    VARCHAR,\n", "", 1))

    code = main(["diff", str(TOY), str(after), "--format", "markdown"])
    out = capsys.readouterr().out
    assert code == 1
    assert out.splitlines()[0] == MARKER
    assert "### Breaking" in out
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_the_markdown_is_the_same_every_time(before):
    after = drop_column(before, "customers", "name")
    assert format_markdown(review(before, after)) == format_markdown(review(before, after))

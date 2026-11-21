"""`schema-impact comment FILE`: put a Markdown review on the pull request this job is for.

It reads what a GitHub Actions job provides in its environment (the token, the repository, the
event that started the run, the API address and the job summary file), so the job needs no
glue code. Failing to comment must not fail the job: on a pull request from a fork the token is
read-only, and the review is then written to the job summary instead.
"""

import io
import json

import pytest

from schema_impact.cli import main
from schema_impact.core.changes import ColumnDropped
from schema_impact.core.impact import BREAKING, Impact
from schema_impact.core.lineage import ColumnRef
from schema_impact.formats import MARKER, format_markdown, has_impact
from schema_impact.review import Item, Review
from test_github_comment import REPO, TOKEN, github  # noqa: F401  (the fake GitHub fixture)

BREAKING_REVIEW = f"{MARKER}\n## Schema impact: 1 model breaking\n\n### Breaking\n"
EMPTY_REVIEW = f"{MARKER}\n## Schema impact: none\n\nNo downstream impact.\n"


@pytest.fixture
def job(monkeypatch, tmp_path, github):  # noqa: F811
    """The environment of a job on pull request 7 whose API is the fake GitHub."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_API_URL", github.api_url)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    github.event = event
    return github


def review_file(tmp_path, text: str):
    path = tmp_path / "review.md"
    path.write_text(text)
    return path


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- commenting ---------------------------------------------------------------------------------


def test_a_review_with_impact_becomes_a_comment(capsys, job, tmp_path):
    code, out, _ = run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    assert code == 0
    assert "created" in out
    assert [c["body"] for c in job.comments] == [BREAKING_REVIEW]
    assert job.requests[-1][1] == f"/repos/{REPO}/issues/7/comments"


def test_the_next_run_updates_it(capsys, job, tmp_path):
    path = review_file(tmp_path, BREAKING_REVIEW)
    run(capsys, "comment", str(path))
    code, out, _ = run(capsys, "comment", str(path))
    assert code == 0
    assert "updated" in out
    assert len(job.comments) == 1


def test_a_review_with_nothing_to_say_makes_no_comment(capsys, job, tmp_path):
    code, out, _ = run(capsys, "comment", str(review_file(tmp_path, EMPTY_REVIEW)))
    assert code == 0
    assert "skipped" in out
    assert job.comments == []


def test_a_review_that_now_has_nothing_to_say_corrects_the_old_comment(capsys, job, tmp_path):
    run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    run(capsys, "comment", str(review_file(tmp_path, EMPTY_REVIEW)))
    assert [c["body"] for c in job.comments] == [EMPTY_REVIEW]


def test_the_review_can_come_from_standard_input(capsys, monkeypatch, job):
    monkeypatch.setattr("sys.stdin", io.StringIO(BREAKING_REVIEW))
    code, _, _ = run(capsys, "comment", "-")
    assert code == 0
    assert [c["body"] for c in job.comments] == [BREAKING_REVIEW]


# --- when it cannot comment ---------------------------------------------------------------------


def test_a_refused_comment_goes_to_the_job_summary_and_does_not_fail_the_job(
    capsys, monkeypatch, job, tmp_path
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    job.fail_with = 403
    code, _, err = run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    assert code == 0
    assert summary.read_text() == BREAKING_REVIEW
    assert "warning" in err and "403" in err
    assert TOKEN not in err


def test_without_a_summary_file_the_review_is_printed(capsys, job, tmp_path):
    job.fail_with = 403
    code, out, err = run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    assert code == 0
    assert BREAKING_REVIEW in out
    assert "warning" in err


def test_a_run_that_is_not_for_a_pull_request_falls_back_the_same_way(
    capsys, monkeypatch, job, tmp_path
):
    job.event.write_text(json.dumps({"ref": "refs/heads/main"}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, _, err = run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    assert code == 0
    assert "pull request" in err
    assert summary.read_text() == BREAKING_REVIEW
    assert job.requests == []


# --- when it cannot run -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH"])
def test_a_missing_variable_is_named(capsys, monkeypatch, job, tmp_path, name):
    monkeypatch.delenv(name)
    code, out, err = run(capsys, "comment", str(review_file(tmp_path, BREAKING_REVIEW)))
    assert code == 2
    assert out == ""
    assert name in err


def test_a_missing_file_is_an_error(capsys, job, tmp_path):
    code, _, err = run(capsys, "comment", str(tmp_path / "nowhere.md"))
    assert code == 2
    assert "nowhere.md" in err


def test_the_api_address_defaults_to_github(monkeypatch, tmp_path):
    from schema_impact import cli

    seen = {}

    def fake_upsert(api_url, *args, **kwargs):
        seen["url"] = api_url
        return "created"

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 1}}))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    monkeypatch.setattr(cli, "upsert_comment", fake_upsert)
    main(["comment", str(review_file(tmp_path, BREAKING_REVIEW))])
    assert seen["url"] == "https://api.github.com"


# --- what is in the comment ---------------------------------------------------------------------


def test_has_impact_tells_a_review_with_nothing_in_it_from_the_rest():
    assert has_impact(BREAKING_REVIEW) is True
    assert has_impact(EMPTY_REVIEW) is False


def test_a_name_that_looks_like_a_mention_does_not_notify_anyone():
    change = ColumnDropped("customers", "@octocat")
    path = (ColumnRef("customers", "@octocat"), ColumnRef("m", "x"))
    result = Review(
        [change], [Item(Impact(change, "m", "x", frozenset({"select"}), BREAKING, path), None)]
    )
    text = format_markdown(result)
    assert "@octocat" not in text
    assert "@​octocat" in text
    assert text.splitlines()[0] == MARKER

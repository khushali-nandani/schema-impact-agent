"""Posting the review as one comment on a pull request, and updating it on the next push.

The comments are found by the hidden marker on their first line, so a push updates the comment it
made before instead of adding another. These tests talk to a real HTTP server on localhost that
behaves like the part of the GitHub API used (list, create and edit issue comments, paginated),
so the requests, headers and pagination are the real ones.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from schema_impact.formats import MARKER
from schema_impact.github import CommentError, pr_number, upsert_comment, write_summary

REPO = "octo/warehouse"
TOKEN = "ghs_secret_token_value"


class FakeGitHub:
    """The issue-comments endpoints of one pull request, with the requests it received."""

    def __init__(self) -> None:
        self.comments: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.fail_with: int | None = None
        self._next_id = 1000

    def add(self, body: str, login: str = "someone") -> dict:
        comment = {"id": self._next_id, "body": body, "user": {"login": login}}
        self._next_id += 1
        self.comments.append(comment)
        return comment

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.requests]


@pytest.fixture
def github():
    fake = FakeGitHub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the test output quiet
            pass

        def _send(self, status: int, payload) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else {}
            fake.requests.append((method, self.path, dict(self.headers)))
            if fake.fail_with:
                self._send(fake.fail_with, {"message": "Resource not accessible by integration"})
                return
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            if method == "GET" and parts[-1] == "comments":
                query = parse_qs(url.query)
                size, page = int(query["per_page"][0]), int(query.get("page", ["1"])[0])
                self._send(200, fake.comments[(page - 1) * size : page * size])
            elif method == "POST" and parts[-1] == "comments":
                self._send(201, fake.add(body["body"], "github-actions[bot]"))
            elif method == "PATCH" and parts[-2] == "comments":
                target = next(c for c in fake.comments if c["id"] == int(parts[-1]))
                target["body"] = body["body"]
                self._send(200, target)
            else:
                self._send(404, {"message": "Not Found"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PATCH(self):
            self._handle("PATCH")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.api_url = f"http://127.0.0.1:{server.server_port}"
    yield fake
    server.shutdown()
    server.server_close()


def post(github, body: str, **kwargs) -> str:
    return upsert_comment(github.api_url, TOKEN, REPO, 7, body, **kwargs)


BODY = f"{MARKER}\n## Schema impact: 1 model breaking\n"


# --- creating and updating ----------------------------------------------------------------------


def test_the_first_review_is_a_new_comment(github):
    assert post(github, BODY) == "created"
    assert [c["body"] for c in github.comments] == [BODY]
    method, path, headers = github.requests[-1]
    assert (method, path) == ("POST", f"/repos/{REPO}/issues/7/comments")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/vnd.github+json"


def test_the_next_push_updates_that_comment_instead_of_adding_one(github):
    post(github, BODY)
    newer = f"{MARKER}\n## Schema impact: none\n"
    assert post(github, newer) == "updated"
    assert [c["body"] for c in github.comments] == [newer]
    assert github.methods().count("POST") == 1


def test_the_same_review_twice_is_still_one_comment(github):
    post(github, BODY)
    post(github, BODY)
    assert len(github.comments) == 1


def test_comments_without_the_marker_are_left_alone(github):
    github.add("looks good to me")
    github.add("a comment that mentions schema-impact-agent but has no marker")
    assert post(github, BODY) == "created"
    assert len(github.comments) == 3
    assert github.comments[0]["body"] == "looks good to me"


def test_the_marker_is_found_on_a_later_page(github):
    for i in range(150):
        github.add(f"comment {i}")
    github.add(BODY, "github-actions[bot]")
    assert post(github, f"{MARKER}\nnew\n") == "updated"
    assert len(github.comments) == 151
    assert github.comments[-1]["body"] == f"{MARKER}\nnew\n"


# --- nothing to say -----------------------------------------------------------------------------


def test_no_comment_is_made_when_there_is_nothing_to_say_and_none_exists(github):
    assert post(github, BODY, create_if_missing=False) == "skipped"
    assert "POST" not in github.methods()
    assert github.comments == []


def test_an_old_comment_is_updated_even_when_nothing_is_to_be_created(github):
    post(github, BODY)
    assert post(github, f"{MARKER}\nNo downstream impact.\n", create_if_missing=False) == "updated"
    assert "No downstream impact." in github.comments[0]["body"]


# --- when it fails ------------------------------------------------------------------------------


def test_a_refusal_is_an_error_that_says_the_status_and_not_the_token(github):
    github.fail_with = 403
    with pytest.raises(CommentError) as error:
        post(github, BODY)
    assert "403" in str(error.value)
    assert TOKEN not in str(error.value)


def test_a_server_that_is_not_there_is_an_error():
    with pytest.raises(CommentError) as error:
        upsert_comment("http://127.0.0.1:1", TOKEN, REPO, 7, BODY)
    assert TOKEN not in str(error.value)


# --- which pull request -------------------------------------------------------------------------


def test_the_pull_request_number_is_read_from_the_event(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "synchronize", "pull_request": {"number": 42}}))
    assert pr_number(event) == 42


def test_an_event_that_is_not_a_pull_request_is_an_error(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"ref": "refs/heads/main"}))
    with pytest.raises(CommentError, match="pull request"):
        pr_number(event)


def test_a_missing_event_file_is_an_error(tmp_path):
    with pytest.raises(CommentError):
        pr_number(tmp_path / "nowhere.json")


# --- the job summary, for when a comment cannot be made -----------------------------------------


def test_the_summary_is_appended_to_the_file_github_names(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    assert write_summary("the review\n", summary) is True
    assert summary.read_text() == "earlier step\nthe review\n"


def test_without_a_summary_file_nothing_is_written():
    assert write_summary("the review\n", None) is False

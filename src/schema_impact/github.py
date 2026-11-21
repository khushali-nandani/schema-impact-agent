"""Put the review on a pull request as one comment that is updated, not repeated.

The comment is found by the hidden marker on its first line (`formats.MARKER`), so each push edits
the comment of the one before. Only the standard library is used to talk to the GitHub API.

A job on a pull request from a fork gets a read-only token, so writing a comment fails there. That
is an error here (`CommentError`) and the caller falls back to the job summary (`write_summary`).
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from schema_impact.formats import MARKER

PAGE_SIZE = 100
TIMEOUT_SECONDS = 30
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class CommentError(Exception):
    """The comment could not be read or written. The message never contains the token."""


def upsert_comment(
    api_url: str,
    token: str,
    repo: str,
    pr: int,
    body: str,
    *,
    create_if_missing: bool = True,
    marker: str = MARKER,
) -> str:
    """Edit this tool's comment on the pull request, or add it. Returns what was done.

    "updated", "created", or "skipped" when there is no comment yet and `create_if_missing` is
    False (a review with nothing to say is not worth a new comment, but an old one that now says
    something else must still be corrected).
    """
    if not _REPO.match(repo):
        raise CommentError(f"not a repository name: {repo!r}")
    base = f"{api_url.rstrip('/')}/repos/{repo}"
    existing = _find(base, token, int(pr), marker)
    if existing is not None:
        _request("PATCH", f"{base}/issues/comments/{existing}", token, {"body": body})
        return "updated"
    if not create_if_missing:
        return "skipped"
    _request("POST", f"{base}/issues/{int(pr)}/comments", token, {"body": body})
    return "created"


def pr_number(event_path: str | Path) -> int:
    """The number of the pull request a workflow run is for, from its event file."""
    try:
        event = json.loads(Path(event_path).read_text())
        return int(event["pull_request"]["number"])
    except OSError as error:
        raise CommentError(f"cannot read the event file {event_path}: {error}") from error
    except (ValueError, KeyError, TypeError) as error:
        raise CommentError("the event is not a pull request event") from error


def write_summary(body: str, path: str | Path | None) -> bool:
    """Append the review to the job summary file; False when there is none to write to."""
    if not path:
        return False
    with open(path, "a") as summary:
        summary.write(body)
    return True


def _find(base: str, token: str, pr: int, marker: str) -> int | None:
    page = 1
    while True:
        comments = _request(
            "GET", f"{base}/issues/{pr}/comments?per_page={PAGE_SIZE}&page={page}", token
        )
        for comment in comments:
            if marker in (comment.get("body") or ""):
                return int(comment["id"])
        if len(comments) < PAGE_SIZE:
            return None
        page += 1


def _request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "schema-impact-agent",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raise CommentError(
            f"GitHub answered {error.code} to {method} {_path(url)}: {_message(error)}"
        ) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CommentError(f"could not reach GitHub at {_path(url)}: {error}") from error


def _path(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[-1].split("?")[0]


def _message(error: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(error.read()).get("message", ""))[:200]
    except (ValueError, AttributeError):
        return ""

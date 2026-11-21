"""The demo repository (`examples/pr_demo/`): a project to push to a new GitHub repository and
open two pull requests against, to see the Action comment.

It has to keep working without anyone looking at it, so these tests do what the README tells the
reader to do, on a temporary repository: apply each patch as a commit and review it. The comment
the README says to expect is the one the tool produces.
"""

import subprocess
from pathlib import Path

import pytest

from schema_impact.cli import main
from test_cli_git import commit_all, git

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "pr_demo"
TOY = ROOT / "examples" / "toy_project"
README = (DEMO / "README.md").read_text()

# patch, exit code with the default --fail-on, what the comment must say
PATCHES = [
    ("average-total.patch", 0, ["## Schema impact: 1 model needs review", "### Needs review"]),
    ("drop-customer-name.patch", 1, ["## Schema impact: 2 models breaking", "### Breaking"]),
]


def test_the_demo_project_is_the_toy_project():
    for path in [*TOY.rglob("*.sql")]:
        relative = path.relative_to(TOY)
        assert (DEMO / relative).read_bytes() == path.read_bytes(), relative
    assert {p.name for p in (DEMO / "models").glob("*.sql")} == {
        p.name for p in (TOY / "models").glob("*.sql")
    }


def demo_repo(tmp_path: Path, patch: str) -> Path:
    """A repository with the demo project on main, and one more commit that applies the patch."""
    repo = tmp_path / "repo"
    for source in [*(DEMO / "models").glob("*.sql"), DEMO / "schema.sql"]:
        target = repo / source.relative_to(DEMO)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "demo project")
    subprocess.run(
        ["git", "apply", str(DEMO / "patches" / patch)], cwd=repo, check=True, capture_output=True
    )
    commit_all(repo, "the change of the pull request")
    return repo


@pytest.mark.parametrize(("patch", "code", "says"), PATCHES)
def test_each_patch_applies_and_the_review_is_what_the_readme_promises(
    capsys, tmp_path, patch, code, says
):
    repo = demo_repo(tmp_path, patch)
    result = main(["diff", "--repo", str(repo), "--base", "HEAD~1", "--format", "markdown"])
    comment = capsys.readouterr().out
    assert result == code
    for text in says:
        assert text in comment
    assert comment in README, "the README must show the comment the tool really makes"


def test_the_readme_names_the_workflow_files_and_both_patches():
    for name in ["average-total.patch", "drop-customer-name.patch", "schema-impact.yml"]:
        assert name in README
    assert (DEMO / ".github" / "workflows" / "schema-impact.yml").is_file()

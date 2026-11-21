"""Read a project as it was at a git ref, without touching the working tree.

`git archive` writes the files of a commit as a tar stream, so nothing is checked out and the
repository, its index and the files in it stay as they are.
"""

import io
import subprocess
import tarfile
from pathlib import Path


class GitError(ValueError):
    """git could not give the files of a ref."""


def export_ref(repo: str | Path, ref: str, project_dir: str | None, dest: Path) -> Path:
    """Write the project folder `project_dir` (the root if None) of `ref` into `dest`."""
    tree = f"{ref}:{project_dir}" if project_dir and project_dir != "." else ref
    result = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", tree],
        capture_output=True,
    )
    if result.returncode != 0:
        reason = result.stderr.decode(errors="replace").strip()
        raise GitError(f"git could not read {tree!r} in {repo}: {reason}")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        archive.extractall(dest, filter="data")
    return dest

"""Check the files that pyproject.toml's per-file-target-version keeps on an older Python for newer stdlib APIs.

Ruff's target version catches newer syntax, not a call such as `shutil.rmtree(onexc=...)`. This
runs vermin, pinned in the dev group, on each listed file against its version. Mark a call that a
version check guards with a `# novermin` comment.

Usage: uv run python tools/check_min_python.py [file...]
With no files, checks every .py file git tracks or would add that the table names. A file the
table doesn't name, that doesn't exist, or that isn't a .py file in this repo, is skipped.
"""

import re
import shutil
import subprocess
import sys
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

VERMIN_ARGS = [
    "--violations",
    "--no-tips",
    "-q",
    "--no-config-file",
    "-vvv",  # the line of each violation
    "-p=1",  # a worker per core takes seconds to start on Windows
    "--eval-annotations",  # a def evaluates its annotations: `list[int]` there needs 3.9
]


def targets(pyproject):
    """per-file-target-version as {glob: (3, 9)}."""
    table = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]
    versions = {}
    for glob, version in table["per-file-target-version"].items():
        match = re.fullmatch(r"py3(\d+)", version)
        if not match:
            sys.exit(f"{pyproject}: unrecognised target version {version!r} for {glob}")
        versions[glob] = (3, int(match.group(1)))
    return versions


def target_of(relative, versions):
    """The lowest version whose glob matches a repo-relative path, or None.

    As in Ruff, `*` crosses folders and a glob also matches the file name alone. Where globs
    overlap, Ruff may pick a higher version than this does."""
    path = PurePosixPath(relative)
    return min(
        (
            version
            for glob, version in versions.items()
            if path.full_match(glob)  # `**/x.py` matches a top-level x.py
            or fnmatchcase(relative, glob)
            or fnmatchcase(path.name, glob)
        ),
        default=None,
    )


def repo_files(root):
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        + ["--", "*.py"],
        cwd=root,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        check=True,
    ).stdout
    return [name for name in listed.split("\0") if name]


def relative_files(root, paths):
    """The given paths as repo-relative POSIX paths, dropping anything outside root or not .py."""
    root = root.resolve()
    resolved = [Path(path).resolve() for path in paths]
    return [
        path.relative_to(root).as_posix()
        for path in resolved
        if path.suffix == ".py" and path.is_relative_to(root)
    ]


def check(root, paths=None):
    """Run vermin per target version; return 1 if any file needs a newer Python, else 0."""
    versions = targets(root / "pyproject.toml")
    files = repo_files(root) if paths is None else relative_files(root, paths)
    groups = {}
    for name in files:
        version = target_of(name, versions)
        # A missing file alone in vermin's list fails it without a word.
        if version and (root / name).is_file():
            groups.setdefault(version, []).append(name)
    if not groups:
        return 0
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("uv not found on PATH")
    # --project: vermin from this repo's dev group, even when root is a test repo.
    codes = [
        subprocess.run(
            [uv, "run", "--frozen", "--project", ROOT, "vermin"]
            + [f"-t={major}.{minor}-", *VERMIN_ARGS, *names],
            cwd=root,
            check=False,
        ).returncode
        for (major, minor), names in sorted(groups.items())
    ]
    return 1 if any(codes) else 0


def main(argv):
    return check(ROOT, argv or None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

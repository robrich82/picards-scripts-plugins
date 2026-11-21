"""Check the files that pyproject.toml's per-file-target-version keeps on an older Python for newer stdlib APIs.

Ruff's target version catches newer syntax, not a call such as `shutil.rmtree(onexc=...)`. This
runs vermin, pinned in the dev group, on each listed file against its version. Mark a call that a
version check guards with a `# novermin` comment.

Usage: uv run python tools/check_min_python.py [file...]
With no files, checks every .py file git tracks or would add that the table names. A file the
table doesn't name, or that isn't a .py file in this repo, is skipped.
"""

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent

# Annotations are evaluated when a function is defined, so `list[int]` needs 3.9 there too.
VERMIN_ARGS = [
    "--violations",
    "--no-tips",
    "-q",
    "--no-config-file",
    "--eval-annotations",
]


def targets(pyproject):
    """per-file-target-version as {glob: "3.9"}."""
    table = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]
    versions = {}
    for glob, version in table["per-file-target-version"].items():
        match = re.fullmatch(r"py3(\d+)", version)
        if not match:
            sys.exit(f"{pyproject}: unrecognised target version {version!r} for {glob}")
        versions[glob] = f"3.{match.group(1)}"
    return versions


def target_of(relative, versions):
    """The lowest version whose glob matches a repo-relative path, or None.

    Like Ruff, a glob without a slash also matches the file name alone."""
    path = PurePosixPath(relative)
    found = [
        version
        for glob, version in versions.items()
        if path.full_match(glob) or ("/" not in glob and path.name == glob)
    ]
    return min(found, key=lambda v: tuple(map(int, v.split(".")))) if found else None


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
    found, root = [], root.resolve()
    for path in paths:
        resolved = Path(path).resolve()
        if resolved.suffix == ".py" and resolved.is_relative_to(root):
            found.append(resolved.relative_to(root).as_posix())
    return found


def check(root, paths=None):
    """Run vermin per target version; return 1 if any file needs a newer Python, else 0."""
    versions = targets(root / "pyproject.toml")
    files = repo_files(root) if paths is None else relative_files(root, paths)
    groups = {}
    for name in files:
        version = target_of(name, versions)
        if version:
            groups.setdefault(version, []).append(name)
    if not groups:
        return 0
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("uv not found on PATH")
    failed = 0
    # --project: vermin comes from this repo's dev group whichever tree is checked.
    for version, names in sorted(groups.items()):
        result = subprocess.run(
            [uv, "run", "--frozen", "--project", ROOT, "vermin"]
            + [f"-t={version}-", *VERMIN_ARGS, *names],
            cwd=root,
            check=False,
        )
        failed = failed or result.returncode
    return 1 if failed else 0


def main(argv):
    return check(ROOT, argv or None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

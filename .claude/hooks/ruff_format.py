"""PostToolUse hook: ruff-format an edited .py file and sort its imports, with the repo's pinned Ruff.

Only import sorting is auto-fixed: a full `ruff check --fix` would delete an import added one edit
before the code that uses it. The repo root comes from the file's path, not CLAUDE_PROJECT_DIR,
which is the main checkout even when the edit is in a worktree.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def main():
    try:
        file_path = json.load(sys.stdin)["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return 0
    path = Path(file_path).resolve()
    if path.suffix not in (".py", ".pyi") or not path.is_file():
        return 0
    root = repo_root(path)
    uv = shutil.which("uv")
    if root is None or uv is None:
        return 0
    if any(path.is_relative_to(root / ".picard" / d) for d in ("src", "venv")):
        return 0  # Picard's upstream source and the test venv
    for args in (["format"], ["check", "--fix", "--select", "I"]):
        result = subprocess.run(
            [uv, "run", "--quiet", "--project", str(root), "ruff", *args, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            check=False,
        )
        if result.returncode:
            sys.stderr.write((result.stderr or result.stdout)[-2000:])
            return 2  # PostToolUse: stderr goes back to Claude
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""PostToolUse hook: ruff-format an edited .py file and sort its imports, with the repo's pinned Ruff.

Only import sorting is auto-fixed: a full `ruff check --fix` would delete an import added one edit
before the code that uses it. The repo root comes from the file's path, not CLAUDE_PROJECT_DIR,
which is the main checkout even when the edit is in a worktree. When uv or Ruff can't run, Claude
is told the file went unformatted; only Ruff rejecting the file itself is reported as an error.
Edits made through a shell command are not seen.
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


def edited_path():
    """The edited file from the hook JSON on stdin, or None."""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        file_path = data["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return None
    return Path(file_path).resolve() if isinstance(file_path, str) else None


def note(text):
    """Tell Claude something without blocking (exit 0 with additionalContext)."""
    output = {"hookEventName": "PostToolUse", "additionalContext": text}
    print(json.dumps({"hookSpecificOutput": output}))
    return 0


def ruff(uv, root, *args, timeout):
    # --frozen: run from uv.lock as it is, never re-lock it as a side effect of formatting.
    return subprocess.run(
        [uv, "run", "--frozen", "--quiet", "--project", str(root), "ruff", *args],
        capture_output=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=timeout,
        check=False,
    )


def last_line(result):
    return ((result.stderr or result.stdout).strip().splitlines() or ["no output"])[-1]


def main():
    path = edited_path()
    if path is None or path.suffix not in (".py", ".pyi") or not path.is_file():
        return 0
    root = repo_root(path)
    if root is None:
        return 0
    if any(path.is_relative_to(root / ".picard" / d) for d in ("src", "venv")):
        return 0  # Picard's upstream source and the test venv
    uv = shutil.which("uv")
    if uv is None:
        return note(
            f"{path.name} was not formatted: uv is not on PATH (`mise install`)."
        )
    try:
        # uv and Ruff both exit 2 on failure, so check Ruff starts before blaming the file.
        started = ruff(uv, root, "--version", timeout=45)
        if started.returncode:
            return note(
                f"{path.name} was not formatted: Ruff didn't start ({last_line(started)})."
            )
        for args in (["format"], ["check", "--fix", "--select", "I"]):
            result = ruff(uv, root, *args, str(path), timeout=10)
            if result.returncode:
                sys.stderr.write((result.stderr or result.stdout)[-2000:])
                return 2  # PostToolUse: stderr goes back to Claude
    except subprocess.TimeoutExpired:
        return note(f"{path.name} was not formatted: Ruff timed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

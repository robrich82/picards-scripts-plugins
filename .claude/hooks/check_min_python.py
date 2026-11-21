"""PostToolUse hook: run tools/check_min_python.py on an edited .py file and report violations to Claude.

The tool needs a newer Python than the one hooks run on, so it runs through `uv run` in the repo
the file is in: the repo root comes from the file's path, not CLAUDE_PROJECT_DIR, which is the
main checkout even when the edit is in a worktree. A file per-file-target-version doesn't name,
or a repo without the tool, passes silently. When uv or the tool can't run, Claude is told the
file went unchecked. Edits made through a shell command are not seen.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = Path("tools") / "check_min_python.py"


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


def main():
    path = edited_path()
    if path is None or path.suffix != ".py" or not path.is_file():
        return 0
    root = repo_root(path)
    if root is None or not (root / TOOL).is_file():
        return 0
    uv = shutil.which("uv")
    if uv is None:
        return note(
            f"{path.name} was not checked for newer stdlib APIs: uv is not on PATH (`mise install`)."
        )
    try:
        # --frozen: run from uv.lock as it is, never re-lock it as a side effect of a check.
        result = subprocess.run(
            [uv, "run", "--frozen", "--quiet", "--project", str(root)]
            + ["python", str(root / TOOL), str(path)],
            capture_output=True,
            encoding="utf-8",
            errors="backslashreplace",
            timeout=50,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return note(f"{path.name} was not checked for newer stdlib APIs: timed out.")
    if result.returncode == 1 and result.stdout.strip():  # vermin's violations
        sys.stderr.write(
            f"{path.name} uses something newer than its per-file-target-version. "
            "Mark a call a version check guards `# novermin`.\n" + result.stdout[-2000:]
        )
        return 2  # PostToolUse: stderr goes back to Claude
    if result.returncode:
        last = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return note(
            f"{path.name} was not checked for newer stdlib APIs: the check failed ({last})."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

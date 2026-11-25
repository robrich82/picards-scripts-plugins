"""PostToolUse hook: run tools/check_min_python.py on an edited .py file and report violations to Claude.

The tool needs the dev group's vermin and a newer Python than hooks run on, so it runs through
`uv run`, from the edited file's repo when _hook_common.trusted() allows it.

A file that per-file-target-version in pyproject.toml doesn't name passes silently; when uv or the
tool can't run, Claude is told the file went unchecked, named or not. Edits made through a shell
command are not seen.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import _hook_common as common

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
    if not common.trusted(root):
        return note(
            f"{path.name} was not checked for newer stdlib APIs: "
            f"{common.untrusted_reason(root)}, so its tools/ were not run."
        )
    uv = common.find_uv()
    if uv is None:
        return note(
            f"{path.name} was not checked for newer stdlib APIs: uv is not on PATH (`mise install`)."
        )
    try:
        # --frozen: run from uv.lock as it is, never re-lock it as a side effect of a check.
        result = subprocess.run(
            [uv, "run", "--frozen", "--quiet", "--project", str(root)]
            + ["python", str(root / TOOL), str(path)],
            # Not Claude's cwd: if UV is unset, the tool's shutil.which("uv") searches it first
            # on Windows.
            cwd=str(root),
            capture_output=True,
            encoding="utf-8",
            errors="backslashreplace",
            # Else vermin prints a path the console code page can't encode as a traceback.
            env=dict(os.environ, PYTHONUTF8="1"),
            timeout=50,  # under the 60 in settings.json
            check=False,
        )
    except subprocess.TimeoutExpired:
        return note(f"{path.name} was not checked for newer stdlib APIs: timed out.")
    except OSError as error:
        return note(
            f"{path.name} was not checked for newer stdlib APIs: uv did not start ({error})."
        )
    # The tool exits 1 both for violations, which vermin prints on stdout, and on a crash.
    if result.returncode == 1 and result.stdout.strip():
        sys.stderr.write(
            f"{path.name} uses a stdlib API newer than its per-file-target-version in "
            "pyproject.toml. Use an older one, or mark the call `# novermin` if a version "
            "check guards it.\n" + result.stdout[:2000]
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

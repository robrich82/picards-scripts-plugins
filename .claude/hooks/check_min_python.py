"""PostToolUse hook: run tools/check_min_python.py on an edited .py file and report violations to Claude.

The tool needs the dev group's vermin and a newer Python than hooks run on, so it runs through
`uv run`. The repo comes from the file's path, so an edit in a worktree runs that worktree's tool
(CLAUDE_PROJECT_DIR is the session's checkout, not necessarily the edited file's).

Running a root's tools/ runs its code, so only trusted_roots() are run; any other root holding the
tool, even a folder inside this repo, gets a note instead. Trust is by location, not content: a
worktree of a fork's branch still runs that branch's tools/.

A file that per-file-target-version in pyproject.toml doesn't name passes silently; when uv or the
tool can't run, Claude is told the file went unchecked, named or not. Edits made through a shell
command are not seen.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = Path("tools") / "check_min_python.py"


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def linked_path(file, prefix=""):
    """The path written in a git link file after prefix, resolved against the file's folder.
    None when the file is missing, unreadable or holds no such path."""
    try:
        text = file.read_text(encoding="utf-8")
        if not text.startswith(prefix):
            return None
        target = text[len(prefix) :].strip()
        return (file.parent / target).resolve() if target else None
    except (OSError, ValueError):
        return None


def trusted_roots(project):
    """CLAUDE_PROJECT_DIR, its main checkout and the worktrees its git common dir records.
    Read from .git files, not `git rev-parse`, so it works without git on PATH. A worktree's own
    .git is read only to confirm it still links back, as `git worktree repair` does: an entry
    whose folder was deleted must not trust whatever later appears at that path."""
    roots = {project}  # its .claude/hooks run anyway, this one among them
    dot_git = project / ".git"
    common = dot_git.resolve() if dot_git.is_dir() else linked_path(dot_git, "gitdir:")
    if common is None:
        return roots
    common = linked_path(common / "commondir") or common  # the project is a worktree
    if common.name == ".git":  # else bare or --separate-git-dir: no checkout beside it
        roots.add(common.parent)
    try:
        entries = list(common.glob("worktrees/*"))
    except (OSError, ValueError):  # a NUL in .git gets this far on newer Pythons
        return roots
    for entry in entries:
        worktree_git = linked_path(entry / "gitdir")  # "<worktree>/.git"
        if worktree_git is not None and linked_path(worktree_git, "gitdir:") == entry:
            roots.add(worktree_git.parent)
    return roots


def trusted(root):
    """True when root is one of trusted_roots()."""
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    return bool(project) and root.resolve() in trusted_roots(Path(project).resolve())


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
    if not trusted(root):
        return note(
            f"{path.name} was not checked for newer stdlib APIs: {root} is not "
            "CLAUDE_PROJECT_DIR or one of its git worktrees, so its tools/ were not run."
        )
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

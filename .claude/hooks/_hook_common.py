"""Shared by the PostToolUse hooks: which repo roots they may run code from, and where uv is.

Not a hook itself. settings.json runs hooks with -I, so each hook puts this folder on sys.path.

The repo root comes from the edited file's path, so an edit in a worktree runs that worktree's
code (CLAUDE_PROJECT_DIR is the session's checkout, not necessarily the edited file's). Running a
root's tools/, uv project or .picard/venv runs its code, so only trusted_roots() are run; any
other root, even a folder inside this repo, gets a note instead. Trust is by location, not
content: a worktree of a fork's branch still runs that branch's code.
"""

import os
import sys
from pathlib import Path


def linked_path(file, prefix=""):
    """The path written in a git link file after prefix, resolved against the file's folder.
    None when the file is missing, unreadable or holds no such path."""
    try:
        text = file.read_text(encoding="utf-8")
        if not text.startswith(prefix):
            return None
        target = text[len(prefix) :].strip()
        if (
            not target or "\0" in target
        ):  # newer Pythons' resolve() lets a NUL through on Windows
            return None
        return (file.parent / target).resolve()
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
    for entry in common.glob("worktrees/*"):
        worktree_git = linked_path(entry / "gitdir")  # "<worktree>/.git"
        if worktree_git is not None and linked_path(worktree_git, "gitdir:") == entry:
            roots.add(worktree_git.parent)
    return roots


def trusted(root):
    """True when root is one of trusted_roots()."""
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    return bool(project) and root.resolve() in trusted_roots(Path(project).resolve())


def untrusted_reason(root):
    return f"{root} is not CLAUDE_PROJECT_DIR or one of its git worktrees"


def find_uv():
    """uv from PATH's absolute folders, or None. Not shutil.which: on Windows it looks in the
    current folder first, which is wherever Claude is working, unless
    NoDefaultCurrentDirectoryInExePath is set and Python is 3.12 or later. Only uv.exe on
    Windows: a .cmd or .bat runs through cmd.exe, which would parse the paths Claude passes it."""
    windows = sys.platform == "win32"
    name = "uv.exe" if windows else "uv"
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        # Skips "", "." and other relative folders, and on Windows "\folder", which Python
        # before 3.13 calls absolute but is on the current folder's drive.
        drive = os.path.splitdrive(folder)[0] if windows else "/"
        path = os.path.join(folder, name)
        absolute = bool(drive) and os.path.isabs(folder)
        if absolute and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None

"""PreToolUse hook: the CLAUDE.md gotchas that GH_REPO in settings.json doesn't cover.

- `gh repo view/fork/sync/...` without a repo ignore GH_REPO and use the git remote: upstream
  Picard in .picard/src. Ask, unless the command names this repo.
- make install* and tools/install.py write the user's real Picard config: ask first.
- The Edit/Write tools may not touch .picard/, the upstream Picard clone and test venv that
  tools/run_tests.py manages. Shell commands that write there are not caught.

The command checks are best-effort text matches, not a shell parser. The install check skips
quoted text, so a commit message doesn't count, but neither does `python "tools/install.py"`;
an unquoted mention such as `ruff check tools/install.py` asks too.
"""

import json
import re
import sys
from pathlib import Path

REPO = "robrich82/picards-scripts-plugins"
# gh repo subcommands with no -R flag, which act on the current directory's repo when given none.
GH_REPO_HERE = re.compile(
    r"""\bgh(?:\.exe)?["']?\s+repo\s+"""
    r"(?:view|fork|sync|edit|set-default|delete|archive|unarchive)\b",
    re.IGNORECASE,
)
QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
INSTALL = re.compile(
    r"\binstall\.py\b|tools\.install\b|(?:^|[\s;&|(])make\s[^;&|\n]*\binstall",
    re.IGNORECASE,
)

GH_REASON = (
    f"gh repo subcommands ignore GH_REPO: name the repo (gh repo view {REPO}). "
    "Without it gh uses the git remote, and .picard/src is a metabrainz/picard clone."
)
INSTALL_REASON = (
    "This may install into the real Picard config and plugin folder. "
    "Only run it when the user asked for an install."
)
PICARD_REASON = (
    ".picard/ holds the upstream Picard clone and test venv that tools/run_tests.py "
    "builds; edits there change what the tests run against."
)


def decision(kind, reason):
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": kind,
        "permissionDecisionReason": reason,
    }
    return {"hookSpecificOutput": output}


def check_command(command):
    if GH_REPO_HERE.search(command) and REPO not in command.lower():
        return decision("ask", GH_REASON)
    if INSTALL.search(QUOTED.sub("''", command)):
        return decision("ask", INSTALL_REASON)
    return None


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def check_path(file_path):
    path = Path(file_path).resolve()
    root = repo_root(path)
    if root is not None and path.is_relative_to(root / ".picard"):
        return decision("deny", PICARD_REASON)
    return None


def main():
    try:
        tool_input = json.loads(sys.stdin.buffer.read().decode("utf-8"))["tool_input"]
    except (ValueError, KeyError, TypeError):
        return 0
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    result = None
    if isinstance(command, str):
        result = check_command(command)
    elif isinstance(path, str):
        result = check_path(path)
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

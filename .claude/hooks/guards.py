"""PreToolUse hook: the CLAUDE.md gotchas about .picard/ and installs, which permission rules miss.

- gh run in .picard/src targets the upstream metabrainz/picard clone. Ask before a gh command
  when the shell's cwd is inside .picard/ or the command mentions .picard, unless that command
  passes -R or names this repo.
- mise's install-all, install-plugin and install-scripts tasks, tools/install.py and
  `picard-cli plugins install` write the user's real Picard config: ask first.
- Deny Edit/Write/MultiEdit/NotebookEdit inside .picard/, the upstream Picard clone and test
  venv that tools/run_tests.py manages. Shell commands that write there are not caught.

The command checks are best-effort text matches per `;`/`&`/`|`/newline-separated segment, not a
shell parser. Quoted text with spaces is skipped: a simply-quoted commit message, but also
`bash -c "mise run install-all"`, and a task name that reaches mise through a pipe or variable
isn't seen. Heredoc bodies and unquoted mentions such as `git add tools/install.py`
ask too.
"""

import json
import os
import re
import sys
from pathlib import Path

GH = re.compile(r"\bgh(?:\.exe)?\s", re.IGNORECASE)
GH_REPO_FLAG = re.compile(r"\s(?:-R|--repo)(?![a-z])")
PICARD_DIR = re.compile(r"(?<![\w.])\.picard(?![\w.-])", re.IGNORECASE)
INSTALLER = re.compile(r"\binstall\.py\b|tools\.install\b", re.IGNORECASE)
PLUGINS = re.compile(r"\bplugins\s", re.IGNORECASE)
PLUGINS_INSTALL = re.compile(r"\sinstall\b", re.IGNORECASE)
# `)` and a backtick end `$(which mise)` and `(Get-Command mise)`.
MISE = re.compile(r"\bmise(?:\.exe)?(?=[\s)`]|$)", re.IGNORECASE)
# An install task's name, or a pattern such as `install*` or `*` that mise expands to one.
INSTALL_TASK = re.compile(
    r"(?<![\w.-])install-(?:all|plugin|scripts)(?![\w.-])|[*?\[{]"
)
MSYS_DRIVE = re.compile(r"^/([a-zA-Z])/")
# A quote opens only after a space, `=` or `(`: the apostrophe in `it's` doesn't.
QUOTED = re.compile(r"""(?<![^\s=(])(?:'[^']*'|"[^"]*")""")
HERESTRING_OPEN = re.compile(r"""@(['"])\r?\n""")
QUOTED_EXE = re.compile(r"(?:^|[/\\])(gh|mise)(?:\.exe)?$", re.IGNORECASE)
CONTINUATION = re.compile(r"[\\`]\r?\n")
# Not parens: `mise -C $(git rev-parse --show-toplevel) run install-all` and
# `gh pr view $(...) -R x` stay one segment.
SEPARATOR = re.compile(r"[;&|\n]")
REPO = "robrich82/picards-scripts-plugins"

# Shown to the user in the permission prompt (to Claude only when a -p run auto-denies it).
GH_REASON = (
    "This gh command runs in or mentions .picard/, a metabrainz/picard clone: "
    "without -R it may act on upstream Picard, not this repo."
)
INSTALL_REASON = "Installs into your real Picard config and plugin folder, or mentions the installer."
# Shown to Claude.
PICARD_REASON = (
    ".picard/ holds the upstream Picard clone and test venv that tools/run_tests.py "
    "builds; edits there change what the tests run against. Edit the repo's own files."
)


def decision(kind, reason):
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": kind,
        "permissionDecisionReason": reason,
    }
    return {"hookSpecificOutput": output}


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def inside_picard(file_path):
    """True if file_path is in, or is, a .picard/ folder at a repo root."""
    if os.name == "nt":
        file_path = MSYS_DRIVE.sub(r"\1:/", file_path)  # /c/Users/... from Git Bash
    path = Path(file_path).resolve()
    root = repo_root(path)
    return root is not None and path.is_relative_to(root / ".picard")


def strip_herestrings(text):
    """Drop PowerShell @'...'@ bodies. A scan, not a lazy regex, which is quadratic unclosed."""
    parts, start = [], 0
    while True:
        opener = HERESTRING_OPEN.search(text, start)
        if not opener:
            break
        end = text.find(f"\n{opener.group(1)}@", opener.end() - 1)
        if end < 0:
            break
        parts += [text[start : opener.start()], "''"]
        start = end + 3
    return "".join(parts) + text[start:]


def unquote(match):
    """A quoted path to gh or mise becomes its name, other single words stay, prose goes."""
    text = match.group()[1:-1]
    exe = QUOTED_EXE.search(text)
    if exe:
        return exe.group(1)
    return text if text and not any(c.isspace() for c in text) else "''"


def installs(segment):
    if INSTALLER.search(segment):
        return True
    plugins = PLUGINS.search(segment)
    if plugins and PLUGINS_INSTALL.search(segment, plugins.end() - 1):
        return True
    mise = MISE.search(segment)
    return bool(mise and INSTALL_TASK.search(segment, mise.end()))


def check_command(command, cwd):
    joined = CONTINUATION.sub(" ", command)
    # Raw text, so a quoted path like "$ROOT/.picard/src" counts.
    near_picard = bool(PICARD_DIR.search(joined)) or (
        isinstance(cwd, str) and bool(cwd) and inside_picard(cwd)
    )
    text = QUOTED.sub(unquote, strip_herestrings(joined))
    reasons = []
    for segment in SEPARATOR.split(text):
        names_repo = GH_REPO_FLAG.search(segment) or REPO in segment.lower()
        if near_picard and GH.search(segment) and not names_repo:
            reasons.append(GH_REASON)
        if installs(segment):
            reasons.append(INSTALL_REASON)
    return decision("ask", " ".join(dict.fromkeys(reasons))) if reasons else None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        tool_input = data["tool_input"]
    except (ValueError, KeyError, TypeError):
        return 0
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    result = None
    if isinstance(command, str):
        result = check_command(command, data.get("cwd"))
    elif isinstance(path, str) and inside_picard(path):
        result = decision("deny", PICARD_REASON)
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

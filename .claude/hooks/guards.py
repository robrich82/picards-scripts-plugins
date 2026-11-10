"""PreToolUse hook: the CLAUDE.md gotchas about .picard/ and installs, which permission rules miss.

- gh run in .picard/src targets the upstream metabrainz/picard clone. Ask before a gh command
  when the shell's cwd is inside .picard/ or the command mentions .picard, unless it passes -R.
- make install*, tools/install.py and `picard-cli plugins install` write the user's real Picard
  config: ask first.
- Deny Edit/Write/MultiEdit/NotebookEdit inside .picard/, the upstream Picard clone and test
  venv that tools/run_tests.py manages. Shell commands that write there are not caught.

The command checks are best-effort text matches per `;`/`&`/`|`/newline-separated segment, not a
shell parser. Quoted text with spaces is skipped: a simply-quoted commit message, but also
`bash -c "make install"`. Heredoc bodies and unquoted mentions such as `git add tools/install.py`
ask too.
"""

import json
import re
import sys
from pathlib import Path

GH = re.compile(r"\bgh(?:\.exe)?\s", re.IGNORECASE)
GH_REPO_FLAG = re.compile(r"\s(?:-R|--repo)")
PICARD_DIR = re.compile(r"\.picard(?![\w.-])", re.IGNORECASE)
INSTALLER = re.compile(
    r"\binstall\.py\b|tools\.install\b|\bplugins\s+install\b", re.IGNORECASE
)
MAKE = re.compile(r"\bmake(?:\.exe)?\s", re.IGNORECASE)
INSTALL_TARGET = re.compile(r"\sinstall[\w-]*(?![\w.])", re.IGNORECASE)
QUOTED = re.compile(r"""'[^']*'|"[^"]*\"""")
QUOTED_EXE = re.compile(r"(?:^|[/\\])(gh|make)(?:\.exe)?$", re.IGNORECASE)
CONTINUATION = re.compile(r"[\\`]\r?\n")
SEPARATOR = re.compile(r"[;&|()\n]")

# The ask reasons are shown to the user in the permission prompt, not to Claude.
GH_REASON = (
    "gh run in .picard/ acts on the upstream metabrainz/picard clone, not this repo."
)
INSTALL_REASON = "Installs into your real Picard config and plugin folder, or mentions the installer."
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
    path = Path(file_path).resolve()
    root = repo_root(path)
    return root is not None and path.is_relative_to(root / ".picard")


def unquote(match):
    """A quoted path to gh or make becomes its name, other single words stay, prose goes."""
    text = match.group()[1:-1]
    exe = QUOTED_EXE.search(text)
    if exe:
        return exe.group(1)
    return text if text and not any(c.isspace() for c in text) else "''"


def installs(segment):
    make = MAKE.search(segment)
    # Search from the space after `make`, so an install target can't match earlier text.
    target = make and INSTALL_TARGET.search(segment, make.end() - 1)
    return bool(INSTALLER.search(segment) or target)


def check_command(command, cwd):
    text = QUOTED.sub(unquote, CONTINUATION.sub(" ", command))
    near_picard = bool(PICARD_DIR.search(text)) or (
        isinstance(cwd, str) and bool(cwd) and inside_picard(cwd)
    )
    reasons = []
    for segment in SEPARATOR.split(text):
        if near_picard and GH.search(segment) and not GH_REPO_FLAG.search(segment):
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

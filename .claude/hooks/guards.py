"""PreToolUse hook: the CLAUDE.md gotchas, enforced on shell commands and file-edit tools.

- gh pr/issue/run/... and argument-less gh repo view/edit/... need -R/--repo (or GH_REPO set):
  in .picard/src, gh resolves to upstream Picard. gh api paths can't use {owner}/{repo}.
- make install* and running tools/install.py write the user's real Picard config: ask first.
- The Edit/Write tools may not touch .picard/, the upstream Picard clone and test venv that
  tools/run_tests.py manages. Shell commands that write there are not caught.

Commands are read roughly as a shell would: quoted strings and heredoc bodies are dropped first,
so a commit message or PR body that mentions `gh pr list` or `make install` doesn't count.
"""

import json
import os
import re
import sys
from pathlib import Path

GH_REPO_COMMANDS = {
    "pr",
    "issue",
    "run",
    "workflow",
    "release",
    "label",
    "cache",
    "secret",
    "variable",
    "browse",
    "ruleset",
}
# gh repo subcommands that act on the current directory's repo when given none.
GH_REPO_ACTIONS = {
    "view",
    "edit",
    "sync",
    "set-default",
    "delete",
    "archive",
    "unarchive",
    "rename",
    "deploy-key",
    "autolink",
}
GH_API_PLACEHOLDER = re.compile(r"\{owner\}|\{repo\}")
GH_REPO_ENV = re.compile(
    r"(?:^|\s)(?:export\s+)?GH_REPO=|\$env:GH_REPO\s*=", re.IGNORECASE
)
PYTHON = re.compile(r"python[\d.]*w?|py")
# Flags whose value is a separate word, for python and for uv run.
PYTHON_VALUE_FLAGS = {"-X", "-W", "-Q"}
UV_VALUE_FLAGS = {
    "--with",
    "--with-editable",
    "--with-requirements",
    "--python",
    "-p",
    "--project",
    "--directory",
    "--package",
    "--extra",
    "--group",
    "--env-file",
    "--index",
    "--index-url",
}
# Words that run the command after them.
WRAPPERS = {"sudo", "env", "time", "command", "exec", "nohup", "xargs", "timeout"}

# A quoted path to gh (PowerShell `& "C:\...\gh.exe"`) stays a gh command after quotes go.
QUOTED_GH = re.compile(r"""["']([^"'\n]*[/\\])?gh(?:\.exe)?["']""", re.IGNORECASE)
HEREDOC = re.compile(
    r"""<<-?[ \t]*(['"]?)(\w+)\1[^\n]*\n(?:.*?\n)?[ \t]*\2(?=[\s)]|$)""", re.DOTALL
)
HERESTRING = re.compile(r"""@(['"])\r?\n.*?\r?\n\1@""", re.DOTALL)
QUOTED = re.compile(r"""'[^']*'|"(?:\\.|`.|[^"\\`])*\"""", re.DOTALL)
CONTINUATION = re.compile(r"[\\`]\r?\n")
SEPARATOR = re.compile(r"&&|\|\||\$\(|[;|&\n(){}`]")

GH_REASON = (
    "Pass -R robrich82/picards-scripts-plugins to gh: .picard/src is a metabrainz/picard "
    "clone, and gh run from there targets upstream."
)
GH_API_REASON = (
    "Write the repo into the gh api path (repos/robrich82/picards-scripts-plugins/...): "
    "{owner}/{repo} resolve from the current directory, upstream Picard in .picard/src."
)
INSTALL_REASON = (
    "This installs into the real Picard config and plugin folder. "
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


def name_of(word):
    name = os.path.basename(word.replace("\\", "/")).lower()
    return name.removesuffix(".exe")


def commands(text):
    """Each simple command in a shell command line, as words, with quoted text removed."""
    text = QUOTED_GH.sub(" gh ", text)
    text = HERESTRING.sub("''", HEREDOC.sub(" ", text))
    text = CONTINUATION.sub(" ", QUOTED.sub("''", text))
    for part in SEPARATOR.split(text):
        words = part.split()
        # Skip VAR=value, wrappers like sudo, and a duration (`timeout 10 gh ...`).
        while words and (
            re.match(r"\w+=|\d+[smhd]?$", words[0]) or words[0] in WRAPPERS
        ):
            words.pop(0)
        if words and name_of(words[0]) == "mise" and "--" in words:
            words = words[words.index("--") + 1 :]
        if words:
            yield name_of(words[0]), words[1:]


def positionals(args, value_flags):
    """Arguments that aren't flags or a flag's separate value."""
    result, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in value_flags
        else:
            result.append(arg)
    return result


def check_gh(args, raw):
    if any(
        a in ("-R", "--repo", "-h", "--help") or a.startswith(("-R", "--repo="))
        for a in args
    ):
        return None
    words = positionals(args, {"-R", "--repo"})
    if not words or any(w.startswith("https://github.com/") for w in words):
        return None
    if words[0] in GH_REPO_COMMANDS:
        return decision("deny", GH_REASON)
    repo_action = words[0] == "repo" and words[1:2] and words[1] in GH_REPO_ACTIONS
    if repo_action and not any("/" in w for w in words[2:]):
        return decision("deny", GH_REASON)
    if words[0] == "api" and GH_API_PLACEHOLDER.search(raw):
        return decision("deny", GH_API_REASON)
    return None


def runs_installer(args):
    """True if a python command line runs tools/install.py."""
    for i, arg in enumerate(args):
        if arg == "-m":
            return args[i + 1 : i + 2] in (["tools.install"], ["install"])
        if arg == "-c":
            return False
        if not arg.startswith("-") and (
            i == 0 or args[i - 1] not in PYTHON_VALUE_FLAGS
        ):
            return name_of(arg) == "install.py"
    return False


def check_install(name, args):
    if name == "make":
        return any(a.startswith("install") for a in positionals(args, {"-C", "-f"}))
    if PYTHON.fullmatch(name):
        return runs_installer(args)
    if name == "uv" and args[:1] == ["run"]:
        target = positionals(args[1:], UV_VALUE_FLAGS)
        if not target:
            return False
        if name_of(target[0]) == "install.py":
            return True
        rest = args[args.index(target[0]) + 1 :]
        return bool(PYTHON.fullmatch(name_of(target[0]))) and runs_installer(rest)
    return False


def check_command(raw):
    gh_repo_set = bool(GH_REPO_ENV.search(QUOTED.sub("''", raw)))
    ask = None
    for name, args in commands(raw):
        if name == "gh" and not gh_repo_set:
            denied = check_gh(args, raw)
            if denied:
                return denied  # a deny anywhere beats an ask elsewhere
        if check_install(name, args):
            ask = decision("ask", INSTALL_REASON)
    return ask


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

"""Which Checks jobs a run needs, for the changes job in .github/workflows/checks.yml.

Usage: python3 tools/ci_changes.py >> "$GITHUB_OUTPUT"
Prints one `<output>=true|false` line per entry in OUTPUTS. On a pull_request run (from
GITHUB_EVENT_NAME) they come from the paths its merge commit changes against the base, HEAD^1;
any other run needs everything. A path no rule names runs every job, so a new kind of file can
never skip CI.
"""

import fnmatch
import os
import subprocess
import sys

OUTPUTS = ("lint", "test")  # test gates both test and test-windows

# First match wins; fnmatch's * also matches "/". Each rule lists the outputs whose jobs read the
# path. lint: ruff and vermin over the Python, configured by any pyproject.toml or ruff.toml.
# test: pytest and ty, and the files the tests read: the scripts, the plugin, settings.json,
# release-plugin.yml, renovate.json and the files it manages (README.md's Picard version).
# mise.toml and mise.lock install both jobs' uv, and mise.toml defines the tasks they run.
RULES = (
    ("*.py", OUTPUTS),
    ("*.pyi", OUTPUTS),
    ("*.ipynb", OUTPUTS),
    ("*pyproject.toml", OUTPUTS),
    ("*ruff.toml", OUTPUTS),
    ("uv.lock", OUTPUTS),
    ("mise.toml", OUTPUTS),
    ("mise.lock", OUTPUTS),
    ("pytest.ini", ("test",)),
    ("tagger-scripts/*", ("test",)),
    ("plugins/*", ("test",)),
    (".claude/settings.json", ("test",)),
    (".github/workflows/release-plugin.yml", ("test",)),
    ("renovate.json", ("test",)),
    ("README.md", ("test",)),
    ("CLAUDE.md", ()),
    (".claude/skills/*", ()),
    (".claude/agents/*", ()),
    ("LICENSE", ()),
    (".git-blame-ignore-revs", ()),
)


def outputs_for(path):
    for pattern, outputs in RULES:
        if fnmatch.fnmatchcase(path, pattern):
            return set(outputs)
    return set(OUTPUTS)


# The run uses the pull request's own copy of these, so a change to them runs everything: a
# broken RULES can't skip the tests that would catch it.
SELF = ("tools/ci_changes.py", ".github/workflows/checks.yml")


def needed(paths):
    paths = list(paths)
    if any(path in SELF for path in paths):
        return set(OUTPUTS)
    return set().union(*map(outputs_for, paths))


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def changed_paths():
    """The merge commit's changes against the base it merges into. -z: no quoting of unusual
    names. --no-renames: a move lists the path it leaves too."""
    # Fail, not guess, if HEAD isn't the merge commit (a checkout of the PR's head, say).
    git("rev-parse", "--verify", "HEAD^2")
    diff = git("diff", "--name-only", "--no-renames", "-z", "HEAD^1", "HEAD")
    return [path for path in diff.split("\0") if path]


def main():
    pull_request = os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    outputs = needed(changed_paths()) if pull_request else set(OUTPUTS)
    for output in OUTPUTS:
        print(f"{output}={str(output in outputs).lower()}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(f"git {error.cmd[1]} failed: {error.stderr.strip()}")

"""tools/ci_changes.py on real pull request merge commits, and checks.yml wired to what it prints."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ci_changes", ROOT / "tools" / "ci_changes.py"
)
assert _spec and _spec.loader
ci_changes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_changes)

WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/checks.yml").read_text("utf-8"))
JOBS = WORKFLOW["jobs"]
BOTH, TEST, NONE = {"lint", "test"}, {"test"}, set()
TRACKED = subprocess.run(
    ["git", "ls-files", "-z"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=True,
).stdout.split("\0")[:-1]


@pytest.mark.parametrize(
    ("path", "outputs"),
    [
        (".github/workflows/checks.yml", BOTH),
        ("tools/ci_changes.py", BOTH),
        (".claude/skills/x/helper.py", BOTH),  # *.py comes before the docs rules
        ("plugins/x/stub.pyi", BOTH),
        ("notebooks/x.ipynb", BOTH),
        ("pyproject.toml", BOTH),
        ("plugins/x/pyproject.toml", BOTH),  # ruff reads nested configs
        ("plugins/x/ruff.toml", BOTH),
        ("tagger-scripts/.ruff.toml", BOTH),
        ("uv.lock", BOTH),
        ("mise.toml", BOTH),
        ("mise.lock", BOTH),
        ("pytest.ini", TEST),
        ("tagger-scripts/01-remove-hyphens-in-title.ptsp", TEST),
        ("plugins/highest-disc-total/MANIFEST.toml", TEST),
        (".claude/settings.json", TEST),
        (".github/workflows/release-plugin.yml", TEST),
        ("renovate.json", TEST),
        ("README.md", TEST),
        ("CLAUDE.md", NONE),
        (".claude/skills/picard-script/SKILL.md", NONE),
        (".claude/agents/behaviour-docs-reviewer.md", NONE),
        ("LICENSE", NONE),
        (".git-blame-ignore-revs", NONE),
        # Unknown paths run everything.
        (".gitignore", BOTH),
        (".github/workflows/new.yml", BOTH),
        ("docs/guide.md", BOTH),
        ("readme.md", BOTH),
        ("new-folder/notes.txt", BOTH),
    ],
)
def test_outputs_for(path, outputs):
    assert ci_changes.outputs_for(path) == outputs


def test_needed_is_the_union():
    assert ci_changes.needed(["CLAUDE.md", "README.md"]) == TEST
    assert ci_changes.needed(["CLAUDE.md", "new-folder/x"]) == BOTH
    assert ci_changes.needed([]) == NONE


def test_a_change_to_the_filter_itself_runs_everything(monkeypatch):
    """The run uses the PR's own RULES, so even one that skips everything mustn't skip its tests."""
    monkeypatch.setattr(ci_changes, "RULES", (("*", ()),))
    assert ci_changes.needed(["x.py"]) == NONE
    assert ci_changes.needed(["tools/ci_changes.py"]) == BOTH
    assert ci_changes.needed([".github/workflows/checks.yml", "CLAUDE.md"]) == BOTH


def test_no_test_reads_a_path_that_skips_the_tests():
    """A test that starts reading CLAUDE.md, say, needs its rule changed to run the tests."""
    sources = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TRACKED
        if re.fullmatch(r"tests/.*\.py|tools/run_tests\.py", path)
        and path != "tests/test_ci_changes.py"
    }
    for path in TRACKED:
        if "test" not in ci_changes.outputs_for(path):
            for source, text in sources.items():
                for literal in (f'"{path}"', f"'{path}'"):
                    assert literal not in text, f"{source} names {path}"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo on main with tools/x.py and CLAUDE.md, and git isolated from the user's config."""
    gitconfig = tmp_path / "gitconfig"  # keep the user's signing and hooks out of it
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)  # set when run from a git hook
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    root = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(root))
    write(root, "tools/x.py", "x = 1\n")
    write(root, "CLAUDE.md", "notes\n")
    write(root, "y.py", "y = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def merge_commit(repo, change):
    """What a pull_request run checks out: change() committed on a branch, merged with --no-ff
    into a main that has moved on since (its own y.py edit must not count), cloned at depth 2."""
    git(repo, "switch", "-q", "-c", "feature")
    change(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature")
    git(repo, "switch", "-q", "main")
    write(repo, "y.py", "y = 2\n")
    git(repo, "commit", "-q", "-am", "main moves on")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature")
    clone = repo.parent / "checkout"
    git(repo.parent, "clone", "-q", "--depth", "2", repo.as_uri(), str(clone))
    return clone


def test_the_changes_step_is_the_one_these_tests_run():
    step = JOBS["changes"]["steps"][1]
    assert step["run"] == 'python3 tools/ci_changes.py >> "$GITHUB_OUTPUT"'


def run_step(cwd, event="pull_request"):
    """The changes job's step in cwd, with this Python for python3 and stdout for the file."""
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ci_changes.py")],
        cwd=cwd,
        env=dict(os.environ, GITHUB_EVENT_NAME=event),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def outputs(result):
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_a_docs_only_pull_request_runs_nothing(repo):
    clone = merge_commit(repo, lambda r: write(r, "CLAUDE.md", "more notes\n"))
    assert outputs(run_step(clone)) == "lint=false\ntest=false\n"


def test_a_readme_change_runs_only_the_tests(repo):
    clone = merge_commit(repo, lambda r: write(r, "README.md", "Picard 3.0.5\n"))
    assert outputs(run_step(clone)) == "lint=false\ntest=true\n"


def test_a_non_ascii_path_is_matched_unquoted(repo):
    clone = merge_commit(repo, lambda r: write(r, "tagger-scripts/café.ptsp", "x\n"))
    assert outputs(run_step(clone)) == "lint=false\ntest=true\n"


def test_moving_code_into_a_docs_path_still_runs_its_jobs(repo):
    def move(r):
        (r / ".claude" / "skills").mkdir(parents=True)
        git(r, "mv", "tools/x.py", ".claude/skills/x.md")

    assert outputs(run_step(merge_commit(repo, move))) == "lint=true\ntest=true\n"


def test_any_other_event_runs_everything_without_git(tmp_path):
    assert outputs(run_step(tmp_path, event="push")) == "lint=true\ntest=true\n"


def test_a_depth_1_checkout_fails_the_step(repo, tmp_path):
    """Without the merge commit's parents the job must fail, not print an empty list."""
    merge_commit(repo, lambda r: write(r, "tools/x.py", "x = 2\n"))  # leaves main on it
    clone = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", repo.as_uri(), str(clone))
    result = run_step(clone)
    assert result.returncode != 0
    assert "git rev-parse failed" in result.stderr
    assert result.stdout == ""


def test_a_head_that_is_not_a_merge_commit_fails_the_step(repo, tmp_path):
    """E.g. a checkout of the PR's head: HEAD^1 would be its last commit's parent, not the base."""
    write(repo, "CLAUDE.md", "more notes\n")
    git(repo, "commit", "-q", "-am", "docs")
    clone = tmp_path / "linear"
    git(tmp_path, "clone", "-q", "--depth", "2", repo.as_uri(), str(clone))
    result = run_step(clone)
    assert result.returncode != 0
    assert "git rev-parse failed" in result.stderr
    assert result.stdout == ""


def test_the_checkout_fetches_the_merge_commit_s_parents():
    checkout = JOBS["changes"]["steps"][0]
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 2}


def test_changes_outputs_every_output():
    assert JOBS["changes"]["outputs"] == {
        output: "${{ steps.jobs.outputs." + output + " }}"
        for output in ci_changes.OUTPUTS
    }


def test_each_job_runs_only_when_its_output_says_so():
    gated = {name: job for name, job in JOBS.items() if name not in ("changes", "gate")}
    assert {name: job["needs"] for name, job in gated.items()} == dict.fromkeys(
        gated, "changes"
    )
    assert {name: job["if"] for name, job in gated.items()} == {
        "lint": "needs.changes.outputs.lint == 'true'",
        "test": "needs.changes.outputs.test == 'true'",
        "test-windows": "needs.changes.outputs.test == 'true'",
    }


def test_the_gate_needs_every_job_and_always_runs():
    gate = JOBS["gate"]
    assert set(gate["needs"]) == set(JOBS) - {"gate"}
    assert gate["if"] == "always()"
    [step] = gate["steps"]
    assert step["if"] == (
        "contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')"
    )
    assert step["run"] == "exit 1"

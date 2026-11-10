"""The Claude Code hooks in .claude/hooks, fed hook JSON on stdin as Claude Code runs them."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
SETTINGS = ROOT / ".claude" / "settings.json"
SCRIPT_FILES = sorted((ROOT / "tagger-scripts").glob("*.ptsp"))
POST_EDIT_HOOKS = ["check_ptsp.py", "ruff_format.py"]


def run_hook(name, stdin, env=None):
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )


def run_after_edit(name, file_path, tool_name="Edit", env=None):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": str(file_path)},
    }
    return run_hook(name, json.dumps(payload), env)


def context(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.fixture
def in_repo():
    """A scratch folder under the git-ignored .picard/, so the hooks find this repo's root,
    its test venv and pyproject.toml."""
    (ROOT / ".picard").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".picard", prefix="hook-test-") as tmp:
        yield Path(tmp)


@pytest.fixture
def bare_repo(tmp_path):
    """A repo root with no .picard/ built."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "run_tests.py").write_text("")
    return tmp_path


def ptsp(folder, script, name="test.ptsp"):
    path = folder / name
    body = "".join(f"  {line}\n" for line in script.splitlines())
    path.write_text(
        f"title: Test\nscript_language_version: '1.1'\nscript: |\n{body}",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("path", SCRIPT_FILES, ids=lambda p: p.name)
def test_check_ptsp_passes_the_real_scripts(path):
    result = run_after_edit("check_ptsp.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("$set(a,b)\n$set(c,$if(1,d)", "Unexpected end of script"),
        ("$set(a,b)\n$nosuch(x)", "Unknown function '$nosuch'"),
        ("$set(a,b)\n$if(1)", "Wrong number of arguments for $if"),
    ],
)
def test_check_ptsp_reports_a_script_error_with_its_file_line(in_repo, script, message):
    result = run_after_edit("check_ptsp.py", ptsp(in_repo, script))
    assert result.returncode == 2
    assert message in result.stderr
    assert "script line 1 is file line 4" in result.stderr


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"title: Test\n", "Invalid script package"),
        (b"title: [unclosed\nscript: |\n  x\n", "line 2, column 7"),
        ("title: Tést\nscript: |\n  x\n".encode("cp1252"), "not UTF-8"),
    ],
)
def test_check_ptsp_reports_a_file_that_is_not_a_script_package(
    in_repo, content, message
):
    path = in_repo / "test.ptsp"
    path.write_bytes(content)
    result = run_after_edit("check_ptsp.py", path)
    assert result.returncode == 2
    assert message in result.stderr
    assert "script line 1 is" not in result.stderr


def test_check_ptsp_checks_an_upper_case_suffix_and_a_multiedit(in_repo):
    path = ptsp(in_repo, "$nosuch(x)", name="TEST.PTSP")
    result = run_after_edit("check_ptsp.py", path, tool_name="MultiEdit")
    assert result.returncode == 2
    assert "Unknown function" in result.stderr


def test_check_ptsp_says_how_to_enable_it_without_picard(bare_repo):
    result = run_after_edit("check_ptsp.py", ptsp(bare_repo, "$nosuch("))
    assert "python tools/run_tests.py" in context(result)


def test_check_ptsp_blames_a_broken_checker_not_the_script(bare_repo):
    """A venv whose Picard source is missing: the child fails to import, not to parse."""
    venv = bare_repo / ".picard" / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    shutil.copy(sys.executable, python)
    shutil.copy(Path(sys.prefix) / "pyvenv.cfg", venv / "pyvenv.cfg")
    (bare_repo / ".picard" / "src").mkdir()
    note = context(run_after_edit("check_ptsp.py", ptsp(bare_repo, "$set(a,b)")))
    assert "the checker failed" in note
    assert "picard" in note


@pytest.mark.parametrize("name", POST_EDIT_HOOKS)
def test_hooks_ignore_other_files(name, in_repo):
    other = in_repo / "notes.txt"
    other.write_text("$nosuch( x=1")
    result = run_after_edit(name, other)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert other.read_text() == "$nosuch( x=1"


@pytest.mark.parametrize("name", POST_EDIT_HOOKS)
@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "[]",
        "{}",
        '{"tool_input": null}',
        '{"tool_input": "x"}',
        '{"tool_input": {"file_path": null}}',
    ],
)
def test_hooks_ignore_bad_input(name, stdin):
    result = run_hook(name, stdin)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_ruff_format_formats_and_sorts_imports_but_keeps_unused_ones(in_repo):
    assert shutil.which("uv"), "uv must be on PATH for the ruff_format tests"
    path = in_repo / "example.py"
    path.write_text("import sys\nimport os\nx=[1,\n2]\n", encoding="utf-8")
    result = run_after_edit("ruff_format.py", path, tool_name="MultiEdit")
    assert (result.returncode, result.stdout) == (0, ""), result.stderr
    assert path.read_text(encoding="utf-8") == "import os\nimport sys\n\nx = [1, 2]\n"


def test_ruff_format_reports_a_syntax_error(in_repo):
    path = in_repo / "broken.pyi"
    path.write_text("def f(:\n", encoding="utf-8")
    result = run_after_edit("ruff_format.py", path)
    assert result.returncode == 2
    assert "broken.pyi" in result.stderr
    assert path.read_text(encoding="utf-8") == "def f(:\n"


def test_ruff_format_says_when_uv_is_missing(in_repo):
    path = in_repo / "example.py"
    path.write_text("x=1\n")
    env = dict(os.environ, PATH=str(in_repo))
    assert "uv is not on PATH" in context(
        run_after_edit("ruff_format.py", path, env=env)
    )
    assert path.read_text() == "x=1\n"


@pytest.mark.parametrize("folder", ["src", "venv"])
def test_ruff_format_leaves_picard_source_and_venv_alone(bare_repo, folder):
    path = bare_repo / ".picard" / folder / "picard" / "upstream.py"
    path.parent.mkdir(parents=True)
    path.write_text("x=1\n")
    result = run_after_edit("ruff_format.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert path.read_text() == "x=1\n"


def settings_hooks():
    """(event, matcher, hook) for every hook in .claude/settings.json."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [
        (event, group["matcher"], hook)
        for event, groups in settings["hooks"].items()
        for group in groups
        for hook in group["hooks"]
    ]


def run_settings_command(hook, project_dir, stdin, cwd=None):
    # The command as Claude Code runs it, through the shell, with `python` pinned to this one.
    command = hook["command"].replace("python ", f'"{sys.executable}" ', 1)
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir)),
        timeout=120,
        check=False,
    )


def test_settings_wire_up_every_hook_script():
    wired = {}
    for event, matcher, hook in settings_hooks():
        script = next(
            p.name for p in HOOKS.glob("*.py") if f"'{p.name}'" in hook["command"]
        )
        wired[script] = (event, set(matcher.split("|")))
        assert hook["timeout"] > 0
    assert set(wired) == {p.name for p in HOOKS.glob("*.py")}
    for name in POST_EDIT_HOOKS:
        assert wired[name] == ("PostToolUse", {"Edit", "Write", "MultiEdit"})
    shells_and_edits = {
        "Bash",
        "PowerShell",
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
    }
    assert wired["guards.py"] == ("PreToolUse", shells_and_edits)


def test_settings_command_runs_the_guard():
    stdin = json.dumps({"tool_input": {"command": "gh pr list"}})
    hook = next(h for _, _, h in settings_hooks() if "guards.py" in h["command"])
    result = run_settings_command(hook, ROOT, stdin)
    assert (
        json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )


def test_settings_commands_run_the_hooks(in_repo):
    path = ptsp(in_repo, "$nosuch(x)")
    stdin = json.dumps({"tool_input": {"file_path": str(path)}})
    hook = next(h for _, _, h in settings_hooks() if "check_ptsp.py" in h["command"])
    result = run_settings_command(hook, ROOT, stdin)
    assert result.returncode == 2
    assert "Unknown function" in result.stderr


def test_settings_commands_ignore_modules_in_the_working_directory(tmp_path):
    """Without -I, a json.py wherever Claude is working would replace the stdlib one."""
    for module in ("json", "runpy"):
        (tmp_path / f"{module}.py").write_text("raise SystemExit(2)\n")
    stdin = json.dumps({"tool_input": {"file_path": str(SCRIPT_FILES[0])}})
    for _, _, hook in settings_hooks():
        result = run_settings_command(hook, ROOT, stdin, cwd=tmp_path)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_settings_commands_do_nothing_when_the_hook_file_is_missing(tmp_path):
    """Exit 2 from a missing script would block every tool call on a PreToolUse hook."""
    stdin = json.dumps({"tool_input": {"file_path": str(tmp_path / "x.ptsp")}})
    for _, _, hook in settings_hooks():
        result = run_settings_command(hook, tmp_path, stdin)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def run_guard(tool_name, tool_input):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    result = run_hook("guards.py", json.dumps(payload))
    assert (result.returncode, result.stderr) == (0, "")
    return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout else None


REPO = "robrich82/picards-scripts-plugins"
GUARD_CASES = [
    # gh without a repo: deny
    ("gh pr list", "deny"),
    ("git fetch && gh pr create --draft --base main", "deny"),
    ("C:/tools/gh.exe issue view 3", "deny"),
    (r'& "C:\Program Files\GitHub CLI\gh.exe" pr list', "deny"),
    ("timeout 30 gh run watch", "deny"),
    ('gh pr create --title "fix -R handling" --base main', "deny"),
    ('gh pr create --body "uses GH_REPO now"', "deny"),
    ("echo GH_REPO; gh pr list", "deny"),
    ("gh api repos/{owner}/{repo}/pulls", "deny"),
    ("gh api 'repos/{owner}/{repo}/pulls'", "deny"),
    ("gh repo view", "deny"),
    ("cd .picard/src && gh repo edit --description x", "deny"),
    ("make install && gh pr merge 3 --squash", "deny"),  # deny beats an earlier ask
    # gh with a repo, or not repo-scoped: allow
    (f"gh pr list -R {REPO}", None),
    (f"gh pr list -R{REPO}", None),
    (f"gh pr view 3 --repo={REPO} | jq .", None),
    (f"$env:GH_REPO='{REPO}'; gh pr list", None),
    (f"GH_REPO={REPO} gh pr list", None),
    (f"gh pr list --json number -q '.[] | .number' -R {REPO}", None),
    (f'gh pr create --title "A & B; c" -R {REPO}', None),
    (
        f"gh pr create --body \"$(cat <<'EOF'\nSummary\ngh run list\nEOF\n)\" -R {REPO}",
        None,
    ),
    (f"gh pr create \\\n  -R {REPO} \\\n  --draft", None),
    (f"gh pr create `\n  --repo {REPO} `\n  --draft", None),
    (f"gh pr view https://github.com/{REPO}/pull/3", None),
    (f"gh repo view {REPO}", None),
    (f"gh api repos/{REPO}/pulls", None),
    ("gh auth status", None),
    ("gh pr list --help", None),
    ('git commit -m "Deny gh pr list without a repo flag"', None),
    ("git commit -F - <<'EOF'\nGuard: gh issue view now needs a repo\nEOF", None),
    # installs: ask
    ("make install", "ask"),
    ("make -C . install-plugin", "ask"),
    ("mise exec -- make install-scripts", "ask"),
    ("python tools/install.py scripts", "ask"),
    (r"cd x && .picard/venv/Scripts/python.exe -I tools\install.py all", "ask"),
    ("python3.12 -X utf8 tools/install.py all", "ask"),
    ("cd tools && python install.py all", "ask"),
    ("python -m tools.install all", "ask"),
    ("uv run tools/install.py", "ask"),
    ("uv run --with rich python -I tools/install.py", "ask"),
    # not installs: allow
    ("make test", None),
    ('make test PYTEST_ARGS="-k install"', None),
    ("python tools/run_tests.py tests/test_install.py", None),
    ("uv run ruff check tools/install.py", None),
    ("uv run ruff check tools/run_tests.py tools/install.py", None),
    ("git diff tools/run_tests.py tools/install.py", None),
    ("cat Makefile | grep install", None),
    ('git commit -m "make sure we install the hook"', None),
]


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize(("command", "expected"), GUARD_CASES)
def test_guards_commands(tool, command, expected):
    output = run_guard(tool, {"command": command})
    decision = output["permissionDecision"] if output else None
    assert decision == expected
    if output:
        assert output["hookEventName"] == "PreToolUse"
        assert output["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize("folder", ["src", "venv", "new"])
def test_guards_deny_edits_inside_picard(tool, folder):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    path = ROOT / ".picard" / folder / "x.py"
    output = run_guard(tool, {key: str(path)})
    assert output and output["permissionDecision"] == "deny"


def test_guards_deny_edits_inside_a_worktrees_picard(bare_repo):
    path = bare_repo / "tools" / ".." / ".picard" / "src" / "x.py"
    output = run_guard("Write", {"file_path": str(path), "content": ""})
    assert output and output["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "tools" / "install.py",
        ROOT / "tagger-scripts" / "01-remove-hyphens-in-title.ptsp",
        ROOT / ".picard.bak" / "x.py",
        Path(tempfile.gettempdir()) / ".picard" / "x.py",  # not this repo's .picard
    ],
)
def test_guards_allow_other_edits(path):
    assert run_guard("Edit", {"file_path": str(path)}) is None


@pytest.mark.parametrize(
    "stdin",
    ["not json", "[]", "{}", '{"tool_input": null}', '{"tool_input": "gh pr list"}'],
)
def test_guards_ignore_bad_input(stdin):
    result = run_hook("guards.py", stdin)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

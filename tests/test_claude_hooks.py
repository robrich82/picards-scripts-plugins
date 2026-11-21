"""The Claude Code hooks in .claude/hooks, fed hook JSON on stdin as Claude Code runs them."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
SETTINGS = ROOT / ".claude" / "settings.json"
SCRIPT_FILES = sorted((ROOT / "tagger-scripts").glob("*.ptsp"))
POST_EDIT_HOOKS = ["check_ptsp.py", "ruff_format.py", "check_min_python.py"]
REPO = "robrich82/picards-scripts-plugins"


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


def load_hook(name):
    spec = importlib.util.spec_from_file_location(Path(name).stem, HOOKS / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_after_edit(monkeypatch, capsys, name, file_path):
    """A post-edit hook's main() in-process, so a test can fake what it runs.
    Returns (exit code, the note to Claude)."""
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    code = load_hook(name).main()
    output = json.loads(capsys.readouterr().out)
    return code, output["hookSpecificOutput"]["additionalContext"]


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


def test_check_ptsp_gives_no_file_line_for_an_inline_script(in_repo):
    """A plain `script: ...` value has no `script: |` line to count from."""
    path = in_repo / "test.ptsp"
    path.write_text("title: Test\nscript: $nosuch(x)\n", encoding="utf-8")
    result = run_after_edit("check_ptsp.py", path)
    assert result.returncode == 2
    assert "Unknown function '$nosuch'" in result.stderr
    assert "script line 1 is" not in result.stderr


def test_check_ptsp_says_when_the_check_times_out(bare_repo, monkeypatch, capsys):
    venv = bare_repo / ".picard" / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("")
    (bare_repo / ".picard" / "src").mkdir()

    def too_slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", too_slow)
    path = ptsp(bare_repo, "$set(a,b)")
    code, note = call_after_edit(monkeypatch, capsys, "check_ptsp.py", path)
    assert code == 0
    assert note == "test.ptsp was not parse-checked: the check timed out."


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


@pytest.mark.parametrize("name", POST_EDIT_HOOKS)
def test_hooks_ignore_files_outside_a_repo(name, tmp_path):
    files = [ptsp(tmp_path, "$nosuch("), tmp_path / "example.py"]
    files[1].write_text("x=1\n")
    for path in files:
        result = run_after_edit(name, path)
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert files[1].read_text() == "x=1\n"


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


@pytest.fixture
def py_file_with_uv(bare_repo, monkeypatch):
    """A .py file in a repo, with uv on PATH as far as the hook can tell."""
    path = bare_repo / "example.py"
    path.write_text("x=1\n")
    monkeypatch.setattr(shutil, "which", lambda name: "uv")
    return path


@pytest.mark.parametrize(
    ("stderr", "stdout", "shown"),
    [
        (
            "warning: x\nerror: Failed to spawn: `ruff`\n",
            "Usage: uv run\n",
            "error: Failed to spawn: `ruff`",
        ),
        ("", "error: on stdout\n", "error: on stdout"),
        ("", "", "no output"),
    ],
)
def test_ruff_format_says_when_ruff_does_not_start(
    py_file_with_uv, monkeypatch, capsys, stderr, stdout, shown
):
    path, commands = py_file_with_uv, []

    def cannot_start(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 2, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", cannot_start)
    code, note = call_after_edit(monkeypatch, capsys, "ruff_format.py", path)
    assert code == 0
    assert note == f"example.py was not formatted: Ruff didn't start ({shown})."
    assert [c[-1] for c in commands] == ["--version"]  # the file is never formatted


def test_ruff_format_says_when_ruff_times_out(py_file_with_uv, monkeypatch, capsys):
    path, commands = py_file_with_uv, []

    def slow_format(args, **kwargs):
        commands.append(args)
        if "format" in args:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, "ruff 0.0.0\n", "")

    monkeypatch.setattr(subprocess, "run", slow_format)
    code, note = call_after_edit(monkeypatch, capsys, "ruff_format.py", path)
    assert code == 0
    assert note == "example.py was not formatted: Ruff timed out."
    assert len(commands) == 2  # --version, then format; the import sort never runs


NEW_API = 'import shutil\nshutil.rmtree("x", onexc=None)\n'  # 3.12


@pytest.fixture
def in_plugins():
    """A scratch folder under plugins/, which per-file-target-version holds to 3.10."""
    with tempfile.TemporaryDirectory(dir=ROOT / "plugins", prefix="hook-test-") as tmp:
        yield Path(tmp)


def test_check_min_python_passes_a_real_file():
    result = run_after_edit("check_min_python.py", ROOT / "tools" / "run_tests.py")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_min_python_reports_a_newer_api_with_its_line(in_plugins):
    path = in_plugins / "example.py"
    path.write_text(NEW_API)
    result = run_after_edit("check_min_python.py", path, tool_name="Write")
    assert result.returncode == 2
    assert result.stderr.startswith("example.py uses something newer")
    assert "L2: 'shutil.rmtree(onexc)' requires !2, 3.12" in result.stderr


def test_check_min_python_skips_files_the_table_does_not_name(in_repo):
    path = in_repo / "example.py"  # under .picard/, which no glob names
    path.write_text(NEW_API)
    result = run_after_edit("check_min_python.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_min_python_skips_a_repo_without_the_tool(bare_repo):
    path = bare_repo / "example.py"
    path.write_text(NEW_API)
    result = run_after_edit("check_min_python.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_min_python_says_when_uv_is_missing(in_plugins):
    path = in_plugins / "example.py"
    path.write_text(NEW_API)
    env = dict(os.environ, PATH=str(in_plugins))
    assert "uv is not on PATH" in context(
        run_after_edit("check_min_python.py", path, env=env)
    )


@pytest.fixture
def tool_run(in_repo, monkeypatch):
    """A .py file in this repo and a fake subprocess.run: set .result or .error, read .args."""
    path = in_repo / "example.py"
    path.write_text("x = 1\n")
    monkeypatch.setattr(shutil, "which", lambda name: "uv")
    run = SimpleNamespace(path=path, args=None, result=None, error=None)

    def fake_run(args, **kwargs):
        run.args = args
        if run.error:
            raise run.error
        return run.result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return run


def test_check_min_python_runs_the_tool_of_the_file_s_repo(
    tool_run, monkeypatch, capsys
):
    tool_run.result = subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(
                json.dumps({"tool_input": {"file_path": str(tool_run.path)}}).encode()
            )
        ),
    )
    assert load_hook("check_min_python.py").main() == 0
    assert tool_run.args[-3:] == [
        "python",
        str(ROOT / "tools" / "check_min_python.py"),
        str(tool_run.path),
    ]
    assert tool_run.args[tool_run.args.index("--project") + 1] == str(ROOT)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("code", "stdout", "stderr", "shown"),
    [
        (1, "", "Traceback\nKeyError: 'tool'\n", "KeyError: 'tool'"),
        (1, "  \n", "", "no output"),
        (
            2,
            "",
            "error: Failed to spawn: `python`\n",
            "error: Failed to spawn: `python`",
        ),
    ],
)
def test_check_min_python_says_when_the_check_fails(
    tool_run, monkeypatch, capsys, code, stdout, stderr, shown
):
    tool_run.result = subprocess.CompletedProcess([], code, stdout, stderr)
    code, note = call_after_edit(
        monkeypatch, capsys, "check_min_python.py", tool_run.path
    )
    assert code == 0
    assert note == (
        f"example.py was not checked for newer stdlib APIs: the check failed ({shown})."
    )


def test_check_min_python_says_when_the_check_times_out(tool_run, monkeypatch, capsys):
    tool_run.error = subprocess.TimeoutExpired([], 50)
    code, note = call_after_edit(
        monkeypatch, capsys, "check_min_python.py", tool_run.path
    )
    assert code == 0
    assert note == "example.py was not checked for newer stdlib APIs: timed out."


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
    stdin = json.dumps({"tool_input": {"command": "mise run install-all"}})
    hook = next(h for _, _, h in settings_hooks() if "guards.py" in h["command"])
    result = run_settings_command(hook, ROOT, stdin)
    assert (
        json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
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


def run_guard(tool_name, tool_input, cwd=ROOT):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    if cwd is not None:
        payload["cwd"] = str(cwd)
    result = run_hook("guards.py", json.dumps(payload))
    assert (result.returncode, result.stderr) == (0, "")
    return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout else None


def decision_for(command, cwd=ROOT):
    output = run_guard("Bash", {"command": command}, cwd)
    if output:
        assert output["hookEventName"] == "PreToolUse"
        assert output["permissionDecisionReason"]
    return output["permissionDecision"] if output else None


PICARD_SRC = ROOT / ".picard" / "src"
GH_CASES = [
    # gh with the shell in .picard/, or sent there, and no -R: ask
    ("gh pr view 10", PICARD_SRC, "ask"),
    ("gh repo view", PICARD_SRC, "ask"),
    ("gh api repos/{owner}/{repo}/pulls", PICARD_SRC, "ask"),
    ("GH pr list", PICARD_SRC, "ask"),
    ("C:/tools/gh.exe pr list", PICARD_SRC, "ask"),
    (r'& "C:\Program Files\GitHub CLI\gh.exe" pr list', PICARD_SRC, "ask"),
    ("git status && gh pr list", ROOT / ".picard", "ask"),
    (f"gh pr list && gh pr view 3 -R {REPO}", PICARD_SRC, "ask"),  # -R on one only
    ("cd .picard/src && gh pr view 10", ROOT, "ask"),
    (r"cd .picard\src; gh issue list", ROOT, "ask"),
    ("cd .PICARD/src && gh pr view 10", ROOT, "ask"),
    ('cd "$(git rev-parse --show-toplevel)/.picard/src" && gh pr list', ROOT, "ask"),
    ("gh pr list -Recurse", PICARD_SRC, "ask"),
    # an apostrophe that isn't a quote must not hide the gh call after it
    ("cat <<EOF\nit's done\nEOF\ngh pr view 10 --jq '.body'", PICARD_SRC, "ask"),
    ("git commit -m @'\nDon't x\n'@\ngh pr list --jq '.x'", PICARD_SRC, "ask"),
    # gh with -R, gh elsewhere, or no gh: allow
    (f"gh pr view 10 -R {REPO}", PICARD_SRC, None),
    (f"gh pr view 10 --repo={REPO}", PICARD_SRC, None),
    ("git log", PICARD_SRC, None),
    ('git commit -m "gh pr list"', PICARD_SRC, None),
    ("gh pr list", ROOT, None),
    ("gh repo view", ROOT / "tools", None),
    ("gh pr list", Path(tempfile.gettempdir()), None),
    ("ls .picard.bak && gh pr list", ROOT, None),
    ("gh pr list", None, None),  # no cwd in the hook input
    (f"gh pr view $(git branch --show-current) -R {REPO}", PICARD_SRC, None),
    (f"gh pr view 10 --repo {REPO}", PICARD_SRC, None),
    (f"gh api repos/{REPO}/pulls", PICARD_SRC, None),
    ("flatpak run org.musicbrainz.Picard; gh pr list", ROOT, None),
]


@pytest.mark.parametrize(("command", "cwd", "expected"), GH_CASES)
def test_guards_gh(command, cwd, expected):
    assert decision_for(command, cwd) == expected


@pytest.mark.skipif(os.name != "nt", reason="Git Bash drive paths are Windows-only")
def test_guards_gh_with_a_git_bash_cwd():
    drive, rest = str(PICARD_SRC).split(":", 1)
    cwd = f"/{drive.lower()}{rest.replace(os.sep, '/')}"
    assert decision_for("gh pr list", cwd) == "ask"


def test_guards_gh_in_a_worktrees_picard(bare_repo):
    assert decision_for("gh pr list", bare_repo / ".picard" / "src") == "ask"


INSTALL_CASES = [
    # installs: ask
    ("mise run install-all", "ask"),
    ("mise r install-plugin", "ask"),
    ("mise install-scripts", "ask"),  # mise runs a task named in place of a command
    ("mise tasks run install-plugin", "ask"),
    ("mise run test ::: install-scripts", "ask"),
    ("mise run --jobs 1 install-all", "ask"),
    ("mise -C $(git rev-parse --show-toplevel) run install-all", "ask"),
    ("MISE.EXE run install-all", "ask"),
    (r'& "C:\Users\me\.local\bin\mise.exe" run install-plugin', "ask"),
    ("picard-cli plugins --locale en install C:/x", "ask"),
    ("git status;mise run install-scripts", "ask"),
    ("a&&mise run install-all", "ask"),
    ("(mise run install-all)", "ask"),
    ("mise run install-all>install.log", "ask"),
    ("mise run install-plugin>NUL", "ask"),
    ("mise run \\\n  install-all", "ask"),
    ("mise run `\n  install-all", "ask"),
    ("mise\trun install-all", "ask"),
    ("mise run 'install-all'", "ask"),
    ("mise run '*'", "ask"),  # mise expands task patterns
    ("mise run install*", "ask"),
    ("mise run install-?ll", "ask"),
    ("mise run 'install-[a]ll'", "ask"),
    ('mise run "install-{all,x}"', "ask"),
    ("mise r --jobs 1 'i*'", "ask"),
    ("mise watch 'install-*'", "ask"),
    ("mise w '*'", "ask"),
    ("mise tasks run 'install*'", "ask"),
    ("mise 'i*'", "ask"),  # a pattern in place of the command runs matching tasks
    ("mise '*'", "ask"),
    ("mise -C . 'install*'", "ask"),
    ("mise --cd x -j 2 -q 'i*'", "ask"),
    ("$(which mise) 'i*'", "ask"),
    ("$(which mise) run install-all", "ask"),
    ("`which mise` run install-all", "ask"),
    ("& (Get-Command mise) run install-all", "ask"),
    ("picard-cli --yes plugins install C:/x/highest-disc-total", "ask"),
    ("python tools/install.py scripts", "ask"),
    ('python "tools/install.py" scripts', "ask"),
    (r"cd x && .picard/venv/Scripts/python.exe -I tools\install.py all", "ask"),
    (r'& ".picard\venv\Scripts\python.exe" tools\install.py all', "ask"),
    ("cd tools && python install.py all", "ask"),
    ("python -m tools.install all", "ask"),
    ("uv run -mtools.install", "ask"),
    ("uv run --with rich python -I tools/install.py", "ask"),
    ("uv run ruff check tools/install.py", "ask"),  # any unquoted mention asks
    # not installs: allow
    ("mise install", None),  # mise's own command: installs the tools in mise.toml
    ("mise install --locked uv", None),
    ("mise uninstall uv", None),
    ("mise install-into uv@0.13.8 /tmp/uv", None),
    ("mise run test", None),
    ("mise run test -k install", None),
    ("mise run test > install-all.log", None),
    ("mise tasks ls | grep install", None),
    ("mise run test && uv pip install ruff", None),
    ("cat mise.toml | grep install-all", None),
    ("cat mise.toml install-all", None),
    ("install-all mise run test", None),
    ("mise run pre-install-all", None),
    ("mise run foo.install-all", None),
    ("mise run x_install-all", None),
    ("mise run install-all-x", None),
    ("mise run install-plugins", None),
    ("mise run INSTALL-ALL", None),  # mise task names are case-sensitive: no such task
    ("does mise work on Windows?", None),  # glob characters count only after run/watch
    ("mise has uv {0.13.8}", None),
    ("mise ls 'u*'", None),
    ("mise -C 'a*' -E x ls", None),  # flag values aren't the command
    ("mise -v", None),
    ("mise run", None),
    ("echo install-plugin", None),
    ("make install", None),  # no Makefile any more
    ("cmake --install build", None),
    ("python tools/run_tests.py tests/test_install.py", None),
    ('git commit -m "mise run install-all via tools/install.py"', None),
    ("git commit -m 'mise run install-all and tools/install.py'", None),
]


@pytest.mark.parametrize(("command", "expected"), INSTALL_CASES)
def test_guards_installs(command, expected):
    # guards.py ignores the tool name; the settings matcher test covers Bash and PowerShell.
    assert decision_for(command) == expected


@pytest.mark.parametrize(
    "command",
    ["echo " + "mise x " * 20000, "@'\n" * 50000, "x '" * 50000, "$(" * 50000],
    ids=["mise", "herestring", "quote", "paren"],
)
def test_guards_stay_fast_on_a_long_command(command):
    """A regex that backtracks per opener would run past the hook's 10s timeout."""
    start = time.monotonic()
    run_guard("Bash", {"command": command})
    assert time.monotonic() - start < 5


def test_guards_give_every_reason_for_a_combined_command():
    reason = run_guard(
        "Bash", {"command": "cd .picard/src && gh pr list && mise run install-all"}
    )
    assert "upstream" in reason["permissionDecisionReason"]
    assert "Picard config" in reason["permissionDecisionReason"]


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


@pytest.mark.parametrize(("os_name", "inside"), [("nt", True), ("posix", False)])
def test_guards_read_git_bash_drive_paths_only_on_windows(
    tmp_path, monkeypatch, os_name, inside
):
    """Both branches on either OS. guards.py gets a stand-in os, since pathlib reads os.name."""
    if os.name == "nt":
        if not tmp_path.drive.endswith(":"):
            pytest.skip("Git Bash spells only drive-letter paths /c/...")
        repo = tmp_path
        drive, rest = repo.as_posix().split(":", 1)
        git_bash = f"/{drive.lower()}{rest}/.picard/src/x.py"
    else:  # c:/... is relative on POSIX, so the repo is a folder named c: in the cwd
        monkeypatch.chdir(tmp_path)
        repo = tmp_path / "c:"
        git_bash = "/c/.picard/src/x.py"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "run_tests.py").write_text("")
    guards = load_hook("guards.py")
    monkeypatch.setattr(guards, "os", SimpleNamespace(name=os_name))
    assert guards.inside_picard(git_bash) is inside


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
    [
        "not json",
        "[]",
        "{}",
        '{"tool_input": null}',
        '{"tool_input": "gh pr list"}',
        '{"tool_input": {}}',
        '{"tool_input": {"command": 5}}',
    ],
)
def test_guards_ignore_bad_input(stdin):
    result = run_hook("guards.py", stdin)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")

"""The Claude Code hooks in .claude/hooks, fed hook JSON on stdin as Claude Code runs them."""

import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
# The module the hooks import, so a test can fake what they run.
sys.path.append(str(HOOKS))
COMMON = importlib.import_module("_hook_common")
SETTINGS = ROOT / ".claude" / "settings.json"
SCRIPT_FILES = sorted((ROOT / "tagger-scripts").glob("*.ptsp"))
REPO = "robrich82/picards-scripts-plugins"
# Each post-edit hook, the file it acts on and the parts of its note for a root it won't run.
UNTRUSTED = {
    "check_ptsp.py": ("example.ptsp", "parse-checked", ".picard/venv was not run"),
    "ruff_format.py": ("example.py", "formatted", "uv project was not run"),
    "check_min_python.py": (
        "example.py",
        "checked for newer stdlib APIs",
        "tools/ were not run",
    ),
}
POST_EDIT_HOOKS = list(UNTRUSTED)


def untrusted_note(hook, root):
    name, done, what = UNTRUSTED[hook]
    return (
        f"{name} was not {done}: {root} is not CLAUDE_PROJECT_DIR or one of its git "
        f"worktrees, so its {what}."
    )


def run_hook(name, stdin, env=None, project=ROOT, cwd=None):
    # Pin CLAUDE_PROJECT_DIR: the post-edit hooks run code only from its checkouts.
    env = dict(os.environ if env is None else env, CLAUDE_PROJECT_DIR=str(project))
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        env=env,
        cwd=cwd,
        timeout=120,
        check=False,
    )


def run_after_edit(name, file_path, tool_name="Edit", **kwargs):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": str(file_path)},
    }
    return run_hook(name, json.dumps(payload), **kwargs)


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
    Returns (exit code, the note to Claude or None)."""
    payload = json.dumps({"tool_input": {"file_path": str(file_path)}}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
    code = load_hook(name).main()
    out = capsys.readouterr().out
    if not out:
        return code, None
    return code, json.loads(out)["hookSpecificOutput"]["additionalContext"]


@pytest.fixture
def in_repo():
    """A scratch folder under the git-ignored .picard/, so the hooks find this repo's root,
    its test venv and pyproject.toml."""
    (ROOT / ".picard").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".picard", prefix="hook-test-") as tmp:
        yield Path(tmp)


@pytest.fixture
def bare_repo(tmp_path):
    """A repo root with no .picard/ built. Pass it as project= to have the hooks trust it."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "run_tests.py").write_text("")
    return tmp_path


def venv_python(root):
    venv = root / ".picard" / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def fake_uv(folder):
    """An empty file that _hook_common.find_uv() takes for uv. Returns its path."""
    uv = folder / ("uv.exe" if os.name == "nt" else "uv")
    uv.write_text("")
    uv.chmod(0o755)
    return uv


@pytest.fixture
def uv_on_path(tmp_path_factory, monkeypatch):
    """A fake uv, alone on PATH, for tests that fake what the hooks run. Returns its path."""
    folder = tmp_path_factory.mktemp("bin")
    monkeypatch.setenv("PATH", str(folder))
    return fake_uv(folder)


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
    path = ptsp(bare_repo, "$nosuch(")
    result = run_after_edit("check_ptsp.py", path, project=bare_repo)
    assert "python tools/run_tests.py" in context(result)


def test_check_ptsp_blames_a_broken_checker_not_the_script(bare_repo):
    """A venv whose Picard source is missing: the child fails to import, not to parse."""
    python = venv_python(bare_repo)
    python.parent.mkdir(parents=True)
    shutil.copy(sys.executable, python)
    shutil.copy(Path(sys.prefix) / "pyvenv.cfg", python.parent.parent / "pyvenv.cfg")
    (bare_repo / ".picard" / "src").mkdir()
    path = ptsp(bare_repo, "$set(a,b)")
    note = context(run_after_edit("check_ptsp.py", path, project=bare_repo))
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


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (subprocess.TimeoutExpired([], 25), "the check timed out."),
        (
            OSError("bad"),
            ".picard/venv's python did not start (bad). Rebuild .picard/ with `python tools/run_tests.py`.",
        ),
    ],
    ids=["timeout", "python-does-not-start"],
)
def test_check_ptsp_says_when_the_check_does_not_finish(
    bare_repo, monkeypatch, capsys, error, shown
):
    python = venv_python(bare_repo)
    python.parent.mkdir(parents=True)
    python.write_text("")
    (bare_repo / ".picard" / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(bare_repo))

    def fails(args, **kwargs):
        raise error

    monkeypatch.setattr(COMMON, "run", fails)
    path = ptsp(bare_repo, "$set(a,b)")
    code, note = call_after_edit(monkeypatch, capsys, "check_ptsp.py", path)
    assert code == 0
    assert note == f"test.ptsp was not parse-checked: {shown}"


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


@pytest.fixture
def decoy_uv_in_cwd(tmp_path):
    """kwargs for run_after_edit: a cwd holding a uv that can't start, and an environment in
    which Windows' shutil.which looks there first."""
    fake_uv(tmp_path)
    # Claude Code sets it. os.environ's keys are upper case on Windows.
    off = "NODEFAULTCURRENTDIRECTORYINEXEPATH"
    env = {k: v for k, v in os.environ.items() if k.upper() != off}
    return {"cwd": tmp_path, "env": env}


def test_ruff_format_formats_and_sorts_imports_but_keeps_unused_ones(
    in_repo, decoy_uv_in_cwd
):
    assert shutil.which("uv"), "uv must be on PATH for the ruff_format tests"
    path = in_repo / "example.py"
    path.write_text("import sys\nimport os\nx=[1,\n2]\n", encoding="utf-8")
    result = run_after_edit(
        "ruff_format.py", path, tool_name="MultiEdit", **decoy_uv_in_cwd
    )
    assert (result.returncode, result.stdout) == (0, ""), result.stderr
    assert path.read_text(encoding="utf-8") == "import os\nimport sys\n\nx = [1, 2]\n"


def test_ruff_format_reports_a_syntax_error(in_repo):
    path = in_repo / "broken.pyi"
    path.write_text("def f(:\n", encoding="utf-8")
    result = run_after_edit("ruff_format.py", path)
    assert result.returncode == 2
    assert "broken.pyi" in result.stderr
    assert path.read_text(encoding="utf-8") == "def f(:\n"


@pytest.mark.parametrize("name", ["ruff_format.py", "check_min_python.py"])
def test_hooks_never_run_a_uv_in_the_working_directory(name, in_repo, decoy_uv_in_cwd):
    """shutil.which takes the cwd's uv on Windows, and a relative PATH folder's anywhere."""
    path = in_repo / "example.py"
    path.write_text("x=1\n")
    cwd, env = decoy_uv_in_cwd["cwd"], decoy_uv_in_cwd["env"]
    (cwd / "rel").mkdir()
    fake_uv(cwd / "rel")
    env["PATH"] = os.pathsep.join(["", ".", "rel", str(in_repo)])
    note = context(run_after_edit(name, path, **decoy_uv_in_cwd))
    assert "uv is not on PATH" in note
    assert path.read_text() == "x=1\n"


def test_find_uv_takes_the_first_absolute_path_folder_holding_it(tmp_path, monkeypatch):
    for folder in ("empty", "rooted", "folder", "first", "second"):
        (tmp_path / folder).mkdir()
    first, *_ = [fake_uv(tmp_path / f) for f in ("first", "second", "rooted")]
    (tmp_path / "folder" / first.name).mkdir()
    monkeypatch.chdir(tmp_path / "second")
    drive, rooted = os.path.splitdrive(tmp_path / "rooted")
    # On Windows, "\...\rooted" is on the cwd's drive; Python before 3.13 calls it absolute.
    # "C:." is the cwd on drive C: a drive, but not absolute.
    on_drive = [rooted, f"{drive}."] if os.name == "nt" else []
    folders = [
        str(tmp_path / f) for f in ("missing", "empty", "folder", "first", "second")
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", *on_drive, *folders]))
    if os.name == "nt":
        # Python 3.13 stopped calling "\folder" absolute; fake the older isabs the hook may run on.
        monkeypatch.setattr(
            os.path, "isabs", lambda p: p[:1] in "\\/" or p[1:3] in (":\\", ":/")
        )
    found = COMMON.find_uv()
    assert found and Path(found) == first


@pytest.mark.skipif(os.name != "nt", reason="the .cmd and .exe lookup is Windows-only")
def test_find_uv_takes_only_uv_exe_on_windows(tmp_path, monkeypatch):
    for folder in ("cmd", "exe"):
        (tmp_path / folder).mkdir()
    for name in ("uv.cmd", "uv.bat", "uv"):
        (tmp_path / "cmd" / name).write_text("")
    exe = fake_uv(tmp_path / "exe")
    monkeypatch.setenv(
        "PATH", os.pathsep.join(str(tmp_path / f) for f in ("cmd", "exe"))
    )
    assert COMMON.find_uv() == str(exe)


@pytest.mark.skipif(
    os.name == "nt", reason="every file passes os.access(X_OK) on Windows"
)
def test_find_uv_skips_a_uv_that_is_not_executable(tmp_path, monkeypatch):
    for folder in ("first", "second"):
        (tmp_path / folder).mkdir()
    fake_uv(tmp_path / "first").chmod(0o644)
    second = fake_uv(tmp_path / "second")
    monkeypatch.setenv(
        "PATH", os.pathsep.join(str(tmp_path / f) for f in ("first", "second"))
    )
    assert COMMON.find_uv() == str(second)


@pytest.mark.parametrize("folder", ["src", "venv"])
def test_ruff_format_leaves_picard_source_and_venv_alone(bare_repo, folder):
    path = bare_repo / ".picard" / folder / "picard" / "upstream.py"
    path.parent.mkdir(parents=True)
    path.write_text("x=1\n")
    result = run_after_edit("ruff_format.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert path.read_text() == "x=1\n"


@pytest.fixture
def py_file_with_uv(bare_repo, monkeypatch, uv_on_path):
    """A .py file in a trusted repo, with uv on PATH as far as the hook can tell."""
    path = bare_repo / "example.py"
    path.write_text("x=1\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(bare_repo))
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

    monkeypatch.setattr(COMMON, "run", cannot_start)
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

    monkeypatch.setattr(COMMON, "run", slow_format)
    code, note = call_after_edit(monkeypatch, capsys, "ruff_format.py", path)
    assert code == 0
    assert note == "example.py was not formatted: Ruff timed out."
    assert len(commands) == 2  # --version, then format; the import sort never runs


def test_ruff_format_says_when_uv_does_not_start(py_file_with_uv, monkeypatch, capsys):
    def cannot_start(args, **kwargs):
        raise OSError("[WinError 193] %1 is not a valid Win32 application")

    monkeypatch.setattr(COMMON, "run", cannot_start)
    code, note = call_after_edit(monkeypatch, capsys, "ruff_format.py", py_file_with_uv)
    assert code == 0
    assert note == (
        "example.py was not formatted: uv did not start "
        "([WinError 193] %1 is not a valid Win32 application)."
    )


NEW_API = 'import shutil\nshutil.rmtree("x", onexc=None)\n'  # 3.12


@pytest.fixture
def in_plugins():
    """A scratch folder under plugins/, which per-file-target-version holds to 3.10."""
    with tempfile.TemporaryDirectory(dir=ROOT / "plugins", prefix="hook-test-") as tmp:
        yield Path(tmp)


def needs_uv():
    assert shutil.which("uv"), "uv must be on PATH for the check_min_python tests"


def check_min_python_hook():
    return next(
        h for _, _, h in settings_hooks() if "check_min_python.py" in h["command"]
    )


def test_check_min_python_passes_a_py39_file(decoy_uv_in_cwd):
    """The tool's own shutil.which("uv") must not find the decoy either."""
    needs_uv()
    path = ROOT / "tools" / "run_tests.py"
    result = run_after_edit("check_min_python.py", path, **decoy_uv_in_cwd)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_min_python_reports_a_newer_api_with_its_line(in_plugins):
    needs_uv()
    path = in_plugins / "example.py"
    path.write_text(NEW_API)
    result = run_after_edit("check_min_python.py", path, tool_name="Write")
    assert result.returncode == 2
    assert result.stderr.startswith("example.py uses a stdlib API newer than")
    assert "L2: 'shutil.rmtree(onexc)' requires !2, 3.12" in result.stderr


def test_settings_command_runs_check_min_python(in_plugins):
    needs_uv()
    path = in_plugins / "example.py"
    path.write_text(NEW_API)
    stdin = json.dumps({"tool_input": {"file_path": str(path)}})
    result = run_settings_command(check_min_python_hook(), ROOT, stdin)
    assert result.returncode == 2
    assert "L2: 'shutil.rmtree(onexc)'" in result.stderr


def test_check_min_python_skips_files_the_table_does_not_name(in_repo):
    needs_uv()
    path = in_repo / "example.py"  # under .picard/, which no glob names
    path.write_text(NEW_API)
    result = run_after_edit("check_min_python.py", path)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.fixture
def tool_run(in_repo, monkeypatch, capsys, uv_on_path):
    """A .py file in this repo and a fake subprocess.run: set .result or .error, read .calls.
    .edit(path, hook) runs a post-edit hook, check_min_python.py unless named, on path and
    returns (exit code, the note to Claude or None)."""
    path = in_repo / "example.py"
    path.write_text("x = 1\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ROOT))
    passed = subprocess.CompletedProcess([], 0, "", "")
    run = SimpleNamespace(path=path, calls=[], result=passed, error=None)

    def fake_run(args, **kwargs):
        run.calls.append((args, kwargs))
        if run.error:
            raise run.error
        return run.result

    monkeypatch.setattr(COMMON, "run", fake_run)
    run.edit = lambda path, hook="check_min_python.py": call_after_edit(
        monkeypatch, capsys, hook, path
    )
    return run


def fake_repo(folder, git=None):
    """A folder holding what each post-edit hook runs (check_min_python's tool, a test venv)
    and an example.py and example.ptsp. Its .git is a folder, or a file of this text."""
    (folder / "tools").mkdir(parents=True)
    for name in ("run_tests.py", "check_min_python.py"):
        (folder / "tools" / name).write_text("")
    venv_python(folder).parent.mkdir(parents=True)
    venv_python(folder).write_text("")
    (folder / ".picard" / "src").mkdir()
    if git is None:
        (folder / ".git").mkdir()
    else:
        (folder / ".git").write_text(git)
    (folder / "example.py").write_text("x = 1\n")
    ptsp(folder, "$set(a,b)", name="example.ptsp")


@pytest.fixture
def project(tmp_path):
    """A main checkout with worktree wt (absolute links, as git writes by default) and wt2
    (relative links, as worktree.useRelativePaths writes). Two more entries must be skipped:
    one whose back-link is unreadable, and one whose folder was deleted without a prune."""
    tmp = tmp_path.resolve()
    fake_repo(tmp / "main")
    common = tmp / "main" / ".git"
    for name, dot_git, back_link in [
        ("wt", f"gitdir: {common / 'worktrees' / 'wt'}\n", f"{tmp / 'wt' / '.git'}\n"),
        ("wt2", "gitdir: ../main/.git/worktrees/wt2\n", "../../../../wt2/.git\n"),
        ("gone", None, f"{tmp / 'gone' / '.git'}\n"),
    ]:
        git_dir = common / "worktrees" / name
        git_dir.mkdir(parents=True)
        (git_dir / "commondir").write_text("../..\n")
        (git_dir / "gitdir").write_text(back_link)
        if dot_git:
            fake_repo(tmp / name, git=dot_git)
    (common / "worktrees" / "broken").mkdir()
    (common / "worktrees" / "broken" / "gitdir").write_bytes(b"\xff\xfe garbage")
    return tmp


def test_trusted_roots_are_the_main_checkout_and_its_worktrees(project):
    common = COMMON
    roots = {project / "main", project / "wt", project / "wt2"}
    for folder in roots:
        assert common.trusted_roots(folder) == roots


@pytest.mark.parametrize("relative", ["false", "true"], ids=["absolute", "relative"])
def test_trusted_roots_read_what_git_worktree_add_writes(
    tmp_path, isolated_git, relative
):
    tmp = tmp_path.resolve()
    git = ["git", "-c", f"worktree.useRelativePaths={relative}", "-C", str(tmp)]
    for args in (
        ["init", "-q", "main"],
        ["-C", "main", "worktree", "add", "-q", "--orphan", "-b", "wt", "../wt"],
    ):
        subprocess.run(git + args, check=True, capture_output=True)
    back_link = (tmp / "main" / ".git" / "worktrees" / "wt" / "gitdir").read_text()
    assert back_link.startswith("..") == (relative == "true")
    common = COMMON
    roots = {tmp / "main", tmp / "wt"}
    for folder in roots:
        assert common.trusted_roots(folder) == roots


@pytest.mark.parametrize(
    "git",
    [
        None,
        b"\xff\xfe garbage",
        b"not a link\n",
        b"gitdir:\n",
        b"gitdir: \x00\n",
        b"gitdir: .git\n",
    ],
    ids=["no-git", "garbage", "no-gitdir", "empty-gitdir", "nul", "gitdir-to-a-file"],
)
def test_trusted_roots_fall_back_to_the_project_alone(tmp_path, git):
    """Its .claude/hooks run anyway, so it is trusted even when its git links can't be read."""
    if git is not None:
        (tmp_path / ".git").write_bytes(git)
    assert COMMON.trusted_roots(tmp_path) == {tmp_path}


def test_trusted_roots_skip_the_folder_above_a_git_dir_not_named_dot_git(tmp_path):
    """--separate-git-dir or a bare repo: no checkout sits beside the git dir."""
    project = tmp_path.resolve() / "project"
    project.mkdir()
    (project / ".git").write_text("gitdir: ../store\n")
    (tmp_path / "store").mkdir()
    assert COMMON.trusted_roots(project) == {project}


@pytest.mark.parametrize("folder", ["main", "wt", "wt2"])
def test_hooks_run_a_worktree_s_own_code(tool_run, monkeypatch, project, folder):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project / "main"))
    root = project / folder
    assert tool_run.edit(root / "example.py") == (0, None)
    [(args, kwargs)] = tool_run.calls
    assert args[5:8] == [
        str(root),
        "python",
        str(root / "tools" / "check_min_python.py"),
    ]
    assert kwargs["cwd"] == str(root)  # the tool looks for uv in its cwd
    tool_run.calls.clear()
    assert tool_run.edit(root / "example.py", "ruff_format.py") == (0, None)
    assert {(tuple(args[4:6]), kw["cwd"]) for args, kw in tool_run.calls} == {
        (("--project", str(root)), str(root))
    }
    tool_run.calls.clear()
    assert tool_run.edit(root / "example.ptsp", "check_ptsp.py") == (0, None)
    [(args, _)] = tool_run.calls
    assert args[0] == str(venv_python(root))


def test_check_min_python_skips_a_worktree_without_the_tool(
    tool_run, monkeypatch, project
):
    """A worktree of a branch from before the tool existed."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project / "main"))
    (project / "wt" / "tools" / "check_min_python.py").unlink()
    assert tool_run.edit(project / "wt" / "example.py") == (0, None)
    assert tool_run.calls == []


@pytest.mark.parametrize(
    ("folder", "git"),
    [
        ("other", None),
        ("main/.picard/evil", None),
        ("gone", None),
        ("evil", "gitdir: {project}/main/.git\n"),
        ("evil", "gitdir: {project}/main/.git/worktrees/wt\n"),
        ("evil", "gitdir: ../main/.git\n"),
        ("evil", "gitdir: {project}/evil-git/worktrees/evil\n"),
    ],
    ids=[
        "another-repo",
        "a-folder-inside-the-repo",
        "a-clone-where-a-deleted-worktree-was",
        "gitdir-to-the-common-dir",
        "gitdir-to-a-worktree-s-git-dir",
        "relative-gitdir",
        "gitdir-to-its-own-git-dir-that-lists-it",
    ],
)
def test_hooks_never_run_another_folder_s_code(
    tool_run, monkeypatch, project, folder, git
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project / "main"))
    own_git_dir = project / "evil-git" / "worktrees" / "evil"
    own_git_dir.mkdir(parents=True)
    (own_git_dir / "gitdir").write_text(f"{project / 'evil' / '.git'}\n")
    (own_git_dir / "commondir").write_text("../..\n")
    root = project / folder
    fake_repo(root, git=git and git.format(project=project))
    for hook, (name, _, _) in UNTRUSTED.items():
        assert tool_run.edit(root / name, hook) == (0, untrusted_note(hook, root))
    assert tool_run.calls == []


def test_check_min_python_runs_the_repo_s_tool_through_uv(tool_run, uv_on_path):
    assert tool_run.edit(tool_run.path) == (0, None)
    [(args, kwargs)] = tool_run.calls
    assert Path(args[0]) == uv_on_path
    assert args[1:] == [
        "run",
        "--frozen",
        "--quiet",
        "--project",
        str(ROOT),
        "python",
        str(ROOT / "tools" / "check_min_python.py"),
        str(tool_run.path),
    ]
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["timeout"] < check_min_python_hook()["timeout"]


@pytest.mark.parametrize("project_dir", [None, ""], ids=["unset", "empty"])
def test_hooks_run_nothing_without_claude_project_dir(
    tool_run, monkeypatch, project_dir
):
    """settings.json passes an unset CLAUDE_PROJECT_DIR as "": it must not mean the cwd."""
    if project_dir is None:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    else:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", project_dir)
    monkeypatch.chdir(ROOT)
    ptsp(tool_run.path.parent, "$set(a,b)", name="example.ptsp")
    for hook, (name, _, _) in UNTRUSTED.items():
        path = tool_run.path.with_name(name)
        assert tool_run.edit(path, hook) == (0, untrusted_note(hook, ROOT))
    assert tool_run.calls == []


def test_check_min_python_runs_nothing_for_stubs_or_missing_files(tool_run):
    stub = tool_run.path.with_suffix(".pyi")
    stub.write_text("")
    for path in (stub, tool_run.path.with_name("missing.py")):
        assert tool_run.edit(path) == (0, None)
    assert tool_run.calls == []


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "shown"),
    [
        (1, "", "Traceback\nKeyError: 'tool'\n", "KeyError: 'tool'"),
        (1, "  \n", "", "no output"),
        (2, "", "error: Failed to spawn\n", "error: Failed to spawn"),
        (2, "not violations\n", "", "no output"),
    ],
    ids=["crash", "blank-stdout", "uv-failure", "other-exit-with-stdout"],
)
def test_check_min_python_says_when_the_check_fails(
    tool_run, returncode, stdout, stderr, shown
):
    tool_run.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
    assert tool_run.edit(tool_run.path) == (
        0,
        f"example.py was not checked for newer stdlib APIs: the check failed ({shown}).",
    )


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (subprocess.TimeoutExpired([], 50), "timed out"),
        (OSError("Access is denied"), "uv did not start (Access is denied)"),
    ],
    ids=["timeout", "uv-does-not-start"],
)
def test_check_min_python_says_when_uv_does_not_finish(tool_run, error, shown):
    tool_run.error = error
    assert tool_run.edit(tool_run.path) == (
        0,
        f"example.py was not checked for newer stdlib APIs: {shown}.",
    )


# A child that starts a grandchild, which inherits its output pipes, and both sleep.
TREE = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "time.sleep(60)\n"
)


def test_run_stops_what_the_child_started_on_a_timeout():
    """subprocess.run would kill the child, then wait 60s on Windows for the grandchild."""
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        # Long enough for the child to start the grandchild on a loaded machine.
        COMMON.run([sys.executable, "-c", TREE], timeout=5)
    assert time.monotonic() - start < 15


def test_run_does_not_wait_for_a_process_that_left_the_tree():
    """The grandchild holding the pipes is out of the tree kill's reach: on Windows its parent
    has exited, on POSIX it is in a session of its own."""
    orphan = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
        " start_new_session=True)\n"
    )
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        COMMON.run([sys.executable, "-c", orphan], timeout=3)
    assert time.monotonic() - start < 3 + COMMON.KILL_TIME + 2


def test_run_stops_the_tree_when_interrupted(monkeypatch):
    class Interrupted(subprocess.Popen):
        def communicate(self, *args, **kwargs):
            raise KeyboardInterrupt

    killed = []
    monkeypatch.setattr(subprocess, "Popen", Interrupted)
    monkeypatch.setattr(COMMON, "kill_tree", lambda p: killed.append(p) or p.kill())
    with pytest.raises(KeyboardInterrupt):
        COMMON.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=30)
    [process] = killed
    assert process.wait(timeout=10) is not None


@pytest.mark.parametrize(
    "hook, name",
    [
        ("check_min_python.py", "example.py"),
        ("ruff_format.py", "example.py"),
        ("check_ptsp.py", "example.ptsp"),
    ],
)
def test_hooks_note_a_timeout_before_settings_json_stops_them(tool_run, hook, name):
    """The hook's timeouts, then the time a tree kill takes, fit under its settings.json one."""
    path = tool_run.path.with_name(name)
    if name.endswith(".ptsp"):
        ptsp(path.parent, "$set(a,b)", name)
    tool_run.edit(path, hook)
    [limit] = [h["timeout"] for _, _, h in settings_hooks() if hook in h["command"]]
    timeouts = [kwargs["timeout"] for _, kwargs in tool_run.calls]
    assert timeouts
    assert sum(timeouts) + COMMON.KILL_TIME < limit


def test_run_returns_the_child_s_output_and_exit_code():
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = COMMON.run([sys.executable, "-c", code], timeout=30, encoding="utf-8")
    assert (result.returncode, result.stdout, result.stderr) == (3, "out\n", "err\n")


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("fails", [False, True], ids=["tree-killed", "tree-kill-fails"])
def test_kill_tree_stops_the_tree_then_the_child(monkeypatch, platform, fails):
    """Both platforms' branches on either OS, with taskkill and killpg faked."""
    calls = []

    def kill(*args, **kwargs):
        calls.append(args)
        assert kwargs.get("timeout", COMMON.TASKKILL_TIME) == COMMON.TASKKILL_TIME
        if fails:
            raise ProcessLookupError("no such process")

    monkeypatch.setenv("SystemRoot", "")  # else a relative path, looked up in the cwd
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(subprocess, "run", kill)
    monkeypatch.setattr(os, "killpg", kill, raising=False)  # POSIX only
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)  # POSIX only
    COMMON.kill_tree(SimpleNamespace(pid=1234, kill=lambda: calls.append("kill")))
    if platform == "win32":
        [command] = calls[0]
        assert command[0] == os.path.join(r"C:\Windows", "System32", "taskkill.exe")
        assert command[1:] == ["/F", "/T", "/PID", "1234"]
    else:
        assert calls[0] == (1234, 9)
    assert calls[1:] == ["kill"]


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
    # _hook_common.py is imported by the hooks, not run by settings.json.
    assert set(wired) == {p.name for p in HOOKS.glob("[!_]*.py")}
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

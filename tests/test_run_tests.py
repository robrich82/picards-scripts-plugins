"""The test runner's rebuild logic, with git and uv replaced by a recorder."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "run_tests", Path(__file__).resolve().parent.parent / "tools" / "run_tests.py"
)
assert _spec and _spec.loader
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)


def read_only_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.chmod(path, stat.S_IREAD)


@pytest.fixture
def commands(monkeypatch):
    """Record git/uv commands; `uv venv` creates the interpreter and `git clone` the checkout."""
    seen = []

    def fake_run(*cmd):
        seen.append([str(c) for c in cmd])
        if cmd[:2] == ("uv", "venv"):
            python = run_tests.venv_python(Path(cmd[-1]))
            python.parent.mkdir(parents=True)
            python.write_text("")
        if cmd[:2] == ("git", "clone"):
            Path(cmd[-1]).mkdir(parents=True)

    monkeypatch.setattr(run_tests, "run", fake_run)
    # A fake venv python counts as working while its stub file is empty.
    monkeypatch.setattr(
        run_tests,
        "interpreter_works",
        lambda python: python.is_file() and not python.read_text(),
    )
    return seen


@pytest.fixture
def picard_src(tmp_path):
    src = tmp_path / "picard-src"
    src.mkdir()
    (src / "requirements.txt").write_text("PyQt6==6.0\n")
    (src / "requirements-build.txt").write_text("pytest==9.1.1\n")
    return src


@pytest.mark.skipif(
    os.name != "nt", reason="a read-only file only blocks deletion on Windows"
)
def test_rmtree_removes_read_only_files(tmp_path):
    read_only_file(tmp_path / "repo" / ".git" / "objects" / "pack" / "pack-1.pack")
    with pytest.raises(PermissionError):
        shutil.rmtree(tmp_path / "repo")
    run_tests.rmtree(tmp_path / "repo")
    assert not (tmp_path / "repo").exists()


def test_rmtree_handler_clears_read_only_and_retries(tmp_path, monkeypatch):
    """The handler itself, so platforms where a read-only file doesn't block deletion cover it too."""
    path = tmp_path / "pack-1.pack"
    read_only_file(path)
    unlink, writable_on_retry = os.unlink, []

    def spy(target):
        writable_on_retry.append(bool(os.stat(target).st_mode & stat.S_IWRITE))
        unlink(target)

    # The handler only retries os.unlink/os.rmdir, so the spy has to be os.unlink.
    monkeypatch.setattr(os, "unlink", spy)
    run_tests._clear_readonly(spy, str(path), PermissionError("in use"))
    assert writable_on_retry == [True] and not path.exists()


def test_rmtree_handler_reraises_other_errors(tmp_path):
    error = OSError("Cannot call rmtree on a symbolic link")
    with pytest.raises(OSError, match="symbolic link"):
        run_tests._clear_readonly(os.path.islink, str(tmp_path), error)
    with pytest.raises(OSError, match="symbolic link"):
        run_tests._clear_readonly(os.path.islink, str(tmp_path), (OSError, error, None))


def test_install_uses_picard_pins_and_cooldown(tmp_path, picard_src, commands):
    venv = tmp_path / "venv"
    run_tests.ensure_venv(venv, picard_src)
    assert commands == [
        ["uv", "venv", "--quiet", "--python", run_tests.PYTHON, str(venv)],
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--exclude-newer",
            "24 hours",
            "--python",
            str(run_tests.venv_python(venv)),
            "-r",
            str(picard_src / "requirements.txt"),
            "-c",
            str(picard_src / "requirements-build.txt"),
            "-r",
            str(venv / "coverage-requirements.txt"),
            "pytest",
        ],
    ]
    assert (venv / "coverage-requirements.txt").read_text(
        encoding="utf-8"
    ) == run_tests.coverage_requirements()


def test_coverage_requirements_need_uv(monkeypatch):
    monkeypatch.setattr(run_tests.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="uv not found"):
        run_tests.coverage_requirements()


def test_coverage_requirements_are_hashed_pins_from_uv_lock():
    with open(run_tests.ROOT / "uv.lock", "rb") as f:
        locked = {p["name"]: p["version"] for p in tomllib.load(f)["package"]}
    exported = run_tests.coverage_requirements()
    assert exported.startswith(f"coverage=={locked['coverage']} \\")
    assert "--hash=sha256:" in exported


@pytest.mark.parametrize(
    "change",
    [
        "python",
        "picard ref",
        "coverage pin",
        "requirements",
        "constraints",
        "interpreter gone",
        "base interpreter gone",
        "stamp gone",
    ],
)
def test_venv_rebuilt_when_anything_changes(
    tmp_path, picard_src, commands, monkeypatch, change
):
    venv = tmp_path / "venv"
    run_tests.ensure_venv(venv, picard_src)
    read_only_file(venv / "Lib" / "old.pyc")
    commands.clear()
    if change == "python":
        monkeypatch.setattr(run_tests, "PYTHON", "3.13")
    elif change == "picard ref":
        monkeypatch.setattr(run_tests, "PICARD_REF", "release-9")
    elif change == "coverage pin":
        monkeypatch.setattr(run_tests, "coverage_requirements", lambda: "coverage==9\n")
    elif change == "interpreter gone":
        run_tests.venv_python(venv).unlink()
    elif change == "base interpreter gone":
        run_tests.venv_python(venv).write_text("No Python at ...")
    elif change == "stamp gone":
        (venv / ".picard-deps").unlink()
    else:
        (
            picard_src
            / f"requirements{'-build' if change == 'constraints' else ''}.txt"
        ).write_text("changed\n")

    run_tests.ensure_venv(venv, picard_src)

    assert not (venv / "Lib" / "old.pyc").exists()
    assert [c[:2] for c in commands] == [["uv", "venv"], ["uv", "pip"]]


def test_venv_reused_when_nothing_changed(tmp_path, picard_src, commands):
    run_tests.ensure_venv(tmp_path / "venv", picard_src)
    commands.clear()
    run_tests.ensure_venv(tmp_path / "venv", picard_src)
    assert commands == []


def test_failed_venv_removal_leaves_no_stamp(
    tmp_path, picard_src, commands, monkeypatch
):
    venv = tmp_path / "venv"
    run_tests.ensure_venv(venv, picard_src)
    monkeypatch.setattr(run_tests, "PYTHON", "3.13")

    def locked(path):
        raise PermissionError("python.exe is in use")

    monkeypatch.setattr(run_tests, "rmtree", locked)
    with pytest.raises(PermissionError):
        run_tests.ensure_venv(venv, picard_src)
    assert not (venv / ".picard-deps").exists()


def test_failed_install_leaves_no_stamp(tmp_path, picard_src, commands, monkeypatch):
    recorder = run_tests.run

    def failing_install(*cmd):
        recorder(*cmd)
        if cmd[:2] == ("uv", "pip"):
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(run_tests, "run", failing_install)
    with pytest.raises(subprocess.CalledProcessError):
        run_tests.ensure_venv(tmp_path / "venv", picard_src)
    assert not (tmp_path / "venv" / ".picard-deps").exists()


def test_interpreter_works(tmp_path):
    assert run_tests.interpreter_works(Path(sys.executable))
    assert not run_tests.interpreter_works(tmp_path / "missing.exe")
    (tmp_path / "broken.exe").write_text("")
    assert not run_tests.interpreter_works(tmp_path / "broken.exe")


def test_venv_without_pytest_is_rebuilt(tmp_path, capsys):
    """A uv sync aimed at the venv strips pytest but leaves the stamp and a working interpreter."""
    import venv

    venv.create(tmp_path / "stripped", with_pip=False)
    assert not run_tests.interpreter_works(run_tests.venv_python(tmp_path / "stripped"))
    assert "No module named 'pytest'" in capsys.readouterr().out


def test_interpreter_failing_to_start_reports_why(monkeypatch, capsys):
    """A venv launcher whose base interpreter is gone starts, then exits 103. It prints the path in the
    ANSI code page, which need not decode as UTF-8."""
    seen = []

    def no_base_python(cmd, capture_output, check):
        seen.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 103, b"", b"No Python at 'C:\\Users\\Ren\xe9\\python.exe'\r\n"
        )

    monkeypatch.setattr(run_tests.subprocess, "run", no_base_python)
    assert not run_tests.interpreter_works(Path("venv/python"))
    assert seen == [[str(Path("venv/python")), "-I", "-c", "import pytest"]]
    assert (
        "(exit 103: No Python at 'C:\\Users\\Ren\\xe9\\python.exe'); rebuilding"
        in capsys.readouterr().out
    )


def test_failed_command_raises():
    with pytest.raises(subprocess.CalledProcessError):
        run_tests.run(sys.executable, "-c", "raise SystemExit(3)")


def test_picard_cloned_then_reused(tmp_path, commands):
    picard_dir = tmp_path / ".picard"
    run_tests.ensure_picard_source(picard_dir)
    assert commands == [
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            run_tests.PICARD_REF,
            run_tests.PICARD_REPO,
            str(picard_dir / "src"),
        ]
    ]
    assert (picard_dir / "REF").read_text() == run_tests.PICARD_REF + "\n"
    commands.clear()
    run_tests.ensure_picard_source(picard_dir)
    assert commands == []


def test_failed_clone_leaves_no_stamp(tmp_path, monkeypatch):
    picard_dir = tmp_path / ".picard"
    read_only_file(picard_dir / "src" / ".git" / "pack.idx")
    (picard_dir / "REF").write_text("release-old\n")

    def failing_clone(*cmd):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(run_tests, "run", failing_clone)
    with pytest.raises(subprocess.CalledProcessError):
        run_tests.ensure_picard_source(picard_dir)
    assert not (picard_dir / "REF").exists()
    assert not (picard_dir / "src").exists()


def test_missing_tool_exits_with_its_name(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SystemExit, match="no-such-tool not found on PATH"):
        run_tests.run("no-such-tool", "--version")


def test_main_forwards_args_and_exit_code(monkeypatch):
    seen = {}

    def fake_subprocess_run(cmd, cwd, env, check):
        seen.update(cmd=cmd, cwd=cwd, picard_src=env["PICARD_SRC"], check=check)
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(run_tests, "ensure_picard_source", lambda: None)
    monkeypatch.setattr(run_tests, "ensure_venv", lambda: None)
    monkeypatch.setattr(run_tests.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(sys, "argv", ["run_tests.py", "-k", "CV"])
    assert run_tests.main() == 3
    assert seen == {
        "cmd": [str(run_tests.venv_python(run_tests.VENV)), "-m", "pytest", "-k", "CV"],
        "cwd": run_tests.ROOT,
        "picard_src": str(run_tests.PICARD_SRC),
        "check": False,
    }


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ({}, 0),
        ({"pytest": 1}, 1),  # failing tests still get a report
        ({"pytest": 1, "combine": 4}, 1),  # the test failure is the one to report
        ({"combine": 4}, 4),  # a failed report step fails the run
        ({"report": 2}, 2),  # coverage under fail_under
    ],
)
@pytest.mark.parametrize(
    "argv", [["--coverage", "-k", "CV"], ["-k", "CV", "--coverage"]]
)
def test_main_coverage_runs_pytest_under_coverage_then_reports(
    monkeypatch, codes, expected, argv
):
    seen = []

    def fake_subprocess_run(cmd, cwd, env, check):
        seen.append(cmd[2:])
        step = "pytest" if cmd[2:5] == ["coverage", "run", "-m"] else cmd[3]
        return subprocess.CompletedProcess(cmd, codes.get(step, 0))

    monkeypatch.setattr(run_tests, "ensure_picard_source", lambda: None)
    monkeypatch.setattr(run_tests, "ensure_venv", lambda: None)
    monkeypatch.setattr(run_tests.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(sys, "argv", ["run_tests.py", *argv])
    assert run_tests.main() == expected
    steps = [
        ["coverage", "erase"],
        ["coverage", "run", "-m", "pytest", "-k", "CV"],
        ["coverage", "combine", "--quiet"],
        ["coverage", "report"],
    ]
    assert seen == steps[: 3 if "combine" in codes else 4]


def test_coverage_measures_hook_subprocesses_by_repo_path(tmp_path):
    """The real [tool.coverage] config, run as --coverage runs it: a hook launched as a
    subprocess from another folder is measured, and never-run files are listed at 0%."""
    hook = run_tests.ROOT / ".claude" / "hooks" / "guards.py"
    launcher = tmp_path / "launch.py"
    launcher.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, {str(hook)!r}], input='{{}}', text=True,"
        f" cwd={str(tmp_path)!r}, check=False)\n"
    )
    # Not the COVERAGE_* settings of an outer --coverage run measuring this test.
    env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE_")}
    env["COVERAGE_FILE"] = str(tmp_path / ".coverage")

    def coverage(*args, check=True):
        return subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=run_tests.ROOT,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=check,
        )

    coverage("run", str(launcher))
    coverage("combine", "--quiet")
    json_path = tmp_path / "coverage.json"
    coverage("json", "--quiet", "--fail-under=0", "-o", str(json_path))
    files = json.loads(json_path.read_text(encoding="utf-8"))["files"]
    covered = {
        name.replace("\\", "/"): data["summary"]["covered_lines"]
        for name, data in files.items()
    }
    assert covered[".claude/hooks/guards.py"] > 0
    assert covered["tools/install.py"] == 0
    assert not [name for name in covered if name.startswith(("tests/", ".picard/"))]
    report = coverage("report", check=False)  # CI's gate: under fail_under fails
    assert report.returncode == 2
    assert "less than fail-under=100" in report.stdout + report.stderr


def test_python_is_requires_python():
    """The test venv and uv sync's dev tools both take their Python from requires-python."""
    with open(run_tests.ROOT / "pyproject.toml", "rb") as f:
        requires = tomllib.load(f)["project"]["requires-python"]
    major, minor = run_tests.PYTHON.split(".")
    assert requires == f">={major}.{minor},<{major}.{int(minor) + 1}"


@pytest.mark.parametrize(
    "line", ['requires-python = ">=3.14,<3.15"', "requires-python='>=3.14,<3.15'"]
)
def test_pinned_python(tmp_path, line):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f"[project]\n{line}\n")
    assert run_tests.pinned_python(pyproject) == "3.14"


@pytest.mark.parametrize(
    "line",
    [
        "",  # missing
        'requires-python = ">=3.12"',  # uv venv would take any newer Python found first
        'requires-python = ">=3.12,<3.14"',
        'requires-python = " >=3.12,<3.13"',
        'requires-python = "==3.12.*"',
    ],
)
def test_pinned_python_rejects_anything_but_one_minor(tmp_path, line):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f"[project]\n{line}\n")
    with pytest.raises(SystemExit, match="requires-python must be"):
        run_tests.pinned_python(pyproject)

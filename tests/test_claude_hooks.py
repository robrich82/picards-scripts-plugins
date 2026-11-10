"""The Claude Code hooks in .claude/hooks, fed hook JSON on stdin as Claude Code runs them."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
SCRIPT_FILES = sorted((ROOT / "tagger-scripts").glob("*.ptsp"))


def run_hook(name, file_path):
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit"}
    payload["tool_input"] = {"file_path": str(file_path)}
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def in_repo():
    """A scratch folder inside this repo, so the hooks find its root, .picard/ and pyproject.toml."""
    (ROOT / ".picard").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".picard", prefix="hook-test-") as tmp:
        yield Path(tmp)


@pytest.fixture
def bare_repo(tmp_path):
    """A repo root with no .picard/ built."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "run_tests.py").write_text("")
    return tmp_path


def ptsp(folder, script):
    path = folder / "test.ptsp"
    body = "".join(f"  {line}\n" for line in script.splitlines())
    path.write_text(
        f"title: Test\nscript_language_version: '1.1'\nscript: |\n{body}",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("path", SCRIPT_FILES, ids=lambda p: p.name)
def test_check_ptsp_passes_the_real_scripts(path):
    result = run_hook("check_ptsp.py", path)
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
    result = run_hook("check_ptsp.py", ptsp(in_repo, script))
    assert result.returncode == 2
    assert message in result.stderr
    assert "script line 1 is file line 4" in result.stderr


def test_check_ptsp_reports_a_file_that_is_not_a_script_package(in_repo):
    path = in_repo / "test.ptsp"
    path.write_text("title: Test\n", encoding="utf-8")
    result = run_hook("check_ptsp.py", path)
    assert result.returncode == 2
    assert "Invalid script package" in result.stderr


def test_check_ptsp_says_how_to_enable_it_without_picard(bare_repo):
    result = run_hook("check_ptsp.py", ptsp(bare_repo, "$nosuch("))
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "python tools/run_tests.py" in context


@pytest.mark.parametrize("name", ["check_ptsp.py", "ruff_format.py"])
def test_hooks_ignore_other_files_and_bad_input(name, in_repo):
    other = in_repo / "notes.txt"
    other.write_text("$nosuch( x=1")
    result = run_hook(name, other)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    bad = subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 0


def test_ruff_format_formats_and_sorts_imports_but_keeps_unused_ones(in_repo):
    path = in_repo / "example.py"
    path.write_text("import sys\nimport os\nx=[1,\n2]\n", encoding="utf-8")
    result = run_hook("ruff_format.py", path)
    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == "import os\nimport sys\n\nx = [1, 2]\n"


def test_ruff_format_reports_a_syntax_error(in_repo):
    path = in_repo / "broken.py"
    path.write_text("def f(:\n", encoding="utf-8")
    result = run_hook("ruff_format.py", path)
    assert result.returncode == 2
    assert result.stderr
    assert path.read_text(encoding="utf-8") == "def f(:\n"


def test_ruff_format_leaves_picard_source_alone(bare_repo):
    path = bare_repo / ".picard" / "src" / "picard" / "upstream.py"
    path.parent.mkdir(parents=True)
    path.write_text("x=1\n")
    result = run_hook("ruff_format.py", path)
    assert (result.returncode, result.stderr) == (0, "")
    assert path.read_text() == "x=1\n"

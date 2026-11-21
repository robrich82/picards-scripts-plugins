"""tools/check_min_python.py running the pinned vermin on throwaway repos, and on this one."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_min_python", ROOT / "tools" / "check_min_python.py"
)
assert _spec and _spec.loader
check_min_python = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_min_python)

PYPROJECT = """\
[tool.ruff]
per-file-target-version = { "old/*.py" = "py39", "plugin/**" = "py310", "solo.py" = "py39", "ancient.py" = "py38" }
"""
NEW_API = 'import shutil\nshutil.rmtree("x", onexc=None)\n'  # 3.12
GENERIC_ANNOTATION = "def f(a: list[int]): pass\n"  # 3.9
MATCH = "match 1:\n    case _:\n        pass\n"  # 3.10


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)  # set when run from a git hook
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "pyproject.toml").write_text(PYPROJECT)
    return root


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_this_repo_meets_its_target_versions():
    assert check_min_python.main([]) == 0


def test_targets_reads_versions():
    assert check_min_python.targets(ROOT / "pyproject.toml")["plugins/**"] == "3.10"


def test_targets_rejects_unknown_version(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.ruff]\nper-file-target-version = { "a.py" = "3.9" }\n')
    with pytest.raises(SystemExit, match="unrecognised target version '3.9'"):
        check_min_python.targets(pyproject)


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("old/a.py", "3.9"),
        ("old/deeper/a.py", None),  # * stays in one folder
        ("plugin/x/__init__.py", "3.10"),
        ("solo.py", "3.9"),
        ("sub/solo.py", "3.9"),  # a glob without a slash matches the name
        ("new.py", None),
    ],
)
def test_target_of(relative, expected):
    versions = {"old/*.py": "3.9", "plugin/**": "3.10", "solo.py": "3.9"}
    assert check_min_python.target_of(relative, versions) == expected


def test_target_of_takes_the_lowest_matching_version():
    versions = {"**/*.py": "3.10", "a/*.py": "3.9", "a/b.py": "3.11"}
    assert check_min_python.target_of("a/b.py", versions) == "3.9"


def test_old_file_using_newer_api_fails(repo, capfd):
    write(repo, "old/a.py", NEW_API)
    assert check_min_python.check(repo) == 1
    assert "'shutil.rmtree(onexc)' requires !2, 3.12" in capfd.readouterr().out


def test_annotations_are_checked(repo, capfd):
    write(repo, "solo.py", GENERIC_ANNOTATION)
    assert check_min_python.check(repo) == 0
    write(repo, "ancient.py", GENERIC_ANNOTATION)
    assert check_min_python.check(repo) == 1
    assert "list[..]" in capfd.readouterr().out


def test_each_file_is_held_to_its_own_version(repo, capfd):
    write(repo, "plugin/p.py", MATCH)  # 3.10 is fine for plugin/
    write(repo, "old/a.py", MATCH)
    assert check_min_python.check(repo) == 1
    out = capfd.readouterr().out
    assert "old" in out
    assert "plugin" not in out


def test_novermin_skips_a_guarded_line(repo):
    write(repo, "old/a.py", NEW_API.replace("None)", "None)  # novermin"))
    assert check_min_python.check(repo) == 0


def test_unlisted_and_ignored_files_are_skipped(repo):
    write(repo, "new.py", NEW_API)
    write(repo, ".gitignore", "old/ignored.py\n")
    write(repo, "old/ignored.py", NEW_API)
    assert check_min_python.check(repo) == 0


def test_untracked_files_are_checked(repo):
    write(repo, "old/a.py", NEW_API)
    assert check_min_python.repo_files(repo) == ["old/a.py"]


def test_given_paths_only(repo, capfd):
    bad = write(repo, "old/a.py", NEW_API)
    good = write(repo, "old/b.py", "x = 1\n")
    assert check_min_python.check(repo, [good]) == 0
    assert check_min_python.check(repo, [str(bad)]) == 1
    capfd.readouterr()


def test_given_paths_outside_the_repo_or_not_python_are_skipped(repo, tmp_path):
    outside = write(tmp_path, "old/a.py", NEW_API)
    notes = write(repo, "old/a.txt", NEW_API)
    assert check_min_python.relative_files(repo, [outside, notes]) == []
    assert check_min_python.check(repo, [outside, notes]) == 0


def test_missing_uv_exits(repo, monkeypatch):
    write(repo, "old/a.py", "x = 1\n")
    monkeypatch.setattr(check_min_python.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="uv not found"):
        check_min_python.check(repo)

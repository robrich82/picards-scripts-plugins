"""The test runner's rebuild logic, with git and uv replaced by a recorder."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess

import pytest

_spec = importlib.util.spec_from_file_location(
    'run_tests', Path(__file__).resolve().parent.parent / 'tools' / 'run_tests.py')
run_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_tests)


def read_only_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    os.chmod(path, stat.S_IREAD)


@pytest.fixture
def commands(monkeypatch):
    """Record git/uv commands; `uv venv` creates the interpreter so later steps find it."""
    seen = []

    def fake_run(*cmd):
        seen.append([str(c) for c in cmd])
        if cmd[:2] == ('uv', 'venv'):
            python = run_tests.venv_python(Path(cmd[-1]))
            python.parent.mkdir(parents=True)
            python.write_text('')

    monkeypatch.setattr(run_tests, 'run', fake_run)
    return seen


@pytest.fixture
def picard_src(tmp_path):
    src = tmp_path / 'picard-src'
    src.mkdir()
    (src / 'requirements.txt').write_text('PyQt6==6.0\n')
    return src


def test_rmtree_removes_read_only_files(tmp_path):
    read_only_file(tmp_path / 'repo' / '.git' / 'objects' / 'pack' / 'pack-1.pack')
    run_tests.rmtree(tmp_path / 'repo')
    assert not (tmp_path / 'repo').exists()


def test_venv_rebuilt_when_python_version_changes(tmp_path, picard_src, commands):
    venv = tmp_path / 'venv'
    run_tests.ensure_venv(venv, picard_src)
    stamp = venv / '.picard-deps'
    stamp.write_text(stamp.read_text().replace(run_tests.PYTHON, '3.15', 1))
    read_only_file(venv / 'Lib' / 'old.pyc')
    commands.clear()

    run_tests.ensure_venv(venv, picard_src)

    assert not (venv / 'Lib' / 'old.pyc').exists()
    assert commands[0][:5] == ['uv', 'venv', '--quiet', '--python', run_tests.PYTHON]
    assert run_tests.PYTEST in commands[1]
    assert stamp.read_text().startswith(run_tests.PYTHON + ' ')


def test_venv_reused_when_nothing_changed(tmp_path, picard_src, commands):
    run_tests.ensure_venv(tmp_path / 'venv', picard_src)
    commands.clear()
    run_tests.ensure_venv(tmp_path / 'venv', picard_src)
    assert commands == []


def test_failed_clone_leaves_no_stamp(tmp_path, monkeypatch):
    picard_dir = tmp_path / '.picard'
    read_only_file(picard_dir / 'src' / '.git' / 'pack.idx')
    (picard_dir / 'REF').write_text('release-old\n')

    def failing_clone(*cmd):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(run_tests, 'run', failing_clone)
    with pytest.raises(subprocess.CalledProcessError):
        run_tests.ensure_picard_source(picard_dir)
    assert not (picard_dir / 'REF').exists()
    assert not (picard_dir / 'src').exists()

"""The test runner's rebuild logic, with git and uv replaced by a recorder."""
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

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
    """Record git/uv commands; `uv venv` creates the interpreter and `git clone` the checkout."""
    seen = []

    def fake_run(*cmd):
        seen.append([str(c) for c in cmd])
        if cmd[:2] == ('uv', 'venv'):
            python = run_tests.venv_python(Path(cmd[-1]))
            python.parent.mkdir(parents=True)
            python.write_text('')
        if cmd[:2] == ('git', 'clone'):
            Path(cmd[-1]).mkdir(parents=True)

    monkeypatch.setattr(run_tests, 'run', fake_run)
    return seen


@pytest.fixture
def picard_src(tmp_path):
    src = tmp_path / 'picard-src'
    src.mkdir()
    (src / 'requirements.txt').write_text('PyQt6==6.0\n')
    (src / 'requirements-build.txt').write_text('pytest==9.1.1\n')
    return src


@pytest.mark.skipif(os.name != 'nt', reason='a read-only file only blocks deletion on Windows')
def test_rmtree_removes_read_only_files(tmp_path):
    read_only_file(tmp_path / 'repo' / '.git' / 'objects' / 'pack' / 'pack-1.pack')
    with pytest.raises(PermissionError):
        shutil.rmtree(tmp_path / 'repo')
    run_tests.rmtree(tmp_path / 'repo')
    assert not (tmp_path / 'repo').exists()


def test_rmtree_handler_reraises_other_errors(tmp_path):
    error = OSError('Cannot call rmtree on a symbolic link')
    with pytest.raises(OSError, match='symbolic link'):
        run_tests._clear_readonly(os.path.islink, str(tmp_path), error)
    with pytest.raises(OSError, match='symbolic link'):
        run_tests._clear_readonly(os.path.islink, str(tmp_path), (OSError, error, None))


def test_install_uses_picard_pins_and_cooldown(tmp_path, picard_src, commands):
    venv = tmp_path / 'venv'
    run_tests.ensure_venv(venv, picard_src)
    assert commands == [
        ['uv', 'venv', '--quiet', '--python', run_tests.PYTHON, str(venv)],
        ['uv', 'pip', 'install', '--quiet', '--exclude-newer', '24 hours', '--python',
         str(run_tests.venv_python(venv)), '-r', str(picard_src / 'requirements.txt'),
         '-c', str(picard_src / 'requirements-build.txt'), 'pytest'],
    ]


@pytest.mark.parametrize('change', ['python', 'picard ref', 'requirements', 'constraints', 'interpreter gone'])
def test_venv_rebuilt_when_anything_changes(tmp_path, picard_src, commands, monkeypatch, change):
    venv = tmp_path / 'venv'
    run_tests.ensure_venv(venv, picard_src)
    read_only_file(venv / 'Lib' / 'old.pyc')
    commands.clear()
    if change == 'python':
        monkeypatch.setattr(run_tests, 'PYTHON', '3.13')
    elif change == 'picard ref':
        monkeypatch.setattr(run_tests, 'PICARD_REF', 'release-9')
    elif change == 'interpreter gone':
        run_tests.venv_python(venv).unlink()
    else:
        (picard_src / f'requirements{"-build" if change == "constraints" else ""}.txt').write_text('changed\n')

    run_tests.ensure_venv(venv, picard_src)

    assert not (venv / 'Lib' / 'old.pyc').exists()
    assert [c[:2] for c in commands] == [['uv', 'venv'], ['uv', 'pip']]


def test_venv_reused_when_nothing_changed(tmp_path, picard_src, commands):
    run_tests.ensure_venv(tmp_path / 'venv', picard_src)
    commands.clear()
    run_tests.ensure_venv(tmp_path / 'venv', picard_src)
    assert commands == []


def test_failed_venv_removal_leaves_no_stamp(tmp_path, picard_src, commands, monkeypatch):
    venv = tmp_path / 'venv'
    run_tests.ensure_venv(venv, picard_src)
    monkeypatch.setattr(run_tests, 'PYTHON', '3.13')

    def locked(path):
        raise PermissionError('python.exe is in use')

    monkeypatch.setattr(run_tests, 'rmtree', locked)
    with pytest.raises(PermissionError):
        run_tests.ensure_venv(venv, picard_src)
    assert not (venv / '.picard-deps').exists()


def test_picard_cloned_then_reused(tmp_path, commands):
    picard_dir = tmp_path / '.picard'
    run_tests.ensure_picard_source(picard_dir)
    assert commands == [['git', 'clone', '--quiet', '--depth', '1', '--branch', run_tests.PICARD_REF,
                         run_tests.PICARD_REPO, str(picard_dir / 'src')]]
    assert (picard_dir / 'REF').read_text() == run_tests.PICARD_REF + '\n'
    commands.clear()
    run_tests.ensure_picard_source(picard_dir)
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


def test_missing_tool_exits_with_its_name(monkeypatch):
    monkeypatch.setenv('PATH', '')
    with pytest.raises(SystemExit, match='no-such-tool not found on PATH'):
        run_tests.run('no-such-tool', '--version')


def test_main_forwards_args_and_exit_code(monkeypatch):
    seen = {}

    def fake_subprocess_run(cmd, cwd, env):
        seen.update(cmd=cmd, cwd=cwd, picard_src=env['PICARD_SRC'])
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(run_tests, 'ensure_picard_source', lambda: None)
    monkeypatch.setattr(run_tests, 'ensure_venv', lambda: None)
    monkeypatch.setattr(run_tests.subprocess, 'run', fake_subprocess_run)
    monkeypatch.setattr(sys, 'argv', ['run_tests.py', '-k', 'voice'])
    assert run_tests.main() == 3
    assert seen == {'cmd': [str(run_tests.venv_python(run_tests.VENV)), '-m', 'pytest', '-k', 'voice'],
                    'cwd': run_tests.ROOT, 'picard_src': str(run_tests.PICARD_SRC)}

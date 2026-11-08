"""Fetch Picard's source, build a test venv with uv, and run pytest against it.

Usage: python tools/run_tests.py [pytest args...]
Needs git and uv on PATH. The Picard source and the venv it builds live in git-ignored .picard/ and .venv/.
"""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

PICARD_REF = 'release-3.0.4'
PICARD_REPO = 'https://github.com/metabrainz/picard.git'
PYTHON = '3.12'

ROOT = Path(__file__).resolve().parent.parent
PICARD_DIR = ROOT / '.picard'
PICARD_SRC = PICARD_DIR / 'src'
VENV = ROOT / '.venv'


def venv_python(venv):
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(*cmd):
    exe = shutil.which(cmd[0])  # honours PATHEXT, so .cmd/.bat shims work on Windows
    if exe is None:
        sys.exit(f'{cmd[0]} not found on PATH')
    print('+', *cmd, flush=True)
    subprocess.run([exe, *(str(c) for c in cmd[1:])], check=True)


def _clear_readonly(func, path, exc):
    exc = exc[1] if isinstance(exc, tuple) else exc  # onerror passes exc_info
    if func not in (os.unlink, os.rmdir) or not isinstance(exc, PermissionError):
        raise exc
    os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    func(path)


def rmtree(path):
    """shutil.rmtree that also removes read-only files, such as git pack files on Windows."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly)
    else:
        shutil.rmtree(path, onerror=_clear_readonly)


def ensure_picard_source(picard_dir=PICARD_DIR):
    src, stamp = picard_dir / 'src', picard_dir / 'REF'
    if src.is_dir() and stamp.is_file() and stamp.read_text().strip() == PICARD_REF:
        return
    stamp.unlink(missing_ok=True)
    if src.exists():
        rmtree(src)
    picard_dir.mkdir(exist_ok=True)
    run('git', 'clone', '--quiet', '--depth', '1', '--branch', PICARD_REF, PICARD_REPO, src)
    stamp.write_text(PICARD_REF + '\n')


def ensure_venv(venv=VENV, picard_src=PICARD_SRC):
    # Picard's own hashed pins: requirements.txt for Picard, requirements-build.txt constrains pytest and its deps.
    requirements, constraints = picard_src / 'requirements.txt', picard_src / 'requirements-build.txt'
    digests = ' '.join(hashlib.sha256(p.read_bytes()).hexdigest() for p in (requirements, constraints))
    wanted = f'{PYTHON} {PICARD_REF} {digests}'
    stamp = venv / '.picard-deps'
    python = venv_python(venv)
    if python.exists() and stamp.is_file() and stamp.read_text().strip() == wanted:
        return
    stamp.unlink(missing_ok=True)
    if venv.exists():
        rmtree(venv)
    run('uv', 'venv', '--quiet', '--python', PYTHON, venv)
    run('uv', 'pip', 'install', '--quiet', '--exclude-newer', '24 hours', '--python', python,
        '-r', requirements, '-c', constraints, 'pytest')
    stamp.write_text(wanted + '\n')


def main():
    ensure_picard_source()
    ensure_venv()
    env = dict(os.environ, PICARD_SRC=str(PICARD_SRC))
    return subprocess.run([str(venv_python(VENV)), '-m', 'pytest', *sys.argv[1:]], cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())

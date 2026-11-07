"""Fetch Picard's source, build a test venv with uv, and run pytest against it.

Usage: python tools/run_tests.py [pytest args...]
Needs git and uv on PATH. Everything it creates lives in git-ignored .picard/ and .venv/.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

PICARD_REF = 'release-3.0.4'
PICARD_REPO = 'https://github.com/metabrainz/picard.git'
PYTHON = '3.12'

ROOT = Path(__file__).resolve().parent.parent
PICARD_DIR = ROOT / '.picard'
PICARD_SRC = PICARD_DIR / 'src'
VENV = ROOT / '.venv'
VENV_PY = VENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def run(*cmd):
    print('+', ' '.join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def ensure_picard_source():
    stamp = PICARD_DIR / 'REF'
    if PICARD_SRC.is_dir() and stamp.is_file() and stamp.read_text().strip() == PICARD_REF:
        return
    if PICARD_SRC.exists():
        shutil.rmtree(PICARD_SRC)
    PICARD_DIR.mkdir(exist_ok=True)
    run('git', 'clone', '--quiet', '--depth', '1', '--branch', PICARD_REF, PICARD_REPO, PICARD_SRC)
    stamp.write_text(PICARD_REF + '\n')


def ensure_venv():
    requirements = PICARD_SRC / 'requirements.txt'
    wanted = f'{PICARD_REF} {hashlib.sha256(requirements.read_bytes()).hexdigest()}'
    stamp = VENV / '.picard-deps'
    if VENV_PY.exists() and stamp.is_file() and stamp.read_text().strip() == wanted:
        return
    if not VENV_PY.exists():
        run('uv', 'venv', '--quiet', '--python', PYTHON, VENV)
    run('uv', 'pip', 'install', '--quiet', '--python', VENV_PY, '-r', requirements, 'pytest')
    stamp.write_text(wanted + '\n')


def main():
    ensure_picard_source()
    ensure_venv()
    env = dict(os.environ, PICARD_SRC=str(PICARD_SRC))
    return subprocess.run([str(VENV_PY), '-m', 'pytest', *sys.argv[1:]], cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())

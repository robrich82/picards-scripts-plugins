"""tools/install.py: the Picard-running guard and the plugin install, with picard-cli replaced by a recorder."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('install', ROOT / 'tools' / 'install.py')
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)

FAKE_CLI = """import json, os, sys
with open(os.environ['FAKE_CLI_LOG'], 'a') as log:
    log.write(json.dumps({'argv': sys.argv[1:], 'encoding': os.environ.get('PYTHONIOENCODING')}) + '\\n')
sys.exit(int(os.environ.get('FAKE_CLI_EXIT', '0')))
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A local-plugins dir and a recording picard-cli; Picard not running."""
    plugins = tmp_path / 'local-plugins'
    fake = tmp_path / 'fake_cli.py'
    fake.write_text(FAKE_CLI)
    log = tmp_path / 'cli.log'
    monkeypatch.setenv('PICARD_LOCAL_PLUGINS', str(plugins))
    monkeypatch.setenv('FAKE_CLI_LOG', str(log))
    monkeypatch.setattr(install, 'picard_cli', lambda: [sys.executable, str(fake)])
    monkeypatch.setattr(install, 'picard_running', lambda: False)

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return plugins / install.PLUGIN.name, calls


def test_plugin_copied_and_registered(env):
    dest, calls = env
    install.main(['plugin'])
    assert (dest / '__init__.py').read_bytes() == (install.PLUGIN / '__init__.py').read_bytes()
    assert (dest / 'MANIFEST.toml').is_file()
    assert calls() == [{'argv': ['--yes', 'plugins', 'install', dest.as_posix()], 'encoding': 'utf-8'}]
    assert '/' in calls()[0]['argv'][-1]  # picard-cli treats a path with no / as a registry ID


def test_reinstall_refreshes_the_copy(env):
    dest, calls = env
    install.main(['plugin'])
    (dest / 'removed_since.py').write_text('')
    (dest / '__pycache__').mkdir()
    install.main(['plugin'])
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        p.name for p in install.PLUGIN.iterdir() if p.name != '__pycache__')
    assert len(calls()) == 2


def test_refuses_while_picard_running(env, monkeypatch):
    dest, calls = env
    monkeypatch.setattr(install, 'picard_running', lambda: True)
    with pytest.raises(SystemExit, match='Picard is running'):
        install.main(['plugin'])
    assert not dest.exists() and calls() == []


def test_refuses_when_running_state_unknown(env, monkeypatch):
    dest, calls = env

    def broken():
        raise FileNotFoundError('tasklist')

    monkeypatch.setattr(install, 'picard_running', broken)
    with pytest.raises(SystemExit, match="Can't tell whether Picard is running"):
        install.main(['plugin'])
    assert not dest.exists() and calls() == []


def test_picard_cli_failure_fails_the_install(env, monkeypatch):
    monkeypatch.setenv('FAKE_CLI_EXIT', '2')
    with pytest.raises(SystemExit, match='exit code 2'):
        install.main(['plugin'])


def test_picard_cli_override(monkeypatch, tmp_path):
    monkeypatch.setenv('PICARD_CLI', str(tmp_path / 'picard-cli'))
    assert install.picard_cli() == [str(tmp_path / 'picard-cli')]


def test_picard_running_check_runs_on_this_platform():
    # The real tasklist/pgrep call; nothing in the test run is Picard.
    assert install.picard_running() is False


def test_make_target_runs_the_installer(env):
    out = subprocess.run(['make', '-n', 'install'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.split() == ['python', 'tools/install.py', 'plugin']

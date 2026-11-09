"""Installing the tagger scripts into a real Picard.ini, written and read back with Picard's own config code."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config_scripts = _load('picard_config_scripts')
install = _load('install')

TITLES = ['Remove Hyphens in Title', 'Set Subtitle', 'Use Original Date', 'Various Artists', 'Voice Actor']


def write_config(path, scripts, version='3.0.4', profiles=None):
    import picard.options  # noqa: F401
    from picard.config import Config

    config = Config.from_file(None, str(path))
    config.application['version'] = version
    config.setting['list_of_scripts'] = scripts
    config.setting['enable_tagger_scripts'] = False
    if profiles:
        config.profiles['user_profiles'], config.profiles['user_profile_settings'] = profiles
    config.sync()


def read_config(path):
    from picard.config import Config

    return Config.from_file(None, str(path))


@pytest.fixture
def ini(tmp_path, monkeypatch):
    path = tmp_path / 'Picard.ini'
    monkeypatch.setenv('PICARD_CONFIG_FILE', str(path))
    return path


def names(config):
    return [(name, enabled) for _, name, enabled, _ in config.setting['list_of_scripts']]


def test_fresh_config_gets_every_script_enabled_in_file_order(ini):
    write_config(ini, [])
    config_scripts.main()
    config = read_config(ini)
    assert names(config) == [(t, True) for t in TITLES]
    assert config.setting['enable_tagger_scripts'] is True
    import yaml

    va = next(row for row in config.setting['list_of_scripts'] if row[1] == 'Various Artists')
    ptsp = yaml.safe_load((ROOT / 'tagger-scripts' / '04-various-artists.ptsp').read_text(encoding='utf-8'))
    assert va[3] == ptsp['script'].strip()  # Picard's Import strips it too (ScriptSerializer.update_from_dict)


def test_existing_scripts_replaced_in_place_and_others_kept(ini):
    write_config(ini, [
        (0, 'Mine', True, '$noop()'),
        (1, 'Various Artists', False, 'old'),       # disabled on purpose: stays disabled
        (2, 'Theirs', True, '$noop(x)'),
        (3, 'Remove Hyphens in Title (imported)', True, 'old2'),
        (4, 'Remove Hyphens in Title', True, 'old3'),  # a renamed copy too: one survives
    ])
    config_scripts.main()
    rows = read_config(ini).setting['list_of_scripts']
    # Ours fill the slots they held (1 and 3) plus appended ones, in file order; Mine and Theirs stay put.
    assert [(name, enabled) for _, name, enabled, _ in rows] == [
        ('Mine', True), ('Remove Hyphens in Title', True), ('Theirs', True), ('Set Subtitle', True),
        ('Use Original Date', True), ('Various Artists', False), ('Voice Actor', True)]
    assert [pos for pos, *_ in rows] == list(range(len(rows)))
    assert all(content not in ('old', 'old2', 'old3') for *_, content in rows)


def test_running_twice_changes_nothing_more(ini):
    write_config(ini, [(0, 'Mine', True, '$noop()')])
    config_scripts.main()
    first = read_config(ini).setting['list_of_scripts']
    config_scripts.main()
    assert read_config(ini).setting['list_of_scripts'] == first


def test_backup_is_the_original_file(ini):
    write_config(ini, [(0, 'Mine', True, '$noop()')])
    original = ini.read_bytes()
    config_scripts.main()
    backups = list(ini.parent.glob('Picard.ini.bak-*'))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_active_profile_override_gets_the_scripts(ini):
    profiles = ([{'id': 'p1', 'title': 'Mine', 'enabled': True}], {'p1': {'list_of_scripts': [(0, 'Mine', True, '')]}})
    write_config(ini, [(0, 'Base', True, '')], profiles=profiles)
    config_scripts.main()
    config = read_config(ini)
    assert [name for _, name, *_ in config.profiles['user_profile_settings']['p1']['list_of_scripts']] == ['Mine', *TITLES]
    config.setting.set_profiles_override([])  # the base list, with no profile applied
    assert names(config) == [('Base', True)]


def test_newer_config_refused_untouched(ini):
    write_config(ini, [], version='3.1.0')
    original = ini.read_bytes()
    with pytest.raises(SystemExit, match='newer'):
        config_scripts.main()
    assert ini.read_bytes() == original and not list(ini.parent.glob('*.bak-*'))


def test_missing_config_refused(ini):
    with pytest.raises(SystemExit, match='No Picard config'):
        config_scripts.main()


def test_install_runs_the_helper_in_the_venv(ini, monkeypatch):
    write_config(ini, [])
    monkeypatch.setattr(install, 'picard_running', lambda: False)
    monkeypatch.setattr(install, 'installed_picard_version', lambda: '3.0.4')
    install.main(['scripts'])
    assert [name for name, _ in names(read_config(ini))] == TITLES


def test_install_refuses_other_picard_version(ini, monkeypatch):
    write_config(ini, [])
    original = ini.read_bytes()
    monkeypatch.setattr(install, 'picard_running', lambda: False)
    monkeypatch.setattr(install, 'installed_picard_version', lambda: '3.1.0')
    with pytest.raises(SystemExit, match='Installed Picard is 3.1.0'):
        install.main(['scripts'])
    assert ini.read_bytes() == original


def test_installed_version_read_from_picard_cli(monkeypatch, tmp_path):
    fake = tmp_path / 'fake_cli.py'
    fake.write_text("print('MusicBrainz Picard 3.0.4')\n")
    monkeypatch.setattr(install, 'picard_cli', lambda: [sys.executable, str(fake)])
    assert install.installed_picard_version() == '3.0.4'

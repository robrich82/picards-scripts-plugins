"""Write tagger-scripts/*.ptsp into Picard's config, through Picard's own config code.

Run by tools/install.py inside the test venv, with Picard's source on sys.path and Picard closed.
PICARD_CONFIG_FILE overrides the config file.
"""
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'tagger-scripts'
IMPORTED = ' (imported)'  # what Picard's Import appends to a script's name, in English


def config_file():
    """The file Picard itself would open (Config.from_app): a versioned Picard-x.y.z.ini wins."""
    if os.environ.get('PICARD_CONFIG_FILE'):
        return Path(os.environ['PICARD_CONFIG_FILE'])
    from PyQt6 import QtCore

    from picard import PICARD_APP_NAME, PICARD_ORG_NAME, PICARD_VERSION

    path = Path(QtCore.QSettings(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope,
                                 PICARD_ORG_NAME, PICARD_APP_NAME).fileName())
    versioned = path.with_name(f'{PICARD_APP_NAME}-{PICARD_VERSION.short_str()}.ini')
    return versioned if versioned.is_file() else path


def read_scripts():
    """(title, script) for each .ptsp, in file order."""
    from picard.script.serializer import TaggingScriptInfo

    scripts = []
    for path in sorted(SCRIPTS.glob('*.ptsp')):
        info = TaggingScriptInfo.create_from_yaml(path.read_text(encoding='utf-8'))
        if any(title == info['title'] for title, _ in scripts):
            sys.exit(f"{path.name}: another script is already titled {info['title']!r}")
        if info['title'].endswith(IMPORTED):  # merge() reads that suffix as Picard's Import
            sys.exit(f'{path.name}: a title must not end in {IMPORTED.strip()!r}')
        scripts.append((info['title'], info['script']))
    return scripts


def merge(existing, scripts):
    """Picard's list_of_scripts rows with scripts installed.

    A script already there (by name, or name + " (imported)") is replaced in place, enabled if any copy
    of it was; a new one is appended, enabled. Duplicates of ours are dropped, and ours are then put in
    file order within the slots they hold, leaving everyone else's scripts where they were.
    """
    titles = {title for title, _ in scripts}
    enabled_by_title, rows = {}, []
    for _, name, enabled, content in existing:
        base = name.removesuffix(IMPORTED)
        if base not in titles:
            rows.append((name, enabled, content))
        elif base in enabled_by_title:  # a duplicate: dropped
            enabled_by_title[base] = enabled_by_title[base] or enabled
        else:
            enabled_by_title[base] = enabled
            rows.append(None)  # a slot for one of ours
    rows += [None] * (len(scripts) - len(enabled_by_title))
    ours = iter((title, enabled_by_title.get(title, True), content) for title, content in scripts)
    rows = [next(ours) if row is None else row for row in rows]
    return [(pos, *row) for pos, row in enumerate(rows)]


def reading_profile(config, name):
    """The profile Picard reads option `name` from: the first enabled one with a value. None for the base."""
    settings = config.profiles['user_profile_settings'] or {}
    for profile in config.profiles['user_profiles'] or []:
        if profile['enabled'] and (settings.get(profile['id']) or {}).get(name) is not None:
            return profile
    return None


def write(config, name, value):
    """Set option `name` where Picard reads it, and return that profile (None for the base setting).

    Not plain setting[name] = value: that also picks a profile that tracks the option with no value yet,
    which Picard skips when reading.
    """
    profile = reading_profile(config, name)
    if profile is None:
        with config.setting.no_profile():
            config.setting[name] = value
    else:
        settings = config.profiles['user_profile_settings']
        settings[profile['id']][name] = value
        config.profiles['user_profile_settings'] = settings
    return profile


def other_copies(config, scripts, written):
    """(profile title, our titles) for each other profile with its own list holding some of ours."""
    titles = {title for title, _ in scripts}
    settings = config.profiles['user_profile_settings'] or {}
    for profile in config.profiles['user_profiles'] or []:
        rows = (settings.get(profile['id']) or {}).get('list_of_scripts')
        if rows is None or (written and profile['id'] == written['id']):
            continue
        names = sorted({name.removesuffix(IMPORTED) for _, name, *_ in rows} & titles)
        if names:
            yield profile['title'], names


def main():
    import picard.options  # noqa: F401  registers every Option
    from picard import PICARD_VERSION
    from picard.config import Config
    from picard.version import Version

    path = config_file()
    if not path.is_file():
        sys.exit(f'No Picard config at {path}: start Picard once, close it, and retry')
    config = Config.from_file(None, str(path))
    if config.status() != config.Status.NoError:  # sync() would still write over it
        sys.exit(f"Can't read {path} ({config.status().name}); nothing was changed")
    version, wanted = Version.from_string(config.application['version']), PICARD_VERSION.short_str()
    if version > PICARD_VERSION:
        sys.exit(f'{path} is from Picard {version.short_str()}, newer than {wanted}')
    if version < PICARD_VERSION:  # Picard's upgrade steps would later run over the new scripts too
        sys.exit(f'{path} is from Picard {version.short_str()}: start Picard {wanted} once so it upgrades '
                 f'the config, close it, and retry')
    scripts = read_scripts()
    backup = path.with_name(f'{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}')
    try:
        shutil.copyfile(path, backup)
    except OSError as error:
        sys.exit(f"Can't back up {path} ({error}); nothing was changed")
    written = write(config, 'list_of_scripts', merge(config.setting['list_of_scripts'], scripts))
    write(config, 'enable_tagger_scripts', True)
    config.sync()
    if config.status() != config.Status.NoError:
        sys.exit(f'Writing {path} failed ({config.status().name}); it may be partly written: restore {backup}')
    where = f"profile {written['title']!r} in " if written else ''
    print(f'Installed {len(scripts)} tagger scripts into {where}{path} (backup: {backup.name})')
    for title, names in other_copies(config, scripts, written):
        print(f"Not updated: profile {title!r} has its own copy of {', '.join(names)}")


if __name__ == '__main__':
    main()

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
IMPORTED = ' (imported)'  # what Picard's Import appends to a script's name


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

    infos = [TaggingScriptInfo.create_from_yaml(p.read_text(encoding='utf-8')) for p in sorted(SCRIPTS.glob('*.ptsp'))]
    return [(info['title'], info['script']) for info in infos]


def merge(existing, scripts):
    """Picard's list_of_scripts rows with scripts installed.

    A script already there (by name, or name + " (imported)") is replaced in place and keeps its
    enabled state; a new one is appended, enabled. Duplicates of ours are dropped, and ours are then
    put in file order within the slots they hold, leaving everyone else's scripts where they were.
    """
    names = {title for title, _ in scripts}
    rows, seen = [], set()
    for _, name, enabled, content in existing:
        base = name.removesuffix(IMPORTED)
        if base in names:
            if base in seen:
                continue
            seen.add(base)
            name = base
        rows.append([name, enabled, content])
    slots = []
    for title, content in scripts:
        index = next((i for i, row in enumerate(rows) if row[0] == title), None)
        if index is None:
            rows.append([title, True, content])
            index = len(rows) - 1
        else:
            rows[index][2] = content
        slots.append(index)
    ours = [rows[i] for i in slots]
    for slot, row in zip(sorted(slots), ours):
        rows[slot] = row
    return [(pos, name, enabled, content) for pos, (name, enabled, content) in enumerate(rows)]


def main():
    import picard.options  # noqa: F401  registers every Option
    from picard import PICARD_VERSION
    from picard.config import Config
    from picard.version import Version

    path = config_file()
    if not path.is_file():
        sys.exit(f'No Picard config at {path}: start Picard once, close it, and retry')
    config = Config.from_file(None, str(path))
    if Version.from_string(config.application['version']) > PICARD_VERSION:
        sys.exit(f"{path} is from Picard {config.application['version']}, newer than {PICARD_VERSION.short_str()}")
    backup = path.with_name(f'{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}')
    shutil.copyfile(path, backup)
    scripts = read_scripts()
    # setting[...] writes to an active profile when the profile overrides the option, as Picard reads it.
    config.setting['list_of_scripts'] = merge(config.setting['list_of_scripts'], scripts)
    config.setting['enable_tagger_scripts'] = True
    config.sync()
    if config.status() != config.Status.NoError:
        sys.exit(f'Writing {path} failed ({config.status()}); the backup is {backup}')
    print(f'Installed {len(scripts)} tagger scripts into {path} (backup: {backup.name})')


if __name__ == '__main__':
    main()

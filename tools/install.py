"""Install the plugin into MusicBrainz Picard.

Usage: python tools/install.py plugin
Picard must be closed: it rewrites its config on exit, undoing the install.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / 'plugins' / 'highest-disc-total'


def picard_running():
    """Whether Picard is running. Raises OSError when that can't be told, rather than guessing no."""
    if os.name == 'nt':
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq picard.exe', '/NH', '/FO', 'CSV'],
                             capture_output=True, text=True, check=True).stdout
        return '"picard.exe"' in out.lower()
    # Linux: python3 /usr/bin/picard; macOS: .../MusicBrainz Picard.app/Contents/MacOS/MusicBrainz Picard
    result = subprocess.run(['pgrep', '-f', r'(^|/)picard( |$)|MusicBrainz Picard\.app'], capture_output=True)
    if result.returncode > 1:
        raise OSError(f'pgrep failed: {result.stderr.decode(errors="replace").strip()}')
    return result.returncode == 0


def local_plugins_dir():
    """Where the plugin copy lives; Picard runs a local plugin from the folder it was installed from."""
    if os.environ.get('PICARD_LOCAL_PLUGINS'):
        return Path(os.environ['PICARD_LOCAL_PLUGINS'])
    if os.name == 'nt':
        return Path(os.environ['APPDATA']) / 'MusicBrainz' / 'local-plugins'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'MusicBrainz' / 'local-plugins'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'MusicBrainz' / 'local-plugins'


def picard_cli():
    """The picard-cli command, as a list. PICARD_CLI overrides it."""
    if os.environ.get('PICARD_CLI'):
        return [os.environ['PICARD_CLI']]
    candidates = [shutil.which('picard-cli')]
    if os.name == 'nt':
        candidates.append(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'MusicBrainz Picard' / 'picard-cli.exe')
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return [str(candidate)]
    sys.exit('picard-cli not found: put it on PATH, or set PICARD_CLI to its path')


def install_plugin():
    dest = local_plugins_dir() / PLUGIN.name
    if dest.exists():
        shutil.rmtree(dest)  # drops files the new version no longer has
    shutil.copytree(PLUGIN, dest, ignore=shutil.ignore_patterns('__pycache__'))
    # Forward slashes: picard-cli looks a path with no / up as a registry ID. Installing again is a no-op.
    # PYTHONIOENCODING: on Windows its output otherwise ends in a UnicodeEncodeError traceback.
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run([*picard_cli(), '--yes', 'plugins', 'install', dest.as_posix()], env=env)
    if result.returncode:
        sys.exit(f'picard-cli failed with exit code {result.returncode}')
    print(f'Plugin installed from {dest}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('what', choices=['plugin'])
    args = parser.parse_args(argv)
    try:
        running = picard_running()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Can't tell whether Picard is running ({error}); close it and retry")
    if running:
        sys.exit('Picard is running: close it first, or it will undo the install when it exits')
    install_plugin()


if __name__ == '__main__':
    main()

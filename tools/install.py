"""Install the tagger scripts, the plugin, or both into MusicBrainz Picard.

Usage: python tools/install.py {scripts|plugin|all}
"""
import argparse
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / 'plugins' / 'highest-disc-total'
sys.path.insert(0, str(ROOT / 'tools'))
import run_tests  # noqa: E402  needs the sys.path insert above


def picard_running():
    """Whether Picard is running. Raises OSError when that can't be told, rather than guessing no."""
    if os.name == 'nt':
        # Bytes: tasklist writes in the OEM code page, and its localised "no tasks" text isn't valid UTF-8.
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq picard.exe', '/NH', '/FO', 'CSV'],
                             capture_output=True, check=True).stdout
        return b'"picard.exe"' in out.lower()
    # Linux: python3 /usr/bin/picard, python3 -m picard; macOS: .../MusicBrainz Picard.app/Contents/MacOS/picard-run
    result = subprocess.run(['pgrep', '-f', r'(^|[/ ])picard( |$)|MusicBrainz Picard\.app'], capture_output=True)
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
        found = shutil.which(os.environ['PICARD_CLI'])
        if not found:
            sys.exit(f"PICARD_CLI is {os.environ['PICARD_CLI']}, which isn't a program")
        return [found]
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
        run_tests.rmtree(dest)  # drops files the new version no longer has; handles read-only files
    shutil.copytree(PLUGIN, dest, ignore=shutil.ignore_patterns('__pycache__'))
    # Forward slashes: picard-cli looks a path with no / up as a registry ID. A repeat install of the same
    # path is a no-op to picard-cli; the copy above is what updates the plugin.
    # PYTHONIOENCODING: on Windows its output otherwise ends in a UnicodeEncodeError traceback.
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run([*picard_cli(), '--yes', 'plugins', 'install', dest.as_posix()], env=env)
    if result.returncode:
        sys.exit(f'picard-cli failed with exit code {result.returncode}')
    print(f'Plugin installed from {dest}')


def installed_picard_version():
    """'3.0.4' from 'MusicBrainz Picard 3.0.4'; a daily build appends ' (<build id>)'."""
    cmd = [*picard_cli(), '--version']
    result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace',
                            env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    if result.returncode:
        sys.exit(f'{cmd[0]} --version failed with exit code {result.returncode}: {result.stderr.strip()}')
    match = re.search(r'Picard (\S+)', result.stdout)
    return match[1] if match else ''


def install_scripts():
    # The scripts go in through Picard's own config code, which must match the installed Picard:
    # a different version could store the list differently or miss an upgrade step.
    wanted = run_tests.PICARD_REF.removeprefix('release-')
    installed = installed_picard_version()
    if installed != wanted:
        sys.exit(f'Installed Picard is {installed or "unknown"}, but the scripts are written with the config code '
                 f'of {wanted}: point PICARD_REF in tools/run_tests.py at its release, or import them by hand')
    run_tests.ensure_picard_source()
    run_tests.ensure_venv()
    env = dict(os.environ, PYTHONPATH=str(run_tests.PICARD_SRC))
    result = subprocess.run([str(run_tests.venv_python(run_tests.VENV)), str(ROOT / 'tools' / 'picard_config_scripts.py')], env=env)
    if result.returncode:
        sys.exit(result.returncode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('what', choices=['scripts', 'plugin', 'all'])
    args = parser.parse_args(argv)
    try:
        running = picard_running()
    except (OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Can't tell whether Picard is running ({error}); close it and retry")
    if running:
        sys.exit('Picard is running: close it first, or it will undo the install when it exits')
    if args.what in ('scripts', 'all'):
        install_scripts()
    if args.what in ('plugin', 'all'):
        install_plugin()


if __name__ == '__main__':
    main()

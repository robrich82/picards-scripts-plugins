"""tools/install.py: the Picard-running guard and the plugin install, with picard-cli replaced by a recorder."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("install", ROOT / "tools" / "install.py")
assert _spec and _spec.loader
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
    plugins = tmp_path / "local-plugins"
    fake = tmp_path / "fake_cli.py"
    fake.write_text(FAKE_CLI)
    log = tmp_path / "cli.log"
    monkeypatch.setenv("PICARD_LOCAL_PLUGINS", str(plugins))
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setattr(install, "picard_cli", lambda: [sys.executable, str(fake)])
    monkeypatch.setattr(install, "picard_running", lambda: False)

    def calls():
        return (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )

    return plugins / install.PLUGIN.name, calls


def test_plugin_copied_and_registered(env):
    dest, calls = env
    install.main(["plugin"])
    assert (dest / "__init__.py").read_bytes() == (
        install.PLUGIN / "__init__.py"
    ).read_bytes()
    assert (dest / "MANIFEST.toml").is_file()
    assert calls() == [
        {"argv": ["--yes", "plugins", "install", dest.as_posix()], "encoding": "utf-8"}
    ]
    assert (
        "/" in calls()[0]["argv"][-1]
    )  # picard-cli treats a path with no / as a registry ID


def test_reinstall_refreshes_the_copy(env):
    dest, calls = env
    install.main(["plugin"])
    (dest / "removed_since.py").write_text("")
    (dest / "__pycache__").mkdir()
    install.main(["plugin"])
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        p.name for p in install.PLUGIN.iterdir() if p.name != "__pycache__"
    )
    assert [call["argv"] for call in calls()] == [
        ["--yes", "plugins", "install", dest.as_posix()],
        ["--yes", "plugins", "install", dest.as_posix(), "--reinstall"],
    ]


def test_picard_cli_parses_both_installs(env, monkeypatch):
    """The recorded argv against picard-cli's real parser, so a renamed flag fails when PICARD_REF moves."""
    from picard.cli import build_root_parser
    from picard.util import versions

    # --long-version's text probes Qt's SSL DLL, which crashes (caught) on Windows outside the app.
    monkeypatch.setattr(versions, "as_string", lambda: "")
    dest, calls = env
    install.main(["plugin"])
    install.main(["plugin"])
    first, repeat = (build_root_parser().parse_args(c["argv"]) for c in calls())
    assert (first.yes, first.reinstall, first.source) == (
        True,
        False,
        [dest.as_posix()],
    )
    assert (repeat.yes, repeat.reinstall, repeat.source) == (
        True,
        True,
        [dest.as_posix()],
    )


def test_reinstall_replaces_read_only_files(env):
    dest, _ = env
    install.main(["plugin"])
    os.chmod(
        dest / "MANIFEST.toml", stat.S_IREAD
    )  # git checkouts on Windows can leave files read-only
    install.main(["plugin"])
    assert (dest / "MANIFEST.toml").read_bytes() == (
        install.PLUGIN / "MANIFEST.toml"
    ).read_bytes()


def test_refuses_while_picard_running(env, monkeypatch):
    dest, calls = env
    monkeypatch.setattr(install, "picard_running", lambda: True)
    with pytest.raises(SystemExit, match="Picard is running"):
        install.main(["plugin"])
    assert not dest.exists() and calls() == []


def test_refuses_when_running_state_unknown(env, monkeypatch):
    dest, calls = env

    def broken():
        raise FileNotFoundError("tasklist")

    monkeypatch.setattr(install, "picard_running", broken)
    with pytest.raises(SystemExit, match="Can't tell whether Picard is running"):
        install.main(["plugin"])
    assert not dest.exists() and calls() == []


def test_picard_cli_failure_fails_the_install(env, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_EXIT", "2")
    with pytest.raises(SystemExit, match="exit code 2"):
        install.main(["plugin"])


def test_picard_cli_override(monkeypatch, tmp_path):
    cli = tmp_path / ("picard-cli.exe" if os.name == "nt" else "picard-cli")
    cli.write_text("")
    cli.chmod(0o755)
    monkeypatch.setenv("PICARD_CLI", str(cli))
    assert Path(install.picard_cli()[0]) == cli


def test_picard_cli_override_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("PICARD_CLI", str(tmp_path / "nope.exe"))
    with pytest.raises(SystemExit, match="PICARD_CLI"):
        install.picard_cli()


def test_picard_running_check_runs_on_this_platform():
    # The real tasklist/pgrep call: it must run and parse here, whether or not Picard is open.
    assert isinstance(install.picard_running(), bool)


def _fake_run(monkeypatch, stdout=b"", returncode=0):
    def run(cmd, **kwargs):
        if kwargs.get("check") and returncode:
            raise subprocess.CalledProcessError(returncode, cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, b"pgrep: boom")

    monkeypatch.setattr(install.subprocess, "run", run)


@pytest.mark.parametrize(
    ("stdout", "running"),
    [
        (b'"picard.exe","4242","Console","1","180,000 K"\r\n', True),
        (b"INFO: No tasks are running which match the specified criteria.\r\n", False),
        (
            "INFORMATION: Es werden keine Tasks mit den angegebenen Kriterien ausgef\u00fchrt.\r\n".encode(
                "cp850"
            ),
            False,
        ),
    ],
)
def test_tasklist_output_parsed(monkeypatch, stdout, running):
    monkeypatch.setattr(install.os, "name", "nt")
    _fake_run(monkeypatch, stdout)
    assert install.picard_running() is running


@pytest.mark.parametrize(("returncode", "running"), [(0, True), (1, False)])
def test_pgrep_result_parsed(monkeypatch, returncode, running):
    monkeypatch.setattr(install.os, "name", "posix")
    _fake_run(monkeypatch, returncode=returncode)
    assert install.picard_running() is running


def test_pgrep_failure_raises(monkeypatch):
    monkeypatch.setattr(install.os, "name", "posix")
    _fake_run(monkeypatch, returncode=2)
    with pytest.raises(OSError, match="boom"):
        install.picard_running()


@pytest.mark.parametrize(
    ("args", "command"),
    [
        (["install-all"], "python tools/install.py all"),
        (["install-plugin"], "python tools/install.py plugin"),
        (["install-scripts"], "python tools/install.py scripts"),
        (["test", "-k", "CV"], "python tools/run_tests.py -k CV"),
    ],
)
def test_mise_tasks_run_the_tools(args, command):
    mise = shutil.which("mise")
    assert mise, "mise must be on PATH for the mise task tests"
    # --dry-run prints each task's command to stderr. Pinned so the user's mise config can't
    # hide or colour that line, or install their global tools.
    env = {
        **os.environ,
        "MISE_TRUSTED_CONFIG_PATHS": str(ROOT),
        "MISE_QUIET": "0",
        "MISE_TASK_OUTPUT": "prefix",
        "MISE_COLOR": "0",
        "NO_COLOR": "1",
        "MISE_TASK_RUN_AUTO_INSTALL": "0",
    }
    err = subprocess.run(
        [mise, "run", "--dry-run", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    assert f"[{args[0]}] $ {command}" in err.splitlines()


def as_if_on(monkeypatch, os_name, platform="linux"):
    """install.py sees another OS; os.name itself stays put, since pathlib picks its Path class from it."""
    monkeypatch.setattr(
        install, "os", SimpleNamespace(name=os_name, environ=os.environ)
    )
    monkeypatch.setattr(
        install, "sys", SimpleNamespace(platform=platform, exit=sys.exit)
    )


@pytest.mark.parametrize(
    ("os_name", "platform", "env", "expected"),
    [
        ("nt", "win32", {"APPDATA": "{tmp}/AppData"}, "{tmp}/AppData/MusicBrainz"),
        ("posix", "darwin", {}, "{home}/Library/Application Support/MusicBrainz"),
        ("posix", "linux", {"XDG_DATA_HOME": "{tmp}/data"}, "{tmp}/data/MusicBrainz"),
        ("posix", "linux", {}, "{home}/.local/share/MusicBrainz"),
    ],
)
def test_local_plugins_dir_per_platform(
    monkeypatch, tmp_path, os_name, platform, env, expected
):
    home = tmp_path / "home"
    monkeypatch.delenv("PICARD_LOCAL_PLUGINS", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value.format(tmp=tmp_path))
    as_if_on(monkeypatch, os_name, platform)
    monkeypatch.setattr(install.Path, "home", lambda: home)
    assert install.local_plugins_dir() == Path(
        expected.format(tmp=tmp_path, home=home), "local-plugins"
    )


@pytest.fixture
def no_cli_override(monkeypatch):
    monkeypatch.delenv("PICARD_CLI", raising=False)


def test_picard_cli_found_on_path(no_cli_override, monkeypatch, tmp_path):
    cli = tmp_path / "picard-cli"
    cli.write_text("")
    monkeypatch.setattr(install.shutil, "which", lambda name: str(cli))
    assert install.picard_cli() == [str(cli)]


def test_picard_cli_found_in_program_files_on_windows(
    no_cli_override, monkeypatch, tmp_path
):
    cli = tmp_path / "MusicBrainz Picard" / "picard-cli.exe"
    cli.parent.mkdir()
    cli.write_text("")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    as_if_on(monkeypatch, "nt", "win32")
    assert install.picard_cli() == [str(cli)]


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_picard_cli_not_found(no_cli_override, monkeypatch, tmp_path, os_name):
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))  # no Picard installed there
    as_if_on(monkeypatch, os_name)
    with pytest.raises(SystemExit, match="picard-cli not found"):
        install.picard_cli()

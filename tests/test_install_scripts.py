"""Installing the tagger scripts into a real Picard.ini, written and read back with Picard's own config code."""

import importlib.util
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import pytest
from picard import PICARD_VERSION

# Read from the Picard under test, so a PICARD_REF bump needs no edits here.
CURRENT = PICARD_VERSION.short_str()
NEWER = f"{PICARD_VERSION.major + 1}.0.0"

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config_scripts = _load("picard_config_scripts")
install = _load("install")

TITLES = [
    "Remove Hyphens in Title",
    "Set Subtitle",
    "Use Original Date",
    "Various Artists",
    "Voice Actor",
]


def write_config(path, scripts, version=CURRENT, profiles=None):
    import picard.options  # noqa: F401
    from picard.config import Config

    config = Config.from_file(None, str(path))
    config.application["version"] = version
    config.setting["list_of_scripts"] = scripts
    config.setting["enable_tagger_scripts"] = False
    if profiles:
        config.profiles["user_profiles"], config.profiles["user_profile_settings"] = (
            profiles
        )
    config.sync()


def read_config(path):
    from picard.config import Config

    return Config.from_file(None, str(path))


@pytest.fixture
def ini(tmp_path, monkeypatch):
    path = tmp_path / "Picard.ini"
    monkeypatch.setenv("PICARD_CONFIG_FILE", str(path))
    return path


def names(config):
    return [
        (name, enabled) for _, name, enabled, _ in config.setting["list_of_scripts"]
    ]


def test_fresh_config_gets_every_script_enabled_in_file_order(ini):
    write_config(ini, [])
    config_scripts.main()
    config = read_config(ini)
    assert names(config) == [(t, True) for t in TITLES]
    assert config.setting["enable_tagger_scripts"] is True
    import yaml

    va = next(
        row for row in config.setting["list_of_scripts"] if row[1] == "Various Artists"
    )
    ptsp = yaml.safe_load(
        (ROOT / "tagger-scripts" / "04-various-artists.ptsp").read_text(
            encoding="utf-8"
        )
    )
    assert (
        va[3] == ptsp["script"].strip()
    )  # Picard's Import strips it too (ScriptSerializer.update_from_dict)


def test_existing_scripts_replaced_in_place_and_others_kept(ini):
    write_config(
        ini,
        [
            (0, "Mine", True, "$noop()"),
            (1, "Various Artists", False, "old"),  # disabled on purpose: stays disabled
            (2, "Theirs", True, "$noop(x)"),
            (3, "Remove Hyphens in Title (imported)", True, "old2"),
            (
                4,
                "Remove Hyphens in Title",
                True,
                "old3",
            ),  # a renamed copy too: one survives
        ],
    )
    config_scripts.main()
    rows = read_config(ini).setting["list_of_scripts"]
    # Ours fill the slots they held (1 and 3) plus appended ones, in file order; Mine and Theirs stay put.
    assert [(name, enabled) for _, name, enabled, _ in rows] == [
        ("Mine", True),
        ("Remove Hyphens in Title", True),
        ("Theirs", True),
        ("Set Subtitle", True),
        ("Use Original Date", True),
        ("Various Artists", False),
        ("Voice Actor", True),
    ]
    assert [pos for pos, *_ in rows] == list(range(len(rows)))
    assert all(content not in ("old", "old2", "old3") for *_, content in rows)


def test_running_twice_changes_nothing_more(ini):
    write_config(ini, [(0, "Mine", True, "$noop()")])
    config_scripts.main()
    first = read_config(ini).setting["list_of_scripts"]
    config_scripts.main()
    assert read_config(ini).setting["list_of_scripts"] == first


def test_backup_is_the_original_file(ini):
    write_config(ini, [(0, "Mine", True, "$noop()")])
    original = ini.read_bytes()
    config_scripts.main()
    backups = list(ini.parent.glob("Picard.ini.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original


def test_active_profile_override_gets_the_scripts(ini):
    profiles = (
        [{"id": "p1", "title": "Mine", "enabled": True}],
        {"p1": {"list_of_scripts": [(0, "Mine", True, "")]}},
    )
    write_config(ini, [(0, "Base", True, "")], profiles=profiles)
    config_scripts.main()
    config = read_config(ini)
    assert [
        name
        for _, name, *_ in config.profiles["user_profile_settings"]["p1"][
            "list_of_scripts"
        ]
    ] == ["Mine", *TITLES]
    config.setting.set_profiles_override([])  # the base list, with no profile applied
    assert names(config) == [("Base", True)]


def test_none_tracked_profile_skipped_as_picard_reads_it(ini):
    # p1 ticks the option in Profiles with no value yet; Picard reads p2's list.
    profiles = (
        [
            {"id": "p1", "title": "Tracking", "enabled": True},
            {"id": "p2", "title": "Mine", "enabled": True},
        ],
        {
            "p1": {"list_of_scripts": None},
            "p2": {"list_of_scripts": [(0, "P2", True, "")]},
        },
    )
    write_config(ini, [], profiles=profiles)
    config_scripts.main()
    settings = read_config(ini).profiles["user_profile_settings"]
    assert settings["p1"]["list_of_scripts"] is None
    assert [name for _, name, *_ in settings["p2"]["list_of_scripts"]] == [
        "P2",
        *TITLES,
    ]


def test_disabled_profile_untouched_and_reported(ini, capsys):
    stale = [(0, "Various Artists", True, "old")]
    profiles = (
        [{"id": "p1", "title": "Off", "enabled": False}],
        {"p1": {"list_of_scripts": stale}},
    )
    write_config(ini, [], profiles=profiles)
    config_scripts.main()
    config = read_config(ini)
    assert config.profiles["user_profile_settings"]["p1"]["list_of_scripts"] == stale
    assert [name for name, _ in names(config)] == TITLES
    assert (
        "Not updated: profile 'Off' has its own copy of Various Artists"
        in capsys.readouterr().out
    )


def test_any_enabled_duplicate_keeps_the_script_enabled():
    existing = [(0, "A", False, "old"), (1, "A (imported)", True, "old")]
    assert config_scripts.merge(existing, [("A", "new")]) == [(0, "A", True, "new")]


def test_duplicate_titles_refused(ini, tmp_path, monkeypatch):
    write_config(ini, [])
    original = ini.read_bytes()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("01-a.ptsp", "02-b.ptsp"):
        shutil.copyfile(
            ROOT / "tagger-scripts" / "04-various-artists.ptsp", scripts / name
        )
    monkeypatch.setattr(config_scripts, "SCRIPTS", scripts)
    with pytest.raises(
        SystemExit,
        match="02-b.ptsp: another script is already titled 'Various Artists'",
    ):
        config_scripts.main()
    assert ini.read_bytes() == original


def test_imported_suffix_title_refused(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    ptsp = (ROOT / "tagger-scripts" / "04-various-artists.ptsp").read_text(
        encoding="utf-8"
    )
    (scripts / "01-a.ptsp").write_text(
        ptsp.replace("title: Various Artists", "title: Various Artists (imported)"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_scripts, "SCRIPTS", scripts)
    with pytest.raises(SystemExit, match="must not end in"):
        config_scripts.read_scripts()


def test_newer_config_refused_untouched(ini):
    write_config(ini, [], version=NEWER)
    original = ini.read_bytes()
    with pytest.raises(SystemExit, match="newer"):
        config_scripts.main()
    assert ini.read_bytes() == original and not list(ini.parent.glob("*.bak-*"))


def test_older_config_refused_untouched(ini):
    write_config(ini, [], version="2.13.3")
    original = ini.read_bytes()
    with pytest.raises(SystemExit, match=f"start Picard {re.escape(CURRENT)} once"):
        config_scripts.main()
    assert ini.read_bytes() == original and not list(ini.parent.glob("*.bak-*"))


def test_unreadable_config_refused_untouched(ini):
    write_config(ini, [(0, "Mine", True, "$noop()")])
    with ini.open("a") as f:
        f.write("\n[broken\nfoo=1\n")
    original = ini.read_bytes()
    with pytest.raises(SystemExit, match="Can't read"):
        config_scripts.main()
    assert ini.read_bytes() == original and not list(ini.parent.glob("*.bak-*"))


def test_write_failure_reported_with_the_backup(ini):
    write_config(ini, [(0, "Mine", True, "$noop()")])
    original = ini.read_bytes()
    os.chmod(ini, stat.S_IREAD)
    try:
        with pytest.raises(
            SystemExit, match=r"Writing .* failed .*restore .*Picard\.ini\.bak-"
        ):
            config_scripts.main()
    finally:
        os.chmod(ini, stat.S_IREAD | stat.S_IWRITE)
    # The file on disk, not a read back: QSettings shares unsaved values within a process.
    assert ini.read_bytes() == original


def test_config_file_prefers_the_versioned_ini(tmp_path, monkeypatch):
    from PyQt6 import QtCore

    monkeypatch.delenv("PICARD_CONFIG_FILE", raising=False)
    QtCore.QSettings.setPath(
        QtCore.QSettings.Format.IniFormat,
        QtCore.QSettings.Scope.UserScope,
        str(tmp_path),
    )
    plain = tmp_path / "MusicBrainz" / "Picard.ini"
    assert config_scripts.config_file() == plain
    plain.parent.mkdir()
    (plain.parent / f"Picard-{CURRENT}.ini").write_text("")
    assert config_scripts.config_file() == plain.parent / f"Picard-{CURRENT}.ini"


def test_missing_config_refused(ini):
    with pytest.raises(SystemExit, match="No Picard config"):
        config_scripts.main()


def test_install_runs_the_helper_in_the_venv(ini, monkeypatch):
    write_config(ini, [])
    monkeypatch.setattr(install, "picard_running", lambda: False)
    monkeypatch.setattr(install, "installed_picard_version", lambda: CURRENT)
    install.main(["scripts"])
    assert [name for name, _ in names(read_config(ini))] == TITLES


def test_install_refuses_other_picard_version(ini, monkeypatch):
    write_config(ini, [])
    original = ini.read_bytes()
    monkeypatch.setattr(install, "picard_running", lambda: False)
    monkeypatch.setattr(install, "installed_picard_version", lambda: NEWER)
    with pytest.raises(SystemExit, match=f"Installed Picard is {re.escape(NEWER)}"):
        install.main(["scripts"])
    assert ini.read_bytes() == original


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("MusicBrainz Picard 3.0.4", "3.0.4"),
        ("MusicBrainz Picard 3.0.4 (12-abc1234)", "3.0.4"),  # a daily build
        ("", ""),
    ],
)
def test_installed_version_read_from_picard_cli(monkeypatch, tmp_path, output, version):
    fake = tmp_path / "fake_cli.py"
    fake.write_text(f"print({output!r})\n")
    monkeypatch.setattr(install, "picard_cli", lambda: [sys.executable, str(fake)])
    assert install.installed_picard_version() == version


def test_failing_picard_cli_reported(monkeypatch, tmp_path):
    fake = tmp_path / "fake_cli.py"
    fake.write_text("import sys\nsys.exit('no Qt platform plugin')\n")
    monkeypatch.setattr(install, "picard_cli", lambda: [sys.executable, str(fake)])
    with pytest.raises(SystemExit, match="exit code 1: no Qt platform plugin"):
        install.installed_picard_version()


def test_all_installs_scripts_then_plugin(monkeypatch):
    done = []
    monkeypatch.setattr(install, "picard_running", lambda: False)
    monkeypatch.setattr(install, "install_scripts", lambda: done.append("scripts"))
    monkeypatch.setattr(install, "install_plugin", lambda: done.append("plugin"))
    install.main(["all"])
    assert done == ["scripts", "plugin"]

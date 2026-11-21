"""tools/release_plugin.py against a throwaway repo and remote, and Picard installing from what it pushes."""

import gc
import importlib.util
import re
import shutil
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_plugin", ROOT / "tools" / "release_plugin.py"
)
assert _spec and _spec.loader
release_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_plugin)

PLUGIN = ROOT / release_plugin.PREFIX


def git(repo, *args):
    return release_plugin.git(repo, *args)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo shaped like this one, with the real plugin, and an empty bare remote as origin."""
    gitconfig = tmp_path / "gitconfig"  # keep the user's signing and hooks out of it
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("not part of the plugin\n")
    shutil.copytree(
        PLUGIN,
        work / release_plugin.PREFIX,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    commit(work, "1.0.0")
    yield work, remote
    gc.collect()  # Picard's git backend holds files open on Windows


def commit(work, version, extra=None):
    """Set the plugin's version, optionally write an extra plugin file, and commit."""
    manifest = work / release_plugin.PREFIX / "MANIFEST.toml"
    text = re.sub(r"(?m)^version = .*\n", "", manifest.read_text())
    manifest.write_text(text + f'version = "{version}"\n')
    if extra:
        (work / release_plugin.PREFIX / extra).write_text(extra)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", f"plugin {version}")


def remote_refs(remote):
    lines = git(
        remote, "for-each-ref", "--format=%(refname) %(objectname)"
    ).splitlines()
    return dict(line.split() for line in lines)


def test_first_release_tags_a_branch_with_the_plugin_at_its_root(repo):
    work, remote = repo
    assert release_plugin.release(work, "origin") == ("v1.0.0", True)
    refs = remote_refs(remote)
    assert refs["refs/tags/v1.0.0"] == refs[f"refs/heads/{release_plugin.BRANCH}"]
    files = git(remote, "ls-tree", "--name-only", "v1.0.0").splitlines()
    assert sorted(files) == sorted(
        p.name for p in PLUGIN.iterdir() if p.name != "__pycache__"
    )


def test_change_without_version_bump_moves_branch_only(repo):
    work, remote = repo
    release_plugin.release(work, "origin")
    commit(work, "1.0.0", extra="unreleased.py")
    assert release_plugin.release(work, "origin") == ("v1.0.0", False)
    refs = remote_refs(remote)
    branch = refs[f"refs/heads/{release_plugin.BRANCH}"]
    assert branch != refs["refs/tags/v1.0.0"]
    assert git(remote, "merge-base", "--is-ancestor", "v1.0.0", branch) == ""
    assert [r for r in refs if r.startswith("refs/tags/")] == ["refs/tags/v1.0.0"]


def test_change_outside_the_plugin_leaves_the_branch_alone(repo):
    work, remote = repo
    release_plugin.release(work, "origin")
    before = remote_refs(remote)
    (work / "README.md").write_text("edited\n")
    git(work, "commit", "-qam", "readme")
    assert release_plugin.release(work, "origin") == ("v1.0.0", False)
    assert remote_refs(remote) == before


def test_version_bump_tags_again(repo):
    work, remote = repo
    release_plugin.release(work, "origin")
    commit(work, "1.10.0")
    assert release_plugin.release(work, "origin") == ("v1.10.0", True)
    refs = remote_refs(remote)
    assert refs["refs/tags/v1.10.0"] == refs[f"refs/heads/{release_plugin.BRANCH}"]


def test_older_version_refused_and_nothing_pushed(repo):
    work, remote = repo
    commit(work, "1.10.0")
    release_plugin.release(work, "origin")
    before = remote_refs(remote)
    commit(work, "1.9.0", extra="older.py")
    with pytest.raises(SystemExit, match=r"v1\.9\.0 is older than v1\.10\.0"):
        release_plugin.release(work, "origin")
    assert remote_refs(remote) == before


def test_tags_that_are_not_versions_ignored(repo):
    work, _ = repo
    git(work, "tag", "stable")
    git(work, "tag", "v2.0")
    git(work, "tag", "-a", "-m", "annotated", "v0.9.0")
    git(work, "push", "-q", "origin", "stable", "v2.0", "v0.9.0")
    assert release_plugin.release(work, "origin") == ("v1.0.0", True)


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-beta", ""])
def test_version_not_major_minor_patch_refused(repo, version):
    work, remote = repo
    commit(work, version)
    with pytest.raises(SystemExit, match="isn't <major>.<minor>.<patch>"):
        release_plugin.release(work, "origin")
    assert remote_refs(remote) == {}


def test_main_writes_the_tag_for_the_workflow(repo, monkeypatch, tmp_path, capsys):
    work, _ = repo
    output = tmp_path / "github_output"
    monkeypatch.setattr(release_plugin, "ROOT", work)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release_plugin.main([])
    assert output.read_text() == "tag=v1.0.0\n"
    assert capsys.readouterr().out == "Tagged v1.0.0\n"
    release_plugin.main(["--remote", "origin"])
    assert (
        output.read_text() == "tag=v1.0.0\ntag=v1.0.0\n"
    )  # a re-run still creates a missed release
    assert "v1.0.0 already tagged" in capsys.readouterr().out


def test_main_without_github_output(repo, monkeypatch, capsys):
    work, _ = repo
    monkeypatch.setattr(release_plugin, "ROOT", work)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    release_plugin.main([])
    assert capsys.readouterr().out == "Tagged v1.0.0\n"


def test_picard_installs_a_release_and_updates_to_the_next(repo, tmp_path):
    """What the README tells users to do, through Picard's own plugin manager."""
    from picard.plugin3.manager import PluginManager
    from picard.plugin3.plugin import Plugin

    work, remote = repo
    release_plugin.release(work, "origin")
    manager = PluginManager(Mock(webservice=Mock(), register_cleanup=Mock()))
    manager._primary_plugin_dir = tmp_path / "plugins3"
    manager._primary_plugin_dir.mkdir()
    plugin_id = manager.install_plugin(remote.as_uri(), ref="v1.0.0")
    plugin = Plugin(manager._primary_plugin_dir, plugin_id)
    plugin.read_manifest()
    assert plugin.manifest
    assert str(plugin.manifest.version) == "1.0.0.final0"

    commit(work, "1.1.0")
    release_plugin.release(work, "origin")
    result = manager.update_plugin(plugin)
    assert (result.old_ref_item.shortname, result.new_ref_item.shortname) == (
        "v1.0.0",
        "v1.1.0",
    )
    assert str(result.new_version) == "1.1.0.final0"


def test_repo_manifest_version_releasable():
    """The tag this repo's MANIFEST.toml gets is one Picard's update recognises as a version."""
    from picard.util import parse_versioning_scheme

    version = tomllib.loads((PLUGIN / "MANIFEST.toml").read_text())["version"]
    assert release_plugin.VERSION.fullmatch(version)
    pattern = parse_versioning_scheme("semver")
    assert pattern and pattern.match(f"v{version}")

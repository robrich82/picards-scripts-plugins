"""tools/release_plugin.py against a throwaway repo and remote, and Picard installing from what it pushes."""

import gc
import importlib.util
import re
import shutil
import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_plugin", ROOT / "tools" / "release_plugin.py"
)
assert _spec and _spec.loader
release_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_plugin)

PLUGIN = ROOT / release_plugin.PREFIX
BRANCH_REF = f"refs/heads/{release_plugin.BRANCH}"
git = release_plugin.git


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo shaped like this one, with the real plugin at 1.0.0, and an empty bare remote as origin."""
    gitconfig = tmp_path / "gitconfig"  # keep the user's signing and hooks out of it
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)  # set when run from a git hook
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
    return work, remote


def commit(work, version, new_file=None):
    """Set the plugin's version (None: no version line), optionally add an empty plugin file, and commit."""
    manifest = work / release_plugin.PREFIX / "MANIFEST.toml"
    text = re.sub(r"(?m)^version = .*\n", "", manifest.read_text())
    if version is not None:
        text += (
            f"version = {version!r}\n"
            if isinstance(version, int)
            else f'version = "{version}"\n'
        )
    manifest.write_text(text)
    if new_file:
        (work / release_plugin.PREFIX / new_file).write_text("")
    git(work, "add", "-A")
    git(work, "commit", "-q", "--allow-empty", "-m", f"plugin {version}")
    return git(work, "rev-parse", "HEAD")


def release(work):
    return release_plugin.release(work, "origin")


def remote_refs(remote):
    lines = git(
        remote, "for-each-ref", "--format=%(refname) %(objectname)"
    ).splitlines()
    return dict(line.split() for line in lines)


def test_first_release_tags_a_branch_with_the_plugin_at_its_root(repo):
    work, remote = repo
    assert release(work) == ("v1.0.0", True, True)
    refs = remote_refs(remote)
    assert refs["refs/tags/v1.0.0"] == refs[BRANCH_REF]
    files = git(remote, "ls-tree", "--name-only", "v1.0.0").splitlines()
    assert sorted(files) == sorted(
        p.name for p in PLUGIN.iterdir() if p.name != "__pycache__"
    )


def test_change_without_version_bump_moves_branch_only(repo):
    work, remote = repo
    release(work)
    commit(work, "1.0.0", new_file="unreleased.py")
    assert release(work) == ("v1.0.0", False, True)
    refs = remote_refs(remote)
    assert refs[BRANCH_REF] != refs["refs/tags/v1.0.0"]
    assert release_plugin.is_ancestor(remote, "v1.0.0", refs[BRANCH_REF])
    assert [r for r in refs if r.startswith("refs/tags/")] == ["refs/tags/v1.0.0"]


def test_change_outside_the_plugin_leaves_the_branch_alone(repo):
    work, remote = repo
    release(work)
    before = remote_refs(remote)
    (work / "README.md").write_text("edited\n")
    git(work, "commit", "-qam", "readme")
    assert release(work) == ("v1.0.0", False, True)
    assert remote_refs(remote) == before


def test_version_bump_tags_again(repo):
    work, remote = repo
    release(work)
    commit(work, "1.10.0")
    assert release(work) == ("v1.10.0", True, True)
    refs = remote_refs(remote)
    assert refs["refs/tags/v1.10.0"] == refs[BRANCH_REF]


def test_version_older_than_the_newest_tag_refused(repo):
    work, remote = repo
    for version in ("1.2.0", "1.10.0"):  # v1.2.0 sorts after v1.10.0 as a string
        commit(work, version)
        release(work)
    before = remote_refs(remote)
    commit(work, "1.5.0", new_file="older.py")
    with pytest.raises(SystemExit, match=r"v1\.5\.0 is older than v1\.10\.0"):
        release(work)
    assert remote_refs(remote) == before


def test_reverted_bump_refused(repo):
    """Back to an already tagged version: the branch would go backwards under the newer tag."""
    work, remote = repo
    release(work)
    commit(work, "1.1.0", new_file="feature.py")
    release(work)
    before = remote_refs(remote)
    git(work, "revert", "--no-edit", "HEAD")
    with pytest.raises(SystemExit, match=r"v1\.0\.0 is older than v1\.1\.0"):
        release(work)
    assert remote_refs(remote) == before


def test_rerun_of_an_older_commit_reports_its_tag_and_pushes_nothing(repo):
    """A failed release step re-run after a later release: the tag is there, only the release is missing."""
    work, remote = repo
    first = git(work, "rev-parse", "HEAD")
    release(work)
    commit(work, "1.1.0")
    release(work)
    before = remote_refs(remote)
    git(work, "checkout", "-q", first)
    assert release(work) == ("v1.0.0", False, False)
    assert remote_refs(remote) == before


def test_rerun_finds_its_tag_on_a_later_commit(repo):
    """Its own run failed before pushing, and a later run of the same version made the tag."""
    work, remote = repo
    first = git(work, "rev-parse", "HEAD")
    commit(work, "1.0.0", new_file="later.py")
    release(work)
    before = remote_refs(remote)
    git(work, "checkout", "-q", first)
    assert release(work) == ("v1.0.0", False, True)
    assert remote_refs(remote) == before


def test_tagging_a_commit_the_branch_moved_past_refused(repo):
    """A bump whose run never finished, reverted since, then re-run: it would release reverted code."""
    work, remote = repo
    release(work)
    bump = commit(work, "1.1.0", new_file="feature.py")
    git(work, "revert", "--no-edit", "HEAD")
    release(work)
    before = remote_refs(remote)
    git(work, "checkout", "-q", bump)
    with pytest.raises(
        SystemExit, match=r"v1\.1\.0 would tag a commit .* has moved past"
    ):
        release(work)
    assert remote_refs(remote) == before


def test_annotated_current_tag_checked_by_its_commit(repo):
    work, remote = repo
    release(work)
    git(remote, "tag", "-f", "-a", "-m", "annotated", "v1.0.0", "v1.0.0")
    commit(work, "1.0.0", new_file="unreleased.py")
    assert release(work) == ("v1.0.0", False, True)


def test_diverged_branch_refused_and_no_tag_pushed(repo):
    """No --force, so the branch isn't rewritten; --atomic, so the tag doesn't land without it."""
    work, remote = repo
    git(
        work, "push", "-q", "origin", f"HEAD:{BRANCH_REF}"
    )  # history the split doesn't share
    before = remote_refs(remote)
    commit(work, "1.1.0")
    with pytest.raises(SystemExit, match=r"(?s)git push .* failed:.*non-fast-forward"):
        release(work)
    assert remote_refs(remote) == before


def test_existing_tag_off_the_branch_refused(repo):
    work, remote = repo
    git(work, "push", "-q", "origin", "HEAD:refs/tags/v1.0.0")  # tagged by hand on main
    with pytest.raises(
        SystemExit, match=r"Tag v1\.0\.0 isn't on dist/highest-disc-total"
    ):
        release(work)
    assert BRANCH_REF not in remote_refs(remote)


@pytest.mark.parametrize("tag", ["v2.0", "2.0.0", "release-3.0", "V9.0.0", "v01.0.0"])
def test_other_tags_picard_takes_for_versions_refused(repo, tag):
    work, remote = repo
    git(work, "push", "-q", "origin", f"HEAD:refs/tags/{tag}")
    with pytest.raises(SystemExit, match=f"Tag {tag} looks like a version to Picard"):
        release(work)
    assert BRANCH_REF not in remote_refs(remote)


def test_tags_picard_does_not_take_for_versions_ignored(repo):
    work, _ = repo
    for tag in ("stable", "v2.0.0-rc1", "v2.0.0.1"):
        git(work, "push", "-q", "origin", f"HEAD:refs/tags/{tag}")
    assert release(work) == ("v1.0.0", True, True)


def test_annotated_tag_compared_by_its_version(repo):
    work, _ = repo
    git(work, "tag", "-a", "-m", "annotated", "v2.0.0")
    git(work, "push", "-q", "origin", "v2.0.0")
    with pytest.raises(SystemExit, match=r"v1\.0\.0 is older than v2\.0\.0"):
        release(work)


@pytest.mark.parametrize(
    "version", ["1.0", "v1.0.0", "1.0.0-beta", "", "01.0.0", "1.١.0", None, 1]
)
def test_version_not_major_minor_patch_refused(repo, version):
    work, remote = repo
    commit(work, version)
    with pytest.raises(
        SystemExit, match="isn't <major>.<minor>.<patch> without leading zeros"
    ):
        release(work)
    assert remote_refs(remote) == {}


def test_is_ancestor_fails_loudly_on_a_missing_commit(repo):
    work, _ = repo
    with pytest.raises(SystemExit, match="merge-base --is-ancestor"):
        release_plugin.is_ancestor(work, "0" * 40, "HEAD")


def test_main_writes_the_tag_for_the_workflow_every_run(
    repo, monkeypatch, tmp_path, capsys
):
    work, _ = repo
    output = tmp_path / "github_output"
    monkeypatch.setattr(release_plugin, "ROOT", work)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release_plugin.main([])
    assert capsys.readouterr().out == "Tagged v1.0.0\n"
    # The second run also writes it, so its release step creates a release the first one missed.
    release_plugin.main([])
    assert output.read_text() == "tag=v1.0.0\nlatest=true\n" * 2
    assert capsys.readouterr().out.startswith("v1.0.0 already tagged")


def test_main_pushes_to_the_named_remote(repo, monkeypatch, tmp_path):
    work, remote = repo
    mirror = tmp_path / "mirror.git"
    git(tmp_path, "init", "-q", "--bare", str(mirror))
    git(work, "remote", "add", "mirror", str(mirror))
    monkeypatch.setattr(release_plugin, "ROOT", work)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    release_plugin.main(["--remote", "mirror"])
    assert "refs/tags/v1.0.0" in remote_refs(mirror)
    assert remote_refs(remote) == {}


def test_picard_installs_a_release_and_updates_to_the_next(repo, tmp_path):
    """What the README tells users to do, through Picard's own plugin manager.

    Released into a non-bare repo: Picard clones a local folder only when it has a .git, and
    reads a file:// URL as a folder on Linux but not on Windows. From a local repo it fetches new
    tags before updating; from GitHub it needs the refresh the README describes first.
    """
    from picard.plugin3.manager import PluginManager
    from picard.plugin3.plugin import Plugin

    work, _ = repo
    source = tmp_path / "source"
    git(
        tmp_path, "init", "-q", "-b", "main", str(source)
    )  # main unborn, so pushes land
    git(work, "remote", "add", "source", str(source))
    release_plugin.release(work, "source")
    manager = PluginManager(Mock(webservice=Mock(), register_cleanup=Mock()))
    manager._primary_plugin_dir = tmp_path / "plugins3"  # not the user's plugin folder
    manager._primary_plugin_dir.mkdir()
    try:
        plugin_id = manager.install_plugin(str(source), ref="v1.0.0")
        plugin = Plugin(manager._primary_plugin_dir, plugin_id)
        plugin.read_manifest()
        assert plugin.manifest
        assert str(plugin.manifest.version) == "1.0.0.final0"

        commit(work, "1.1.0")
        release_plugin.release(work, "source")
        result = manager.update_plugin(plugin)
        assert (result.old_ref_item.shortname, result.new_ref_item.shortname) == (
            "v1.0.0",
            "v1.1.0",
        )
        assert str(result.new_version) == "1.1.0.final0"
    finally:
        gc.collect()  # Picard's git backend holds files open on Windows


def test_repo_manifest_version_releasable():
    """The tag this repo's MANIFEST.toml gets is one Picard's update recognises as a version."""
    from picard.util import parse_versioning_scheme

    version = tomllib.loads((PLUGIN / "MANIFEST.toml").read_text())["version"]
    assert release_plugin.TAG.fullmatch(f"v{version}")
    pattern = parse_versioning_scheme("semver")
    assert (
        pattern and pattern.pattern == f"^{release_plugin.PICARD_VERSION_TAG.pattern}$"
    )


def test_workflow_runs_the_script_on_main_and_releases_its_tag():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/release-plugin.yml").read_text()
    )
    paths = workflow[True]["push"]["paths"]  # YAML 1.1 reads the `on` key as True
    assert f"{release_plugin.PREFIX}/**" in paths and "tools/release_plugin.py" in paths
    job = workflow["jobs"]["release"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    checkout, script, publish = job["steps"]
    assert (
        checkout["with"]["fetch-depth"] == 0
    )  # a shallow split would differ from the pushed one
    assert (script["id"], script["run"]) == (
        "release",
        "python3 tools/release_plugin.py",
    )
    assert publish["env"]["TAG"] == "${{ steps.release.outputs.tag }}"
    assert publish["env"]["LATEST"] == "${{ steps.release.outputs.latest }}"

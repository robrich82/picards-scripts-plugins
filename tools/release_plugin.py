"""Publish the plugin as git refs Picard can install from, for .github/workflows/release-plugin.yml.

Picard 3 installs a plugin by cloning a repo and reading MANIFEST.toml at its root, so the
plugin folder's history is split onto its own branch. A `version` in MANIFEST.toml with no tag
yet also tags the split commit v<version>; Picard's update moves a tag install to the newest one.

Pushes to the remote (default origin): python tools/release_plugin.py [--remote NAME]
"""

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "plugins/highest-disc-total"
BRANCH = "dist/highest-disc-total"
# What Picard's update takes for a version tag: picard.util.parse_versioning_scheme("semver").
PICARD_VERSION_TAG = re.compile(r"\D*\d+\.\d+(\.\d+)?")
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def is_ancestor(repo, ancestor, descendant):
    code = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        check=False,
    ).returncode
    if code > 1:
        raise SystemExit(f"git merge-base --is-ancestor {ancestor} {descendant} failed")
    return code == 0


def remote_refs(repo, remote):
    """{ref: commit} on the remote. An annotated tag's peeled line follows it, so the commit wins."""
    refs = {}
    for line in git(repo, "ls-remote", remote).splitlines():
        commit, ref = line.split("\t")
        refs[ref.removesuffix("^{}")] = commit
    return refs


def version_tags(refs):
    """{tag: (major, minor, patch)} for the remote's tags that Picard's update would consider."""
    tags = {}
    for ref in refs:
        name = ref.removeprefix("refs/tags/")
        if name == ref or not PICARD_VERSION_TAG.fullmatch(name):
            continue
        match = TAG.fullmatch(name)
        if not match:
            raise SystemExit(
                f"Tag {name} looks like a version to Picard, whose update would move to it: delete it"
            )
        tags[name] = tuple(map(int, match.groups()))
    return tags


def release(repo, remote):
    """Bring BRANCH up to HEAD's plugin history, with v<version> on it.

    Returns (tag, whether it is new, whether it is the newest release).
    """
    manifest = tomllib.loads(git(repo, "show", f"HEAD:{PREFIX}/MANIFEST.toml"))
    version = manifest.get("version")
    match = TAG.fullmatch(f"v{version}") if isinstance(version, str) else None
    if not match:
        raise SystemExit(
            f"{PREFIX}/MANIFEST.toml: version {version!r} isn't <major>.<minor>.<patch> without leading zeros"
        )
    tag, current = f"v{version}", tuple(map(int, match.groups()))
    refs = remote_refs(repo, remote)
    tags = version_tags(refs)
    split = git(repo, "subtree", "split", f"--prefix={PREFIX}", "HEAD")
    tip = refs.get(f"refs/heads/{BRANCH}")
    have = [r for r in (f"refs/heads/{BRANCH}", f"refs/tags/{tag}") if r in refs]
    if have:
        git(repo, "fetch", "-q", "--no-tags", remote, *have)
    # Not moving: a re-run of an older commit, which only needs its release created.
    moves = not (tip and is_ancestor(repo, split, tip))
    new = tag not in tags
    if new and not moves:
        raise SystemExit(
            f"{tag} would tag a commit {BRANCH} has moved past, such as a reverted bump: release from main"
        )
    newest = max(tags, key=tags.__getitem__, default=None)
    if moves and newest and tags[newest] > current:
        raise SystemExit(
            f"{tag} is older than {newest}, the release Picard updates to: raise version past it"
        )
    if not new and not is_ancestor(
        repo, refs[f"refs/tags/{tag}"], split if moves else tip
    ):
        raise SystemExit(
            f"Tag {tag} isn't on {BRANCH}, so it has no plugin at its root"
        )
    push = [f"{split}:refs/heads/{BRANCH}"] if moves else []
    push += [f"{split}:refs/tags/{tag}"] if new else []
    if push:
        # Atomic, so a refused branch leaves no tag; no --force, as rewriting BRANCH breaks installs.
        git(repo, "push", "-q", "--atomic", remote, *push)
    return tag, new, not newest or tags[newest] <= current


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    tag, new, latest = release(ROOT, args.remote)
    print(f"Tagged {tag}" if new else f"{tag} already tagged; {BRANCH} up to date")
    # Every run, so re-running one whose release step failed creates the release.
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as out:
            out.write(f"tag={tag}\nlatest={str(latest).lower()}\n")


if __name__ == "__main__":
    main()

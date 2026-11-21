"""Publish the plugin as git refs Picard can install from, for .github/workflows/release-plugin.yml.

Picard 3 installs a plugin by cloning a repo and reading MANIFEST.toml at its root, so the
plugin folder's history is split onto its own branch. A `version` in MANIFEST.toml with no tag
yet also tags the split commit v<version>; Picard's update moves a tag install to the newest one.

Usage: python tools/release_plugin.py [--remote NAME]
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
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def version_tags(repo, remote):
    """The remote's v<major>.<minor>.<patch> tags, as {tag: (major, minor, patch)}."""
    tags = {}
    for line in git(repo, "ls-remote", "--tags", remote).splitlines():
        name = line.split("\t")[1].removeprefix("refs/tags/")
        match = VERSION.fullmatch(name.removeprefix("v"))
        if name.startswith("v") and match:
            tags[name] = tuple(map(int, match.groups()))
    return tags


def release(repo, remote):
    """Push HEAD's plugin history to BRANCH, tagged when its version is new. Returns (tag, whether new)."""
    manifest = tomllib.loads(git(repo, "show", f"HEAD:{PREFIX}/MANIFEST.toml"))
    version = str(manifest.get("version", ""))
    match = VERSION.fullmatch(version)
    if not match:
        raise SystemExit(
            f"{PREFIX}/MANIFEST.toml: version {version!r} isn't <major>.<minor>.<patch>"
        )
    tag = f"v{version}"
    tags = version_tags(repo, remote)
    split = git(repo, "subtree", "split", f"--prefix={PREFIX}", "HEAD")
    refs = [f"{split}:refs/heads/{BRANCH}"]
    if tag not in tags:
        newest = max(tags, key=tags.__getitem__, default=None)
        if newest and tags[newest] > tuple(map(int, match.groups())):
            raise SystemExit(
                f"{tag} is older than {newest}, so Picard would never update to it"
            )
        refs.append(f"{split}:refs/tags/{tag}")
    # No --force: rewriting BRANCH would break every install that follows it.
    git(repo, "push", "--atomic", remote, *refs)
    return tag, tag not in tags


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    tag, new = release(ROOT, args.remote)
    print(f"Tagged {tag}" if new else f"Updated {BRANCH}; {tag} already tagged")
    if os.environ.get(
        "GITHUB_OUTPUT"
    ):  # every run, so a re-run creates a release a failed one missed
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"tag={tag}\n")


if __name__ == "__main__":
    main()

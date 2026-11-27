"""Renovate's regex managers find every version they are meant to update.

A pattern that stops matching fails silently: Renovate drops the file from its dependency list and
bumps the rest without it.
"""

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "renovate.json").read_text(encoding="utf-8"))
REGEX_MANAGERS = [m for m in CONFIG["customManagers"] if m["customType"] == "regex"]
TRACKED = subprocess.run(
    ["git", "ls-files", "-z"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    encoding="utf-8",
    check=True,
).stdout.split("\0")[:-1]


def python_pattern(renovate_pattern):
    """Renovate writes named groups the JavaScript way, (?<name>...)."""
    return re.compile(re.sub(r"\(\?<(?=[A-Za-z])", "(?P<", renovate_pattern))


def managed_files(manager):
    patterns = [re.compile(p.strip("/")) for p in manager["managerFilePatterns"]]
    return [f for f in TRACKED if any(p.search(f) for p in patterns)]


def test_there_are_regex_managers():
    assert REGEX_MANAGERS


@pytest.mark.parametrize(
    "manager", REGEX_MANAGERS, ids=[m["depNameTemplate"] for m in REGEX_MANAGERS]
)
def test_each_file_has_every_version_matched_once(manager):
    files = managed_files(manager)
    assert len(files) == len(manager["managerFilePatterns"])
    patterns = [python_pattern(p) for p in manager["matchStrings"]]
    found = {}
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8")
        values = [m["currentValue"] for p in patterns for m in p.finditer(text)]
        assert values, f"no matchString matches {name}"
        found[name] = values
    versions = {v for values in found.values() for v in values}
    assert len(versions) == 1, found
    # An occurrence no pattern catches is left behind on the next bump.
    (version,) = versions
    for name, values in found.items():
        count = (ROOT / name).read_text(encoding="utf-8").count(version)
        assert count == len(values), (
            f"{name} names {version} {count} times; the patterns match {len(values)}"
        )


@pytest.mark.parametrize(
    "manager", REGEX_MANAGERS, ids=[m["depNameTemplate"] for m in REGEX_MANAGERS]
)
def test_a_version_bump_runs_the_tests(manager):
    """The test above reads these files, so CI's path filter must run the tests for them."""
    spec = importlib.util.spec_from_file_location(
        "ci_changes", ROOT / "tools" / "ci_changes.py"
    )
    assert spec and spec.loader
    ci_changes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_changes)
    for name in managed_files(manager):
        assert "test" in ci_changes.outputs_for(name), name

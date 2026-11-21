---
name: picard-plugin
description: Use when adding, changing or debugging a Picard 3 plugin under plugins/ in this repo, including its MANIFEST.toml, tests, README row and release. Covers where the plugin API at the tested Picard release is documented, the Python version a plugin may use, the test harness, version bumps, and what a second plugin would break.
---

# Picard 3 plugins

The reference is the Picard release under test, `PICARD_REF` in `tools/run_tests.py`, not the online docs: `.picard/src/docs/PLUGINSV3/API.md` (the `api` object and every `register_*` hook, with its signature), `MANIFEST.md` (fields and limits), and the code in `.picard/src/picard/plugin3/`. Run `python tools/run_tests.py` once if `.picard/` is missing.

## Layout

A plugin is a folder `plugins/<id>/` holding `MANIFEST.toml` and `__init__.py`, whose `enable(api)` registers the hooks. The folder name is the plugin's id and install path, which is why `N999` is ignored for it in `pyproject.toml`.

## Python version

A plugin runs on Picard's own Python, whose minimum is `requires-python` in `.picard/src/pyproject.toml` (3.10 at `release-3.0.5`). `plugins/**` is `py310` in `per-file-target-version`, so Ruff rejects newer syntax. It doesn't catch newer stdlib APIs (`tomllib`, `ExceptionGroup`, `datetime.UTC`…): see CLAUDE.md for how those are checked. Import only from Picard and the stdlib: Picard 3 has no way to install a plugin's dependencies (Q13 in `.picard/src/docs/PLUGINSV3/DECISIONS.md`).

## MANIFEST.toml

- `description` is one line of at most 200 characters, and `long_description` is at most 2000 (`MANIFEST.md`). `test_manifest_valid_for_this_picard` runs Picard's own validator.
- `api` must include an API version that the tested Picard declares in `api_versions` (`.picard/src/picard/__init__.py`). When a Picard bump adds an API version, add it only after the tests pass on it.
- `long_description` and the plugin's README row describe the same behaviour. Change both in the same commit, and dispatch the `behaviour-docs-reviewer` agent in self-review.
- Never change `uuid`: Picard identifies the installed plugin by it.

## Versions and releases

README's "Releasing the plugin" is the source of truth. In short: raising `version` and merging to `main` releases it as tag `v<version>`, while a merge without a bump only moves the `dist/` branch. Bump when a change should reach users who install from a tag. Use semver: patch for a fix, minor for new behaviour, major for a change a user must react to. Never create a tag or release by hand.

## Tests

`tests/test_highest_disc_total.py` loads the plugin through Picard's own `Plugin` loader and enables it with the test tagger, then drives real `Album`, `Track` and `File` objects. The hooks fire as they do in Picard: `track.add_file` and `remove_file` run the addition and removal processors. Follow its pattern:

1. Write a test that fails without the change, named for the behaviour (`test_edited_total_kept`), and assert on the file metadata a user sees.
2. Cover the neighbouring cases the change must not break, especially user edits, Preserved Tags and files moved between tracks.
3. `plugins/` is measured for 100% branch coverage: run `python tools/run_tests.py --coverage` before pushing, because a subset always fails it.

## A second plugin

These assume `highest-disc-total` is the only plugin, so adding another means generalising them first:

- `PREFIX` and `BRANCH` in `tools/release_plugin.py`, and the `paths` in `.github/workflows/release-plugin.yml`
- `PLUGIN` in `tools/install.py`, which `mise run install-plugin` calls
- the release tags, which are a bare `v<version>`: two plugins would share one tag namespace, and Picard updates to the newest tag. Settle the scheme, and check it against `PICARD_VERSION_TAG` in `release_plugin.py`, before the first release.

# picards-scripts-plugins

Tagger scripts and a plugin for MusicBrainz Picard 3. README.md covers install, tests, lint and versions; read it first.

## Commands

```sh
python tools/run_tests.py [pytest args]   # the only way to run tests: pytest lives in the .picard/venv it builds
uv run ruff check && uv run ruff format --check
uv run ty check                           # needs one run_tests.py first: reads .picard/venv and .picard/src
```

## Gotchas

- `.picard/src` in every worktree is a metabrainz/picard clone, so gh run there targets upstream. `.claude/settings.json` sets `GH_REPO` for Claude sessions, but `gh repo view/fork/sync/edit` ignore it: give them the repo as an argument. Pass `-R` for any other repo, and in a plain terminal.
- `tools/run_tests.py` and `tools/install.py` run on the system Python before the 3.12 venv exists, so keep them 3.9-compatible.
- Don't run `make install*` / `tools/install.py` unless asked: they write the user's real Picard config and plugin folder.
- Behaviour is documented in the README tables and, for the plugin, `MANIFEST.toml` `long_description`. Update them in the same change.
- Tagger scripts run in filename order (04 before 05 matters). A script that writes a new tag needs it added to `WRITTEN` in `tests/test_tagger_scripts.py`.

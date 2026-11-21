# picards-scripts-plugins

Tagger scripts and a plugin for MusicBrainz Picard 3. README.md covers install, tests, lint and versions; read it first.

## Commands

```sh
python tools/run_tests.py [pytest args]   # the only way to run tests: pytest lives in the .picard/venv it builds
python tools/run_tests.py --coverage      # also measures coverage (slower); fails below 100%, so any subset of tests fails
uv run ruff check && uv run ruff format --check
uv run ty check                           # needs one run_tests.py first: reads .picard/venv and .picard/src
uv run python tools/check_min_python.py [file...]  # flags stdlib APIs newer than a file's per-file-target-version
```

## Gotchas

- `.picard/src` in every worktree is a metabrainz/picard clone, and gh run from there targets upstream, so name `robrich82/picards-scripts-plugins` in every gh call that acts on a repo: `-R` where the command's `--help` lists it (`pr`, `issue`, `run`, …), the positional argument for `gh repo view`/`edit`, and the path for `gh api` (`repos/robrich82/picards-scripts-plugins/...`, never `{owner}/{repo}`). `gh auth` and `gist` take no repo.
- `per-file-target-version` in `pyproject.toml` lists the files that run on an older Python than the test venv's 3.14: the `py39` ones (`tools/run_tests.py`, `tools/install.py`, `.claude/hooks/`) run on the system Python before the venv exists, and the plugin (`py310`) runs on Picard's own Python. Ruff's target version catches newer syntax, and `tools/check_min_python.py` newer stdlib APIs (README's "Lint, format and type check"). Nothing catches `X | Y` unions in a `py39` file: check those by hand. A new file of either kind goes in that list.
- Edit `.ptsp` and `.py` files with Edit/Write, not a shell command. The PostToolUse hooks in `.claude/settings.json` only see those tools: they check `.ptsp` edits with Picard's parser (this needs the venv from one `run_tests.py` run) and run `ruff format` and `tools/check_min_python.py` on `.py` edits.
- Don't run the `mise run install-*` tasks / `tools/install.py` unless asked: they write the user's real Picard config and plugin folder.
- Behaviour is documented in the README tables and, for the plugin, `MANIFEST.toml` `long_description`. Update them in the same change. In self-review of such a change, dispatch the `behaviour-docs-reviewer` agent with the base ref.
- Never create a tag Picard reads as a version (`v1.2.3`, `2.0`, `release-1.2`…) or a GitHub release by hand; see README's "Releasing the plugin".
- Tagger scripts run in filename order (04 before 05 matters). A script that writes a new tag needs it added to `WRITTEN` in `tests/test_tagger_scripts.py`.

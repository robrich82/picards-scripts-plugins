# picards-scripts-plugins

My tagger scripts and plugin for [MusicBrainz Picard](https://picard.musicbrainz.org/) 3, tested against Picard 3.0.4.

## Tagger scripts

| File | What it does |
| --- | --- |
| [`01-remove-hyphens-in-title.ptsp`](tagger-scripts/01-remove-hyphens-in-title.ptsp) | Replaces ` - ` in titles with an en dash: `Song - Live` → `Song – Live`. |
| [`02-set-subtitle.ptsp`](tagger-scripts/02-set-subtitle.ptsp) | Title-cases the disc subtitle. |
| [`03-use-original-date.ptsp`](tagger-scripts/03-use-original-date.ptsp) | Pads `originaldate` and `date` to `YYYY-MM-DD` (`01` for a missing month or day). `date` becomes the original release date when there is one, unless it is already a more precise date within it: original `1995` with date `1995-03-02` keeps `1995-03-02`. `originalyear` is the year of the final `date`. |
| [`04-various-artists.ptsp`](tagger-scripts/04-various-artists.ptsp) | Strips featured artists (`feat.`, `featuring`, `ft`, `meets`, `produced by`, also in brackets) from the album artist and `albumartists`, keeping the main artist's sort name. Two or more main artists become `Various Artists`, with `albumartists` listing them and voice actors in place of characters; the actors inside a `(CV…)` credit don't count as main artists. |
| [`05-voice-actor.ptsp`](tagger-scripts/05-voice-actor.ptsp) | Turns a character credit into the voice actor, in `albumartist` and `albumartists`: `Chin-lan Chang(CV.Maki Kawase)` → `Maki Kawase`, sorted as `Kawase, Maki`. Accepts `CV.`, `CV:` and full-width `（ＣＶ：…）`. |

**Order matters.** Picard runs scripts top to bottom, and Various Artists must run before Voice Actor. Keep them in file-number order.

### Installing a script

With Picard closed, run `make install-scripts` (`make install` installs the scripts and the plugin). It writes the scripts into Picard's config and turns on **Enable Tagger Script(s)**:

- These scripts end up in file order. They take the places they already held, plus new places at the end, and your other scripts stay where they are.
- A script already there, under its name or with ` (imported)` appended (English UI), is replaced and renamed back. It stays enabled if any copy of it was, and other copies are removed. A new one is enabled.
- If an enabled profile has its own script list, that's the one updated, as Picard reads it. It names any other profile that has its own copy of one of these scripts.

It saves a copy of the config first, next to it, with `.bak-<timestamp>` added to the name. It refuses to run unless the installed Picard is the version the tests use (`PICARD_REF`), because it writes the config with that version's code. `PICARD_CONFIG_FILE` sets the config file, for an install that keeps it somewhere else (Flatpak, Snap, portable). Such an install also needs `PICARD_CLI` pointed at a single program that runs its `picard-cli`, such as a wrapper script.

By hand: Options → Options… → Scripting. Tick **Enable Tagger Script(s)** if it isn't already, then **Import** and pick the `.ptsp` file. Picard adds it **disabled**, with ` (imported)` appended to the name: rename it, tick it to enable it, move it into place, and delete the old version if you're replacing one.

## Plugins

| Plugin | What it does |
| --- | --- |
| [`highest-disc-total`](plugins/highest-disc-total) | Sets Total Discs to the number of discs with matched files and renumbers later discs to close the gaps: `1` for a CD+DVD release when you only have the CD, and discs 1 and 3 of 4 become `1/2` and `2/2`. Restores the numbering when a missing disc is matched, and leaves alone a value you edit; add the tag to Preserved Tags to keep MusicBrainz's own value. On a release whose discs share recordings, saved renumbered files can re-match to the earlier disc when reloaded. |

### Installing a plugin

With Picard closed, run `make install-plugin` (`mise install` provides `make`). It copies the plugin to `local-plugins` in Picard's data folder (`%APPDATA%\MusicBrainz` on Windows) and registers it with `picard-cli`; run it again to update (it reinstalls, so Picard rereads the new version and manifest). `PICARD_CLI` sets the path to `picard-cli` when it isn't on `PATH` or in the default Windows install folder.

By hand: Picard runs a local plugin from the folder you give it, without copying it, so first copy `plugins/highest-disc-total` somewhere it can stay (e.g. `%APPDATA%\MusicBrainz\local-plugins\`).

Options → Options… → Plugins → **Install Plugin…**, then the **Local** tab, and pick that folder. Answer **Yes** to the "not managed by git" warning.

Or, with Picard closed, `picard-cli --yes plugins install C:/path/to/highest-disc-total` on that folder. Use forward slashes: a path with no `/` is looked up as a registry ID. On Windows the command may end with a `UnicodeEncodeError` traceback while printing its result; the plugin is installed anyway.

## Tests

The tests run every script, in file order, through Picard's real script parser on sample tracks, and load the plugin through Picard's plugin loader. They need `git` and [`uv`](https://docs.astral.sh/uv/); `mise install` installs the pinned uv:

```sh
python tools/run_tests.py
```

The first run downloads Picard's source (`release-3.0.4`) and builds a Python 3.12 environment for it, both in the git-ignored `.picard/`. Extra arguments go to pytest, e.g. `python tools/run_tests.py -k CV`. On headless Linux, Qt also needs `libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0` (Debian/Ubuntu names).

To check a different Picard version, change `PICARD_REF` in [`tools/run_tests.py`](tools/run_tests.py).

## Lint, format and type check

[Ruff](https://docs.astral.sh/ruff/) and [ty](https://docs.astral.sh/ty/) are pinned in `uv.lock`:

```sh
uv sync --locked
uv run ruff check
uv run ruff format --check
uv run ty check
```

`ty check` reads Picard's source and the test environment from `.picard/`, so run the tests once first.

CI runs these and the tests on every pull request and push to `main` ([`checks.yml`](.github/workflows/checks.yml)).

`ruff format` applies the formatting; `.git-blame-ignore-revs` skips the commit that first applied it (`git config blame.ignoreRevsFile .git-blame-ignore-revs`).

## Licence

[GPL-3.0-or-later](LICENSE).

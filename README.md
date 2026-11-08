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

Options → Options… → Scripting. Tick **Enable Tagger Script(s)** if it isn't already, then **Import** and pick the `.ptsp` file. Picard adds it **disabled**, with ` (imported)` appended to the name: rename it, tick it to enable it, move it into place, and delete the old version if you're replacing one.

## Plugins

| Plugin | What it does |
| --- | --- |
| [`highest-disc-total`](plugins/highest-disc-total) | Lowers Total Discs to the highest disc number that has matched files, e.g. to `1` for a CD+DVD release when you only have the CD. Never raises it above the MusicBrainz value, restores it when files for a later disc are matched, and leaves alone a Total Discs you have edited. |

### Installing a plugin

Picard runs a local plugin from the folder you give it, without copying it, so first copy `plugins/highest-disc-total` somewhere it can stay (e.g. `%APPDATA%\MusicBrainz\local-plugins\`).

Options → Options… → Plugins → **Install Plugin…**, then the **Local** tab, and pick that folder. Answer **Yes** to the "not managed by git" warning.

Or, with Picard closed, `picard-cli --yes plugins install C:/path/to/highest-disc-total` on that folder. Use forward slashes: a path with no `/` is looked up as a registry ID. On Windows the command may end with a `UnicodeEncodeError` traceback while printing its result; the plugin is installed anyway.

## Tests

The tests run every script, in file order, through Picard's real script parser on sample tracks, and load the plugin through Picard's plugin loader. They need `git` and [`uv`](https://docs.astral.sh/uv/) 0.9.17 or later:

```sh
python tools/run_tests.py
```

The first run downloads Picard's source (`release-3.0.4`) into `.picard/` and builds a Python 3.12 environment in `.venv/`; both are git-ignored. Extra arguments go to pytest, e.g. `python tools/run_tests.py -k CV`. On headless Linux, Qt also needs `libgl1 libegl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0` (Debian/Ubuntu names).

To check a different Picard version, change `PICARD_REF` in [`tools/run_tests.py`](tools/run_tests.py).

## Licence

[GPL-3.0-or-later](LICENSE).

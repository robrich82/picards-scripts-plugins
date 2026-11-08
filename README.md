# picards-scripts-plugins

My tagger scripts for [MusicBrainz Picard](https://picard.musicbrainz.org/) 3, tested against Picard 3.0.4.

## Tagger scripts

| File | What it does |
| --- | --- |
| [`01-remove-hyphens-in-title.ptsp`](tagger-scripts/01-remove-hyphens-in-title.ptsp) | Replaces ` - ` in titles with an en dash: `Song - Live` → `Song – Live`. |
| [`02-set-subtitle.ptsp`](tagger-scripts/02-set-subtitle.ptsp) | Title-cases the disc subtitle. |
| [`03-use-original-date.ptsp`](tagger-scripts/03-use-original-date.ptsp) | Pads `originaldate` and `date` to `YYYY-MM-DD` with `01` for a missing month/day, then sets `date` to the original release date when there is one, and `originalyear` to the year of the final `date`. |
| [`04-various-artists.ptsp`](tagger-scripts/04-various-artists.ptsp) | Strips featured artists (`feat.`, `(feat. …)`, `featuring`, `ft`, `meets`, `produced by` between two credited names) from the album artist, keeping the main artist's MusicBrainz sort name; when it strips one, `albumartists` becomes that one artist. Two or more main artists become `Various Artists`, and `albumartists` keeps their names, with voice actors in place of characters. The actor inside a `(CV…)` credit isn't counted, but two characters still make `Various Artists`. |
| [`05-voice-actor.ptsp`](tagger-scripts/05-voice-actor.ptsp) | Turns a character credit into the voice actor, in `albumartist` and `albumartists`: `Chin-lan Chang(CV.Maki Kawase)` → `Maki Kawase`, sorted as `Kawase, Maki`. Accepts `CV.`, `CV:` and full-width `（ＣＶ：…）`. |

**Order matters.** Picard runs scripts top to bottom, and Various Artists must run before Voice Actor, which only cleans an `albumartist` that is down to a single credit. Keep them in file-number order.

### Installing a script

Options → Scripting → **Import**, then pick the `.ptsp` file. Picard adds it **disabled**, with ` (imported)` appended to the name: rename it, tick it to enable it, move it into place, and delete the old version if you're replacing one.

## Tests

The tests run every script, in file order, through Picard's real script parser on sample tracks. They need `git` and [`uv`](https://docs.astral.sh/uv/):

```sh
python tools/run_tests.py
```

The first run downloads Picard's source (`release-3.0.4`) into `.picard/` and builds a Python 3.12 environment in `.venv/`; both are git-ignored. Extra arguments go to pytest, e.g. `python tools/run_tests.py -k voice`.

To check a different Picard version, change `PICARD_REF` in [`tools/run_tests.py`](tools/run_tests.py).

## Licence

[GPL-3.0-or-later](LICENSE).

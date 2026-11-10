---
name: picard-script
description: Use when adding, changing or debugging a Picard tagger script (tagger-scripts/*.ptsp) in this repo, including its regexes, tests and README row. Covers Picard's escaping rules, where whitespace leaks into values, run order, the WRITTEN set and the test pattern.
---

# Picard tagger scripts

The function reference is the Picard release under test, `PICARD_REF` in `tools/run_tests.py`: `.picard/src/picard/script/functions.py`. The online docs may describe a different version. Run `python tools/run_tests.py` once if `.picard/` is missing.

## File format

YAML with `title`, `script_language_version: '1.1'` and `script: |`. YAML does no escaping inside a `|` block, so the indented text is passed to Picard exactly as written.

`title` is the name Picard shows, and `make install-scripts` matches existing scripts by it. A rename is visible to the user: update `test_scripts_import_with_titles_in_run_order`.

## Escaping

Picard's parser accepts only `\n \t \uXXXX \$ \% \( \) \, \\` (`parse_escape_sequence` in `.picard/src/picard/script/parser.py`). Any other backslash is a syntax error. In an argument, a bare `(` is a syntax error, `,` starts the next argument, and `)` closes the function.

What a `$rreplace` / `$rsearch` regex needs, and how to write it:

| Regex needs | Write |
| --- | --- |
| `\s` `\d` `\w` `\b` | `\\s` `\\d` `\\w` `\\b` |
| a group `(...)` | `\(...\)` |
| a literal `(` or `)` | `[\(]` `[\)]` (the form `04` and `05` use) |
| end anchor `$` | `\$` |
| a literal `,` | `\,` |
| backreference `\1` in the replacement | `\\1` |

## Whitespace

Whitespace inside an argument is part of its value: a `$set(a,` whose value `b` starts on the next line, indented, sets `a` to `"\n  b"`. Line breaks and indentation are only safe where the text is discarded, which is between statements and inside an `$if` / `$foreach` branch made only of `$set` / `$unset` calls. Keep each `$set` value on one line.

## Conventions

- Read MusicBrainz's value with `$get_new(tag)`. When a file is matched, `%tag%` falls back to the file's own old tags.
- Temporaries are `_`-prefixed. Unset each one at the end. A matched file can carry a hidden variable with the same name, so also unset any you read before setting (see `04`'s first line and `test_hidden_variables_in_file_tags_ignored`). `test_scripts_change_nothing_else` fails on leftovers.
- Picard runs the scripts top to bottom, and the install keeps them in filename order. A script that reads another's output must sort after it, as `05` reads `04`'s album artist.

## Making a change

1. Edit or add the `.ptsp`. For a new script, pick the number from its place in the run order.
2. Add `CASES` rows to `tests/test_tagger_scripts.py` as `(id, input tags, expected tags)`, using `artists()` for album-artist credits. Cover the case the change is for and the neighbouring cases it must not break.
3. If the script writes a tag no other script writes, add it to `WRITTEN`.
4. Update the script's row in the README table. That table is where the behaviour is documented: one sentence, with an input → output example.
5. Run `python tools/run_tests.py -k <word>`, with one word from the case id: a whole id, with its spaces and `->`, silently selects no tests. Then run the full suite.

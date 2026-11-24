"""PostToolUse hook: parse an edited .ptsp with Picard's script parser and report errors to Claude.

Runs on the system Python like tools/, then re-runs itself with --check in the test venv that
tools/run_tests.py builds, where Picard is importable, from the edited file's repo when
_hook_common.trusted() allows it. When that venv is missing or the check itself breaks, it tells
Claude the file went unchecked rather than blaming the script. Edits made through a shell command
are not seen.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import _hook_common as common

PARSE_ERROR = (
    3  # the child's exit code for a script error; anything else is the checker failing
)


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def venv_python(root):
    venv = root / ".picard" / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def edited_path():
    """The edited file from the hook JSON on stdin, or None."""
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        file_path = data["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return None
    return Path(file_path).resolve() if isinstance(file_path, str) else None


def note(text):
    """Tell Claude something without blocking (exit 0 with additionalContext)."""
    output = {"hookEventName": "PostToolUse", "additionalContext": text}
    print(json.dumps({"hookSpecificOutput": output}))
    return 0


def script_start(text):
    """File line of the `script:` key, or None."""
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"script:\s*[|>]", line):
            return number
    return None


def check(path, picard_src):
    """Parse one .ptsp in the test venv; print the error and return PARSE_ERROR if it fails."""
    sys.path.insert(0, picard_src)
    import picard.extension_points
    import yaml
    from picard.script import ScriptError, ScriptParser
    from picard.script.serializer import ScriptSerializerError, TaggingScriptInfo

    # The script-function ExtensionPoint asks its get_config() which plugins are on; None means
    # all. setattr, because ty rejects assigning a lambda to a typed function.
    setattr(picard.extension_points, "get_config", lambda: None)  # noqa: B010
    try:
        text = Path(path).read_text(encoding="utf-8")
        script = TaggingScriptInfo.create_from_yaml(text)["script"]
        ScriptParser().parse(script)
    except UnicodeDecodeError as error:
        print(f"{path}: not UTF-8: {error}", file=sys.stderr)
        return PARSE_ERROR
    except (ScriptError, ScriptSerializerError, yaml.YAMLError) as error:
        # Picard counts lines from the script; YAML errors already give file lines.
        start = script_start(text) if isinstance(error, ScriptError) else None
        where = f" (script line 1 is file line {start + 1})" if start else ""
        print(f"{path}: {type(error).__name__}: {error}{where}", file=sys.stderr)
        return PARSE_ERROR
    return 0


def main():
    path = edited_path()
    if path is None or path.suffix.lower() != ".ptsp" or not path.is_file():
        return 0
    root = repo_root(path)
    if root is None:
        return 0
    if not common.trusted(root):
        return note(
            f"{path.name} was not parse-checked: "
            f"{common.untrusted_reason(root)}, so its .picard/venv was not run."
        )
    python, picard_src = venv_python(root), root / ".picard" / "src"
    if not (python.is_file() and picard_src.is_dir()):
        return note(
            f"{path.name} was not parse-checked: "
            "run `python tools/run_tests.py` once to build .picard/."
        )
    try:
        # -I keeps cwd and PYTHON* out of the child's imports; -X utf8 matches the decoding here.
        result = common.run(
            [
                str(python),
                "-I",
                "-X",
                "utf8",
                __file__,
                "--check",
                str(path),
                str(picard_src),
            ],
            encoding="utf-8",
            errors="backslashreplace",
            timeout=25,
        )
    except subprocess.TimeoutExpired:
        return note(f"{path.name} was not parse-checked: the check timed out.")
    except OSError as error:
        return note(
            f"{path.name} was not parse-checked: .picard/venv's python did not start "
            f"({error}). Rebuild .picard/ with `python tools/run_tests.py`."
        )
    if result.returncode == PARSE_ERROR:
        sys.stderr.write(result.stderr[-2000:])
        return 2  # PostToolUse: stderr goes back to Claude
    if result.returncode:
        last = (result.stderr.strip().splitlines() or ["no output"])[-1]
        return note(
            f"{path.name} was not parse-checked: the checker failed ({last}). "
            "Rebuild .picard/ with `python tools/run_tests.py`."
        )
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        sys.exit(check(*sys.argv[2:4]))
    sys.exit(main())

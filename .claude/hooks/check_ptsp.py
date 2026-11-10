"""PostToolUse hook: parse an edited .ptsp with Picard's script parser and report errors to Claude.

Runs on the system Python like tools/, then re-runs itself with --check in the test venv that
tools/run_tests.py builds, where Picard is importable. Does nothing until that venv exists.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def repo_root(path):
    return next(
        (d for d in path.parents if (d / "tools" / "run_tests.py").is_file()), None
    )


def venv_python(root):
    venv = root / ".picard" / "venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def check(path, picard_src):
    """Parse one .ptsp; print the error and return 1 if it doesn't."""
    sys.path.insert(0, picard_src)
    import picard.extension_points
    import yaml
    from picard.script import ScriptError, ScriptParser
    from picard.script.serializer import ScriptSerializerError, TaggingScriptInfo

    # The script functions list asks the config which plugins are on; None means all of them.
    setattr(picard.extension_points, "get_config", lambda: None)  # noqa: B010
    text = Path(path).read_text(encoding="utf-8")
    try:
        script = TaggingScriptInfo.create_from_yaml(text)["script"]
        ScriptParser().parse(script)
    except (ScriptError, ScriptSerializerError, yaml.YAMLError) as error:
        start = next(
            (
                i
                for i, line in enumerate(text.splitlines(), 1)
                if re.match(r"script:\s*[|>]", line)
            ),
            None,
        )
        where = f" (script line 1 is file line {start + 1})" if start else ""
        print(f"{path}: {type(error).__name__}: {error}{where}", file=sys.stderr)
        return 1
    return 0


def main():
    try:
        file_path = json.load(sys.stdin)["tool_input"]["file_path"]
    except (ValueError, KeyError, TypeError):
        return 0
    path = Path(file_path).resolve()
    if path.suffix != ".ptsp" or not path.is_file():
        return 0
    root = repo_root(path)
    if root is None:
        return 0
    python = venv_python(root)
    if not python.is_file():
        context = f"{path.name} was not parse-checked: run `python tools/run_tests.py` once to build .picard/."
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": context,
                    }
                }
            )
        )
        return 0
    result = subprocess.run(
        [
            str(python),
            "-I",
            __file__,
            "--check",
            str(path),
            str(root / ".picard" / "src"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        check=False,
    )
    if result.returncode:
        sys.stderr.write(result.stderr[-2000:])
        return 2  # PostToolUse: stderr goes back to Claude
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        sys.exit(check(*sys.argv[2:4]))
    sys.exit(main())

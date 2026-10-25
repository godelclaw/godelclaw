"""Qualification-only subprocess transcript capture; never imported at runtime."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

_run = subprocess.run
_directory = Path(os.environ["CONTEXT_BUNDLE_EVIDENCE"])


def _text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _save(command, result):
    with tempfile.NamedTemporaryFile("w", dir=_directory, prefix="subprocess-", suffix=".json",
                                     encoding="utf-8", delete=False) as stream:
        json.dump({"command": command, "returncode": result.returncode,
                   "stdout": _text(result.stdout), "stderr": _text(result.stderr)},
                  stream, ensure_ascii=False, default=str)


def recording_run(*args, **kwargs):
    command = args[0] if args else kwargs["args"]
    try:
        result = _run(*args, **kwargs)
    except subprocess.CalledProcessError as error:
        _save(command, error)
        raise
    _save(command, result)
    return result


subprocess.run = recording_run

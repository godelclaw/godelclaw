"""Direct file writes for the agent's write-file, edit-file and append-file.

Agent edits keep the previous version in a backup directory. State replacement
uses the same atomic writer without accumulating backups.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import time


def _backup_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    instance = os.environ.get("METTACLAW_INSTANCE", Path.cwd().name)
    return Path(state) / instance / "file-backups"


def _resolve(path) -> Path:
    target = Path(str(path)).expanduser()
    return target if target.is_absolute() else Path.cwd() / target


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup(target: Path, previous: bytes) -> Path:
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    name = "%s-%s-%s" % (time.strftime("%Y%m%dT%H%M%S"), _digest(previous)[:12], target.name)
    fd, backup = tempfile.mkstemp(prefix=name + "-", dir=directory)
    with os.fdopen(fd, "wb") as handle:
        handle.write(previous)
        handle.flush()
        os.fsync(handle.fileno())
    return Path(backup)


def _replace(target: Path, data: bytes) -> None:
    mode = target.stat().st_mode & 0o7777 if target.exists() else None
    fd, temporary = tempfile.mkstemp(prefix="." + target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    try:
        directory = os.open(str(target.parent), os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


def _write_result(target: Path, data: bytes, keep_previous=True):
    previous = target.read_bytes() if keep_previous and target.is_file() else None
    backup = _backup(target, previous) if previous is not None else None
    _replace(target, data)
    return ["written", str(target), len(data), _digest(data)[:16], str(backup) if backup else ""]


def update_result(path, operation, first, second="", backup_directory=None):
    target = _resolve(path)
    try:
        if operation in ("write", "replace"):
            data = str(first).encode("utf-8")
            if operation == "replace":
                target.parent.mkdir(parents=True, exist_ok=True)
        elif operation == "append":
            data = target.read_bytes() + (str(first) + "\n").encode("utf-8")
        elif operation == "edit":
            if not first:
                return ["error", "old-string is empty"]
            text = target.read_text(encoding="utf-8")
            count = text.count(str(first))
            if count != 1:
                return ["count", count, str(target)]
            data = text.replace(str(first), str(second), 1).encode("utf-8")
        else:
            return ["error", "unknown file operation"]
        return _write_result(target, data, keep_previous=operation != "replace")
    except (OSError, UnicodeError) as error:
        return ["error", str(error)]


def _present(operation, result):
    if result[0] == "written":
        _, path, size, digest, backup = result
        return f"WROTE {path} op={operation} bytes={size} sha256={digest}" + (f" previous={backup}" if backup else "")
    if result[0] == "count":
        _, count, path = result
        return f"edit failed: old-string occurs {count} times in {path}; it must occur exactly once"
    return f"{operation} failed: {result[1]}"


def write_file(path, content):
    return _present("write", update_result(path, "write", content))


def edit_file(path, old, new):
    return _present("edit", update_result(path, "edit", old, new))


def append_file(path, line):
    return _present("append", update_result(path, "append", line))

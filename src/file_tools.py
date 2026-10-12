"""Direct file writes for the agent's write-file, edit-file and append-file.

The agent may change its own files. Each write replaces the file atomically
and keeps the previous version in a backup directory, so any change can be
inspected and undone. Nothing waits for approval.
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
    backup = directory / name
    with open(backup, "wb") as handle:
        handle.write(previous)
    return backup


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


def _write(target: Path, data: bytes, operation: str) -> str:
    previous = target.read_bytes() if target.is_file() else None
    backup = _backup(target, previous) if previous is not None else None
    _replace(target, data)
    receipt = "WROTE %s op=%s bytes=%d sha256=%s" % (
        target, operation, len(data), _digest(data)[:16])
    if backup is not None:
        receipt += " previous=%s" % backup
    return receipt


def write_file(path, content) -> str:
    target = _resolve(path)
    try:
        return _write(target, str(content).encode("utf-8"), "write")
    except (OSError, UnicodeError) as exc:
        return "write failed: %s: %s" % (target, exc)


def edit_file(path, old, new) -> str:
    target = _resolve(path)
    old, new = str(old), str(new)
    if not old:
        return "edit failed: old-string is empty"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return "edit failed: %s: %s" % (target, exc)
    count = text.count(old)
    if count != 1:
        return "edit failed: old-string occurs %d times in %s; it must occur exactly once" % (count, target)
    try:
        return _write(target, text.replace(old, new, 1).encode("utf-8"), "edit")
    except (OSError, UnicodeError) as exc:
        return "edit failed: %s: %s" % (target, exc)


def append_file(path, line) -> str:
    target = _resolve(path)
    try:
        current = target.read_bytes()
    except OSError as exc:
        return "append failed: %s: %s" % (target, exc)
    try:
        return _write(target, current + (str(line) + "\n").encode("utf-8"), "append")
    except (OSError, UnicodeError) as exc:
        return "append failed: %s: %s" % (target, exc)

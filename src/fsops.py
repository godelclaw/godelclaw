"""Filesystem awareness for the agent loop, adapted from how coding
harnesses handle files: reads are line-numbered, edits are exact-string
replacements anchored to seen content with uniqueness enforced, and every
result string is itself the verification — success and failure are both
explicit, so no blind writes and no re-reading to check what landed."""

import os
import re

MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", "chroma_db", ".venv"}


def read_file(path, max_bytes=MAX_FILE_BYTES):
    """Whole file as a string, or a reason. Never a silent empty result:
    'read failed: no such file: X (cwd Y)' is actionable; '' is a mystery."""
    path, max_bytes = str(path), max(0, int(max_bytes))
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1) if max_bytes else f.read()
        if max_bytes and len(data) > max_bytes:
            return ("read failed: %s exceeds %d bytes — use read-lines for a "
                    "window, or supply a larger max_bytes (0 means unlimited)" % (path, max_bytes))
        if not data:
            # An empty file and a failed read must never look alike.
            return "READ_OK_EMPTY_FILE: %s exists and holds zero bytes" % path
        return data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return "read failed: no such file: %s (cwd %s)" % (path, os.getcwd())
    except IsADirectoryError:
        return "read failed: %s is a directory — use (ls-tree %r 1)" % (path, path)
    except PermissionError:
        return "read failed: permission denied: %s" % path
    except OSError as exc:
        return "read failed (%s): %s" % (type(exc).__name__, exc)


def read_lines(path, start=1, count=2000):
    """Stream the requested numbered window, preserving complete lines."""
    import itertools
    path, start, count = str(path), max(1, int(start)), max(1, int(count))
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            window = list(itertools.islice(fh, start - 1, start - 1 + count))
            more = bool(fh.readline())
        if not window:
            return f"READ_OK_EMPTY_FILE: {path} exists and holds zero bytes" if start == 1 and os.path.getsize(path) == 0 else f"{path}: no lines at or after {start}"
        body = "\n".join(f"{start+i:5d}\t{line.rstrip(chr(10)).rstrip(chr(13))}"
                         for i, line in enumerate(window))
        end = start + len(window) - 1
        return f"{path}: showing {start}-{end}" + (f"; next start {end+1}" if more else "; end of file") + "\n" + body
    except OSError as exc:
        return f"read-lines failed: {exc}"


def ls_tree(root, depth=2, limit=1000, offset=0):
    """Directory entries with an explicit continuation, no fixed depth cap."""
    root, depth, limit, offset = str(root), max(1, int(depth)), max(1, int(limit)), max(0, int(offset))
    if not os.path.isdir(root):
        return f"ls-tree failed: not a directory: {root}"
    def walk(directory, level):
        try:
            entries = sorted(os.scandir(directory), key=lambda e: (e.is_file(), e.name))
        except OSError as exc:
            yield "  " * level + f"[{exc}]"
            return
        for entry in entries:
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_dir(follow_symlinks=False):
                yield "  " * level + entry.name + "/"
                if level + 1 < depth:
                    yield from walk(entry.path, level + 1)
            else:
                yield "  " * level + entry.name
    import itertools
    entries = list(itertools.islice(walk(root, 0), offset, offset + limit + 1))
    more = len(entries) > limit
    return root + "\n" + "\n".join(entries[:limit]) + (f"\n[more entries; next offset {offset+limit}]" if more else "")


def grep_files(pattern, root, limit=200, offset=0):
    """Search complete text lines; caller chooses page length and offset."""
    root, limit, offset = str(root), max(1, int(limit)), max(0, int(offset))
    try:
        rx = re.compile(str(pattern))
    except re.error:
        rx = re.compile(re.escape(str(pattern)))
    hits, matched, scanned, errors = [], 0, 0, []
    def paths():
        if os.path.isfile(root):
            yield root
            return
        for directory, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(names):
                yield os.path.join(directory, name)
    files = paths()
    for path in files:
        try:
            with open(path, "rb") as fh:
                if b"\0" in fh.read(1024):
                    continue
            scanned += 1
            with open(path, encoding="utf-8", errors="replace") as fh:
                for number, line in enumerate(fh, 1):
                    if rx.search(line):
                        matched += 1
                        if matched > offset:
                            hits.append(f"{path}:{number}: {line.rstrip()}")
                        if len(hits) > limit:
                            return "\n".join(hits[:limit]) + f"\n[more matches; next offset {offset+limit}]"
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return ("\n".join(hits) if hits else f"no matches in this page for {pattern!r} under {root} ({scanned} files scanned)") + (
        "\n[unreadable files]\n" + "\n".join(errors) if errors else "")

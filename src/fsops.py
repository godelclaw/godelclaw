"""Filesystem awareness for the agent loop, adapted from how coding
harnesses handle files: reads are line-numbered, edits are exact-string
replacements anchored to seen content with uniqueness enforced, and every
result string is itself the verification — success and failure are both
explicit, so no blind writes and no re-reading to check what landed."""

import os
import re

MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", "chroma_db", ".venv"}


def read_text_result(path, max_bytes=MAX_FILE_BYTES):
    """SWI-PeTTa's I/O backend; presentation belongs to file_io.metta."""
    path, max_bytes = str(path), max(0, int(max_bytes))
    try:
        with open(path, "rb") as file:
            data = file.read(max_bytes + 1) if max_bytes else file.read()
        if max_bytes and len(data) > max_bytes:
            return ["limit", max_bytes]
        return ["ok", data.decode("utf-8", errors="replace")]
    except FileNotFoundError:
        return ["missing", os.getcwd()]
    except IsADirectoryError:
        return ["directory", ""]
    except PermissionError:
        return ["denied", ""]
    except OSError as error:
        return ["error", type(error).__name__, str(error)]


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


def line_window_result(path, start=1, count=2000):
    """Stream the requested numbered window, preserving complete lines."""
    import itertools
    path, start, count = str(path), max(1, int(start)), max(1, int(count))
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            window = list(itertools.islice(fh, start - 1, start - 1 + count))
            more = bool(fh.readline())
        body = "\n".join(f"{start+i:5d}\t{line.rstrip(chr(10)).rstrip(chr(13))}"
                         for i, line in enumerate(window))
        return ["window", start, len(window), more, body, os.path.getsize(path) == 0]
    except OSError as exc:
        return ["error", str(exc)]


def read_lines(path, start=1, count=2000):
    result = line_window_result(path, start, count)
    if result[0] == "error":
        return "read-lines failed: " + result[1]
    _, start, size, more, body, empty = result
    if not size:
        return f"READ_OK_EMPTY_FILE: {path} exists and holds zero bytes" if start == 1 and empty else f"{path}: no lines at or after {start}"
    end = start + size - 1
    return f"{path}: showing {start}-{end}" + (f"; next start {end+1}" if more else "; end of file") + "\n" + body


def tree_page_result(root, depth=2, limit=1000, offset=0, skips=SKIP_DIRS):
    """Directory entries with an explicit continuation, no fixed depth cap."""
    root, depth, limit, offset = str(root), max(1, int(depth)), max(1, int(limit)), max(0, int(offset))
    if not os.path.isdir(root):
        return ["not-directory", root]
    def walk(directory, level):
        try:
            entries = sorted(os.scandir(directory), key=lambda e: (e.is_file(), e.name))
        except OSError as exc:
            yield "  " * level + f"[{exc}]"
            return
        for entry in entries:
            if entry.name in skips:
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
    return ["page", "\n".join(entries[:limit]), more, offset+limit]


def ls_tree(root, depth=2, limit=1000, offset=0):
    result = tree_page_result(root, depth, limit, offset)
    if result[0] == "not-directory":
        return f"ls-tree failed: not a directory: {root}"
    _, body, more, next_offset = result
    return str(root) + "\n" + body + (f"\n[more entries; next offset {next_offset}]" if more else "")


def grep_page_result(pattern, root, limit=200, offset=0, skips=SKIP_DIRS):
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
            dirs[:] = sorted(d for d in dirs if d not in skips)
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
                            return ["matches", "\n".join(hits[:limit]), True, offset+limit, scanned, "\n".join(errors)]
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return ["matches", "\n".join(hits), False, offset+limit, scanned, "\n".join(errors)]


def grep_files(pattern, root, limit=200, offset=0):
    _, body, more, next_offset, scanned, errors = grep_page_result(pattern, root, limit, offset)
    return (body if body else f"no matches in this page for {pattern!r} under {root} ({scanned} files scanned)") + (
        f"\n[more matches; next offset {next_offset}]" if more else "") + (
        "\n[unreadable files]\n" + errors if errors else "")

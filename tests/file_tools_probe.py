"""Assertions observe actual evaluator outputs and resulting files."""
import hashlib
from pathlib import Path
import os
import re
import fsops
import file_tools

def forbid_python():
    def forbidden(*args, **kwargs):
        raise AssertionError("Python filesystem backend used by CeTTa")
    for name in ("read_file", "read_text_result", "read_lines", "line_window_result", "ls_tree", "tree_page_result", "grep_files", "grep_page_result"):
        setattr(fsops, name, forbidden)
    for name in ("write_file", "edit_file", "append_file", "update_result", "_write_result"):
        setattr(file_tools, name, forbidden)
    return True

def check(actual, expected):
    if str(expected) in ("True", "False") and str(actual).lower() == str(expected).lower():
        return True
    if str(actual) != str(expected):
        print(f"result differs: {str(actual)[:500]!r} != {str(expected)[:500]!r}", flush=True)
        raise AssertionError(f"result differs: {str(actual)[:500]!r} != {str(expected)[:500]!r}")
    return True

def written(receipt, path, expected_hex, previous_hex, mode):
    data = bytes.fromhex(expected_hex)
    target = Path(path)
    assert target.read_bytes() == data
    assert target.stat().st_mode & 0o7777 == int(mode)
    digest = hashlib.sha256(data).hexdigest()[:16]
    assert str(receipt).startswith(f"WROTE {path} op=") and f"bytes={len(data)} sha256={digest}" in str(receipt), str(receipt)
    if previous_hex != "none":
        assert " previous=" in str(receipt), str(receipt)
        backup = Path(str(receipt).split(" previous=", 1)[1])
        assert backup.read_bytes() == bytes.fromhex(previous_hex)
        assert backup.is_file()
    else:
        assert " previous=" not in str(receipt)
    assert not list(target.parent.glob("." + target.name + ".*"))
    return True

def failed(receipt, prefix, path, expected_hex):
    assert str(receipt).startswith(str(prefix)), str(receipt)
    assert Path(path).read_bytes() == bytes.fromhex(expected_hex)
    return True

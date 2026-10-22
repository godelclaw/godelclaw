"""Test-only bridge witnesses; never a backend for the native implementation."""
import builtins
import json
import os
import subprocess

_open = builtins.open
_expected = []


def load(path):
    global _expected
    with _open(path, encoding="utf-8") as stream:
        _expected = json.load(stream)
    return 1


def expected(index):
    return _expected[index]


def forbid():
    import helper

    def forbidden(*args, **kwargs):
        raise AssertionError("native action window used Python backend/I/O")

    for name in ("recent_actions", "relevant_files", "_action_blocks",
                 "_commands_of", "_is_noise"):
        setattr(helper, name, forbidden)
    builtins.open = subprocess.run = subprocess.Popen = os.system = forbidden
    return 1

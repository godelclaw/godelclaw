"""Count entries from the native engine into this repository's Python.

Measurement only: the CeTTa census driver imports this into a disposable
scenario; the runtime never loads it. An entry is a Python frame entered
directly from C (no Python caller) whose code lives in this checkout. No
arguments, prompts or values are recorded. Direct C-API callbacks that never
create a frame stay invisible, so a count is a lower bound.
"""
from collections import Counter
import json
from pathlib import Path
import sys
import threading

ROOT = str(Path(__file__).resolve().parents[1]) + "/"
SELF = str(Path(__file__).resolve())
ENTRIES = Counter()
OUTPUT = None


def _trace(frame, event, arg):
    if event != "call" or frame.f_back is not None:
        return
    name = frame.f_code.co_filename
    if not name.startswith(ROOT) or name == SELF:
        return
    module = name[len(ROOT):].removesuffix(".py").replace("/", ".")
    ENTRIES[module + "." + frame.f_code.co_name] += 1


def start(path):
    global OUTPUT
    OUTPUT = str(path)
    sys.setprofile(_trace)
    threading.setprofile(_trace)
    return 1


def finish():
    sys.setprofile(None)
    threading.setprofile(None)
    modules = Counter()
    for name, count in ENTRIES.items():
        modules[name.rsplit(".", 1)[0]] += count
    Path(OUTPUT).write_text(json.dumps({
        "total": sum(ENTRIES.values()),
        "modules": dict(modules.most_common()),
        "functions": dict(ENTRIES.most_common())}, indent=1) + "\n")
    return 1

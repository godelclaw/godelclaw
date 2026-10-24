"""Test-only bridge witnesses; never a backend for the native pins view.

The native view reads the pin file itself. A test uses this module to put
bytes in the file (the guest has no way to write malformed UTF-8 text), to
switch the Python pins reader off, and to record what the real context bundle
produced. After forbid() the Python reader raises and counts the attempt, so
a fallback to it cannot pass for the native implementation.
"""
import builtins
import json
import os
import subprocess

_open = builtins.open
python_reads = 0
_bundle_path = None


def write(path, hexbytes):
    """Replace the file with these bytes; "missing" removes it."""
    if hexbytes == "missing":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    else:
        with _open(path, "wb") as stream:
            stream.write(bytes.fromhex(hexbytes))
    return 1


def forbid():
    """The Python pins readers raise; so do other readers of files/processes."""
    import pins

    def forbidden(*args, **kwargs):
        global python_reads
        python_reads += 1
        raise AssertionError("Python pins reader used")

    for name in ("context_view", "view", "_read"):
        setattr(pins, name, forbidden)

    def no_open(*args, **kwargs):
        raise AssertionError("Python file I/O used")

    builtins.open = no_open
    subprocess.run = subprocess.Popen = os.system = no_open
    return 1


def pins_loaders(conversation=None, views=None, loaders=None):
    """context_sources' loaders for the pins source only."""
    import context_sources

    loaders = loaders or context_sources._loaders
    return [(spec, loader) for spec, loader in loaders(conversation, views)
            if spec.source_id == "pins"]


def record_bundle(path):
    """Restrict the real caller to pins; record_caller observes either route."""
    import context_sources
    global _bundle_path

    original = context_sources._loaders
    context_sources._loaders = lambda conversation=None, views=None: pins_loaders(
        conversation, views, original)
    context_sources._PROJECTOR = context_sources.Projector()
    _bundle_path = path
    return 1


def record_caller(text):
    with _open(_bundle_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"text": text, "python_reads": python_reads},
                                ensure_ascii=False) + "\n")
    return 1


def note(path, text):
    """Record a guest value, for guests that have no native file library."""
    with _open(path, "w", encoding="utf-8") as stream:
        stream.write(str(text))
    return 1


def restrict_to_pins():
    """Run the real bundle with only the pins source (for timing)."""
    import context_sources

    original = context_sources._loaders
    context_sources._loaders = lambda conversation=None, views=None: pins_loaders(
        conversation, views, original)
    return 1


def reset_state():
    """A new process's marker and projector state (the oracle gets the same)."""
    import context_sources
    import helper

    helper._change_marks.clear()
    context_sources._PROJECTOR = context_sources.Projector()
    return 1


def spy():
    """Count (without blocking) the calls of pins' own Python context reader."""
    import pins

    original = pins.context_view

    def counted(*args, **kwargs):
        global python_reads
        python_reads += 1
        return original(*args, **kwargs)

    pins.context_view = counted
    return 1


def log(path, label, text):
    """Append one labelled guest value as a JSON line."""
    with _open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"label": str(label), "text": str(text)}, ensure_ascii=False) + "\n")
    return 1

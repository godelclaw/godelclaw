"""Count calls entering Python from native code: the migration's census hook.

The census driver (scripts/census_python_entries_cetta.metta) installs this
file as sitecustomize in a disposable environment, never in a release. Every
CeTTa process of a census scenario, including spawned workers, then appends
one line per entry to <PYTHON_ENTRY_CENSUS_DIR>/<pid>.entries: a call whose
frame has no Python caller came from native code. Lines name the source file
and function only, never arguments. Calls that CPython serves without a Python
frame are not observed, so the count is a lower bound. A coding session runs
from a frozen copy of the runtime (memory/coding-runtimes/<digest>/); its
files count as project files under their path inside that copy.

The census counts program work only. Py_FinalizeEx enters threading._shutdown
before any atexit handler runs; from that entry on, a process records nothing,
so Python's own shutdown and the runtime's exit handlers are not counted.
"""
import os
import sys
import threading


def _install():
    directory = os.environ.get("PYTHON_ENTRY_CENSUS_DIR", "")
    root = os.environ.get("PYTHON_ENTRY_CENSUS_ROOT", "")
    if not directory or not root:
        return
    roots = tuple({os.path.join(root, ""), os.path.join(os.path.realpath(root), "")})
    path = os.path.join(directory, "%d.entries" % os.getpid())
    # O_APPEND keeps each short line whole across threads.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    # Without a code object to recognise, counting never stops: an exit-time
    # entry is then counted, never a program entry missed.
    shutdown = getattr(getattr(threading, "_shutdown", None), "__code__", None)
    finished = False

    def trace(frame, event, arg):
        nonlocal finished
        if finished or event != "call" or frame.f_back is not None:
            return
        code = frame.f_code
        if code is shutdown:
            finished = True
            return
        filename = code.co_filename
        if filename.startswith("<"):
            return  # py-eval text and frozen import machinery
        kind, name = "other", filename
        for prefix in roots:
            if filename.startswith(prefix):
                kind, name = "project", filename[len(prefix):]
                break
        else:
            frozen = filename.find("/coding-runtimes/")
            if frozen >= 0:
                inside = filename[frozen + len("/coding-runtimes/"):].split("/", 1)
                if len(inside) == 2:
                    kind, name = "project", inside[1]
        os.write(descriptor, ("%s\t%s\t%s\n" % (kind, name, code.co_name)).encode("utf-8", "replace"))

    sys.setprofile(trace)
    threading.setprofile(trace)


_install()

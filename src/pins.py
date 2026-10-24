"""Persistent working-memory pins.

(pin ...) was catalog-only for its whole life: the unbound expression echoed
itself into the results feedback, which LOOKED like working memory but lived
only until it scrolled out — and died entirely on restart. The agent asked
for exactly this: "if pins survived restart, I'd wake up already knowing
what I was doing."

Pins live in a small file, newest last, hard-capped so the per-turn context
cost stays bounded (~1.5 KB worst case). The PINNED block in the context is
built from this file, so a restart changes nothing the agent can see.
"""

import os
import threading
import time

_MAX_PINS = 12
_MAX_LEN = 300
_CONTEXT_PINS = 12
_CONTEXT_LINE = 400
_LOCK = threading.RLock()


def _path():
    return os.environ.get(
        "METTACLAW_PINS_PATH",
        os.path.join(os.path.dirname(os.environ.get(
            "METTACLAW_HISTORY_PATH", "memory/history.metta")), "pins.txt"))


def _read():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f if l.strip()]
    except OSError:
        return []


def _write(lines):
    path = _path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print("[pins] write failed:", exc)
        return False


def pin(text):
    """Add one pin; oldest falls off past the cap. Returns what happened."""
    text = " ".join(str(text).split())[:_MAX_LEN]
    if not text:
        return "pin failed: empty text"
    with _LOCK:
        lines = _read()
        lines = [l for l in lines if l.split("] ", 1)[-1] != text]
        lines.append("[%s] %s" % (time.strftime("%m-%d %H:%M"), text))
        dropped = len(lines) - _MAX_PINS
        lines = lines[-_MAX_PINS:]
        ok = _write(lines)
    if not ok:
        return "pin failed: could not persist"
    note = " (oldest %d dropped)" % dropped if dropped > 0 else ""
    return "pinned %d/%d%s — survives restarts" % (len(lines), _MAX_PINS, note)


def unpin(fragment):
    """Remove every pin containing the fragment. Returns what happened."""
    frag = str(fragment).strip()
    if not frag:
        return "unpin failed: give a fragment of the pin to remove"
    with _LOCK:
        lines = _read()
        keep = [l for l in lines if frag.lower() not in l.lower()]
        removed = len(lines) - len(keep)
        if removed == 0:
            return "unpin: no pin contains %r (%d pins held)" % (frag, len(lines))
        if not _write(keep):
            return "unpin failed: could not persist"
        return "unpinned %d, %d remain" % (removed, len(keep))


def view():
    """The PINNED block for the context; '' when there are no pins."""
    with _LOCK:
        lines = _read()
    if not lines:
        return "(no pins)"
    import helper
    text = " | ".join(lines)
    return helper.change_mark("pins", text) + " " + text


NO_PINS = "(no pins)"


def mark_context_body(body):
    """The one place the context view's change marker is applied.

    The body is the marker-free view text. The marker state lives in
    helper._change_marks under "pins-context" for every producer of that body
    (this module's own reader and the native one), so the streak is one
    sequence whichever of them produced a turn's body. No pins: no marker,
    and the streak is left as it stood.
    """
    if body == NO_PINS:
        return body
    import helper
    return helper.change_mark("pins-context", body) + " " + body


def context_view():
    """Bounded newest-first working view; the ``pins`` tool retains full access."""
    with _LOCK:
        lines = _read()
    if not lines:
        return NO_PINS
    visible = [line if len(line) <= _CONTEXT_LINE
               else line[:_CONTEXT_LINE - 1] + "…"
               for line in lines[-_CONTEXT_PINS:]]
    header = "PINNED newest %d of %d; (pins) reads the full retained list" % (
        len(visible), len(lines))
    return mark_context_body(header + "\n" + " | ".join(visible))

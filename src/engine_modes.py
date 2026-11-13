"""The CeTTa process: its command and the recycle request.

godelclaw is PeTTa-dialect MeTTa on CeTTa: there is one engine and nothing to select.
"""

import os
import time


def engine_command(name="cetta"):
    """The configured argv prefix of the CeTTa executable, without executing it."""
    if str(name or "").strip().lower() != "cetta":
        return ()
    root = os.environ.get(
        "CETTA_ROOT", os.path.join(os.path.expanduser("~"), "repos", "CeTTa-runtime"))
    return (os.environ.get("CETTA_BIN", os.path.join(root, "cetta")),)


def request_recycle(reason="requested"):
    path = os.environ.get("METTACLAW_RECYCLE_REQUEST_PATH", "")
    if not path:
        return False
    parent = os.path.dirname(path)
    temporary = "%s.tmp.%d" % (path, os.getpid())
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as stream:
            stream.write("requested_at=%.6f reason=%s\n" % (time.time(), reason))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return True
    except OSError as exc:
        print("[engine-mode] recycle request failed:", type(exc).__name__)
        return False


def recycle_requested():
    """A recycle flag, or a loop mode selected for the next process, asks for a recycle."""
    path = os.environ.get("METTACLAW_RECYCLE_REQUEST_PATH", "")
    if path and os.path.isfile(path):
        return True
    if __package__:
        from . import loop_modes
    else:
        import loop_modes
    return bool(os.environ.get("METTACLAW_ACTIVE_LOOP_MODE") and
                loop_modes.current_mode() != loop_modes.process_mode())

"""Persistent loop-policy modes shared by the MeTTa loop and operator UI."""

import json
import os
import threading
import time


MODES = {
    "godelclaw": {
        "description": "event-armed life, renewed by Iter's transformations after each idle boundary",
        "autonomous": True,
        "selectable": True,
    },
    "coding": {
        "description": "godelclaw with high reasoning effort",
        "autonomous": True,
        "selectable": True,
    },
    "iter": {
        "description": "qualified MeTTa port of the pinned Iter loop with native tool calls",
        "autonomous": True,
        "selectable": True,
    },
    "omega": {
        "description": "qualified current Omega native-tool loop with NAL/PLN",
        "autonomous": True,
        "selectable": True,
    },
}

# Earlier names keep saved selections working. Iter is now its own loop.
ALIASES = {
    "agent": "godelclaw",
    "default": "godelclaw",
    "generic": "godelclaw",
    "claw23": "godelclaw",
    "iter-coding": "coding",
}

_lock = threading.RLock()


def _path():
    return os.environ.get("METTACLAW_LOOP_MODE_PATH", "memory/loop_mode.json")


def canonical(name):
    name = str(name or "").strip().lower()
    return ALIASES.get(name, name)


_canonical = canonical


def autonomous(name):
    """Whether the mode renews itself without input, as its policy does."""
    return bool(MODES.get(canonical(name), {}).get("autonomous", False))


def selectable(name):
    """Whether the advertised mode has passed its production qualification."""
    return bool(MODES.get(canonical(name), {}).get("selectable", False))


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("mode state is not an object")
    except (OSError, TypeError, ValueError):
        data = {}
    mode = _canonical(data.get("mode", "godelclaw"))
    if mode not in MODES or not selectable(mode):
        mode = "godelclaw"
    return {"mode": mode}


def _save(data):
    path = _path()
    parent = os.path.dirname(path)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
        return True
    except OSError as exc:
        print("[loop-mode] save failed:", exc)
        return False


def current_mode():
    with _lock:
        return _load()["mode"]


def process_mode():
    """The running process's policy, independent of a newer selection.

    Standalone modes are separate programs, not presets that the framework
    loop can adopt mid-turn. The launcher captures this value once per boot.
    """
    active = canonical(os.environ.get("METTACLAW_ACTIVE_LOOP_MODE", ""))
    return active if active in MODES and selectable(active) else current_mode()


def _active_path():
    state = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
    if state:
        return os.path.join(os.path.dirname(state), "active-loop.json")
    state_home = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(state_home, os.environ.get("METTACLAW_INSTANCE", "default"), "active-loop.json")


def record_active(mode, pid, state_directory=""):
    path = _active_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp." + str(os.getpid())
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"mode": canonical(mode), "pid": int(pid), "started_at": time.time(),
                   "state_directory": state_directory}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return True


def active_mode():
    try:
        with open(_active_path(), encoding="utf-8") as stream:
            value = json.load(stream)
        os.kill(int(value["pid"]), 0)
        return canonical(value["mode"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def mode_view():
    mode = current_mode()
    active = active_mode()
    if active and active != mode:
        return "active mode: %s; requested %s — recycling" % (active, mode)
    return "%s mode: %s — %s" % ("active" if active else "selected", mode, MODES[mode]["description"])


def modes_view():
    current = active_mode()
    lines = []
    for name, spec in MODES.items():
        marker = "● " if name == current else ("  " if spec["selectable"] else "○ ")
        suffix = spec["description"]
        if not spec["selectable"]:
            suffix += " — unavailable: " + spec["unavailable"]
        lines.append(marker + name + " — " + suffix)
    return "\n".join(lines)


def set_mode(name):
    requested = str(name or "").strip().lower()
    name = _canonical(requested)
    if name not in MODES:
        choices = list(MODES) + list(ALIASES)
        return "mode-set failed: choose one of %s" % ", ".join(choices)
    if not selectable(name):
        return "mode-set failed: '%s' is unavailable: %s" % (name, MODES[name]["unavailable"])
    with _lock:
        data = _load()
        previous = data["mode"]
        data["mode"] = name
        if not _save(data):
            return "mode-set failed: could not persist"
    if previous != name:
        import engine_modes
        if engine_modes.request_recycle():
            flag = os.environ["METTACLAW_RECYCLE_REQUEST_PATH"]
            wake = os.environ.get("METTACLAW_WAKE_REQUEST_PATH", os.path.join(os.path.dirname(flag), "wake.requested"))
            with open(wake, "w", encoding="utf-8") as stream:
                stream.write("mode switch\n")
    alias = " (from alias '%s')" % requested if requested != name else ""
    return "mode set to '%s'%s; persists across restarts" % (name, alias)


def wait_timed_out(wait_result):
    """Normalize the channel adapter's timeout result for the MeTTa policy."""
    return 1 if str(wait_result).startswith("rested ") else 0

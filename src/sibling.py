"""Sibling-claw control: reliable start/stop/status for the other agent.

The two claws are meant to orchestrate each other, but "stop the sibling"
is only reliable if it also speaks the watchdog's pause convention: a
PAUSED-BY-OPERATOR.md marker in the sibling's body directory. Stopping the
service without the marker means the watchdog revives it within 30 minutes;
removing the marker without starting means it stays down until the next
watchdog pass. These helpers always do both halves, in the safe order
(marker before stop; marker removed before start), and report what actually
happened rather than what was attempted.

Which agent is "the sibling" is derived from this body's own directory, so
the same file serves both bodies unchanged.
"""

import os
import subprocess
import time

_CLAWS = {
    "godel": {
        "body": os.path.expanduser("~/pettaclaw-godel"),
        "unit": "pettaclaw-godel.service",
        "label": "Gödel",
    },
    "lila": {
        "body": os.path.expanduser("~/pettaclaw-lila"),
        "unit": "pettaclaw-lila.service",
        "label": "Lila",
    },
}


def _me():
    cwd = os.path.realpath(os.getcwd())
    for name, claw in _CLAWS.items():
        if cwd.startswith(os.path.realpath(claw["body"])):
            return name
    return ""


def _sibling():
    me = _me()
    if me == "godel":
        return "lila"
    if me == "lila":
        return "godel"
    return ""


def _marker(claw):
    return os.path.join(_CLAWS[claw]["body"], "PAUSED-BY-OPERATOR.md")


def _systemctl(*args):
    try:
        r = subprocess.run(["systemctl", "--user", *args],
                           capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "%s: %s" % (type(exc).__name__, exc)


def _active(claw):
    code, out = _systemctl("is-active", _CLAWS[claw]["unit"])
    return out or ("active" if code == 0 else "unknown")


def sibling_status():
    sib = _sibling()
    if not sib:
        return "sibling-status failed: cannot tell which body this is"
    state = _active(sib)
    paused = os.path.isfile(_marker(sib))
    watch = ("watchdog will NOT revive (pause marker present)" if paused
             else "watchdog revives within 30 min if down")
    return "%s: %s | %s" % (_CLAWS[sib]["label"], state, watch)


def sibling_off(reason=""):
    """Pause the sibling: marker FIRST (so no revival race), then stop."""
    sib = _sibling()
    if not sib:
        return "sibling-off failed: cannot tell which body this is"
    if _me() == sib:
        return "sibling-off refused: that would stop yourself — use (rest)"
    try:
        with open(_marker(sib), "w", encoding="utf-8") as f:
            f.write("# PAUSED BY OPERATOR\n\n"
                    "Paused: %s\nPaused by: %s via (sibling-off)\n"
                    "Reason: %s\n\n"
                    "The watchdog sees this file and will not restart the "
                    "agent. To resume: use (sibling-on) from the other claw, "
                    "or delete this file and start the service.\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                       _CLAWS.get(_me(), {}).get("label", "operator"),
                       reason or "(none given)"))
    except OSError as exc:
        return "sibling-off failed writing pause marker: %s" % exc
    code, out = _systemctl("stop", _CLAWS[sib]["unit"])
    state = _active(sib)
    if state == "inactive":
        return ("%s paused: service stopped, pause marker set — the watchdog "
                "will leave them down" % _CLAWS[sib]["label"])
    return ("sibling-off incomplete: marker set but service is %r (%s)"
            % (state, out))


def sibling_on():
    """Resume the sibling: marker removed first, then start, then verify."""
    sib = _sibling()
    if not sib:
        return "sibling-on failed: cannot tell which body this is"
    try:
        if os.path.isfile(_marker(sib)):
            os.remove(_marker(sib))
    except OSError as exc:
        return "sibling-on failed removing pause marker: %s" % exc
    code, out = _systemctl("start", _CLAWS[sib]["unit"])
    time.sleep(3)
    state = _active(sib)
    if state == "active":
        return ("%s resumed: pause marker cleared, service active — they will "
                "boot, read their inbox, and start their loop"
                % _CLAWS[sib]["label"])
    return ("sibling-on incomplete: marker cleared but service is %r (%s) — "
            "the watchdog will retry within 30 min" % (state, out))


def resume_self():
    """Operator start: undo a pause of this body's own service.

    An operator /start outranks a sibling's pause, so it clears this body's
    pause marker and starts its service when it is not active. It reports
    only what it observed afterwards.
    """
    me = _me()
    if not me:
        return "resume: cannot tell which body this is"
    notes = []
    try:
        if os.path.isfile(_marker(me)):
            os.remove(_marker(me))
            notes.append("pause marker cleared")
    except OSError as exc:
        return "resume failed removing pause marker: %s" % exc
    if _active(me) != "active":
        code, out = _systemctl("start", _CLAWS[me]["unit"])
        state = _active(me)
        notes.append("service %s" % state if code == 0 else
                     "service start failed (%s)" % out)
    return "; ".join(notes) if notes else "service already active"

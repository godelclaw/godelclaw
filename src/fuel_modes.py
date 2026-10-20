"""Persistent fuel discipline: what a renewal does to the budget it finds.

Every grant site in the loop used to overwrite the budget with one full
burst, so a burst the agent had not spent was simply lost at the next
renewal and the budget could never exceed `full`. That is one choice among
several, and it encodes a claim: each interval is a fresh, equal breath and
restraint earns nothing.

The disciplines here make that claim selectable. Each is a function of the
budget currently held and the grant the policy offers:

  saturate    max(loops, grant)              — original behaviour, ceiling `grant`
  accumulate  loops + grant                  — unbounded; restraint compounds
  carry       min(CARRY * grant, loops+grant) — accumulate under a ceiling
  decay       loops * 3 // 4 + grant         — recency-weighted, same ceiling
  unlimited   budget is not spent            — `nop`, rest or stop ends work

`carry` and `decay` share the ceiling CARRY * grant and differ only in how
they approach it: carry accumulates at full rate and stops dead, decay
forgets a quarter of what it holds each renewal and converges. The decay
rate is 3/4 precisely so the two agree on the limit — the fixed point of
`x = 3x/4 + grant` is `4 * grant`.

The arithmetic is integer and total on purpose: the bounds are the content
of the choice, and `lean/pettaclaw/FuelPolicy.lean` proves each one against
exactly these definitions.

Human arming is deliberately not routed through here. It stays a plain
`max`: a message means work has arrived, and the communication layer grants
one breath to answer it rather than minting fuel.
"""

import json
import os
import threading


CARRY = 4  # carry ceiling, as a multiple of one full burst

FUELS = {
    "saturate": {
        "description": "one fresh full burst per renewal; unspent budget is "
                       "lost (the loop's original behaviour)",
    },
    "accumulate": {
        "description": "every grant adds; unspent budget compounds without limit",
    },
    "carry": {
        "description": "grants add up to a ceiling of %d full bursts" % CARRY,
    },
    "decay": {
        "description": "three quarters of what is held carries over, then the "
                       "grant; same ceiling as carry, approached gradually",
    },
    "unlimited": {
        "description": "turns do not spend the burst; nop, rest or operator stop ends it",
    },
}

# Decay is the default: a quiet hour should leave something behind, and it
# is the discipline that says so without letting fuel run away — it shares
# carry's ceiling and approaches it gradually. `saturate` remains the
# behaviour the loop had before this module existed.
DEFAULT = "decay"

_lock = threading.RLock()


def _path():
    return os.environ.get("METTACLAW_FUEL_MODE_PATH", "memory/fuel_mode.json")


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("fuel state is not an object")
    except (OSError, TypeError, ValueError):
        data = {}
    name = str(data.get("fuel", DEFAULT)).strip().lower()
    if name not in FUELS:
        name = DEFAULT
    return {"fuel": name}


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
        print("[fuel-mode] save failed:", exc)
        return False


def current_fuel():
    with _lock:
        return _load()["fuel"]


def refuel(loops, grant):
    """Apply the active discipline to a renewal. Never returns below `grant`,
    so no discipline can starve a renewal, and never raises at the boundary."""
    try:
        held = max(0, int(loops))
    except (TypeError, ValueError):
        held = 0
    try:
        offered = max(0, int(grant))
    except (TypeError, ValueError):
        offered = 0
    name = current_fuel()
    if name == "unlimited":
        return max(held, offered)
    if name == "accumulate":
        return held + offered
    if name == "carry":
        return min(CARRY * offered, held + offered)
    if name == "decay":
        return held * 3 // 4 + offered
    return max(held, offered)


def ceiling(grant):
    """The bound this discipline guarantees, or 0 when there is none."""
    try:
        offered = max(0, int(grant))
    except (TypeError, ValueError):
        offered = 0
    name = current_fuel()
    if name in ("accumulate", "unlimited"):
        return 0
    if name in ("carry", "decay"):
        return CARRY * offered
    return offered


def spend(loops):
    """Spend one model turn, except in the explicit agent-ended discipline."""
    try:
        held = max(0, int(loops))
    except (TypeError, ValueError):
        held = 0
    return held if current_fuel() == "unlimited" else max(0, held - 1)


def fuel_view():
    name = current_fuel()
    return "active fuel: %s — %s" % (name, FUELS[name]["description"])


def fuels_view():
    current = current_fuel()
    return "\n".join(
        ("● " if name == current else "  ") + name + " — " + spec["description"]
        for name, spec in FUELS.items()
    )


def set_fuel(name):
    requested = str(name or "").strip().lower()
    if requested not in FUELS:
        return "fuel-set failed: choose one of %s" % ", ".join(FUELS)
    with _lock:
        data = _load()
        data["fuel"] = requested
        if not _save(data):
            return "fuel-set failed: could not persist"
    return "fuel set to '%s'; persists across restarts" % requested

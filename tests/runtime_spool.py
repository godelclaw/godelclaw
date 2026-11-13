"""Read the runtime-event spool as the native client would take it."""
import json
import os


def events(folder):
    """The spooled events, oldest first; a missing folder holds none."""
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []
    found = []
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as stream:
                found.append(json.load(stream))
        except (OSError, ValueError):
            continue
    return found

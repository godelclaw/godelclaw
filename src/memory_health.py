"""The configured memory backend and whether its native service is reachable.

Memory is the native space service; `report()` states its configuration and
reachability and is cheap and read-only.
"""

import json
import os


def backend():
    """The configured memory backend: its mode and, for the native service, the
    collection name and socket."""
    path = os.environ.get("METTACLAW_MEMORY_BACKEND_CONFIG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "repos", "petta_lib_chromadb", "memory_backend.json")
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return {"mode": "unconfigured"}
    if not isinstance(config, dict):
        return {"mode": "unconfigured"}
    return {"mode": config.get("mode", "unconfigured"), "name": config.get("name"),
            "socket": config.get("socket")}


def native_reachable(socket_path):
    """Whether the native memory service accepts a connection (read-only)."""
    import socket
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(str(socket_path))
        return True
    except (OSError, TypeError):
        return False
    finally:
        probe.close()


def report():
    configured = backend()
    if configured["mode"] != "native":
        return "memory-health: no native memory is configured (backend %s)" % configured["mode"]
    state = "reachable" if native_reachable(configured["socket"]) else "UNREACHABLE"
    return "memory-health: native backend %s — service %s" % (configured["name"], state)


def boot_check():
    """Print a service-start diagnostic to the service journal, not the model prompt."""
    line = report()
    print("[memory_health] " + line, flush=True)
    return line


if __name__ == "__main__":
    boot_check()

"""One read-only observation snapshot for the native coding-session view.

This module deliberately contains no pin verdict, listing, summary, or clipping
policy.  It reuses the oracle's filesystem/date/hash readers and observes each
matched retirement at most once.  coding_estate_cetta.metta owns every decision
and every character of the rendered view; lifecycle and resume stay elsewhere.
"""
import json
import os
from pathlib import Path
import time

import coding_estate


def _date(path):
    try:
        return coding_estate._local_date(path.stat().st_mtime)
    except OSError:
        return coding_estate._local_date(time.time())


def _session(session, today):
    state = coding_estate._read_json(session / "state.json")
    runtime_path = session / "runtime.json"
    runtime_exists = runtime_path.is_file()
    runtime = coding_estate._read_json(runtime_path) if runtime_exists else {}
    lifecycle = coding_estate.read_lifecycle(session)
    root = runtime.get("root")
    bundle = Path(root) if isinstance(root, str) and root else None
    binary = runtime.get("binary")
    pin_date = _date(runtime_path) if runtime_exists else ""
    runs = coding_estate.read_runs(session)
    repin_date = None
    if runs:
        last_at = runs[-1].get("at")
        repin_date = (coding_estate._local_date(last_at)
                      if isinstance(last_at, (int, float)) else pin_date)
    return {
        "id": session.name,
        "state": state,
        "runtime": runtime,
        "lifecycle": lifecycle,
        "runtime_exists": runtime_exists,
        "root_exists": bool(bundle is not None and bundle.is_dir()),
        "root_name": Path(str(runtime.get("root", ""))).name,
        "matched": [entry.name for entry in coding_estate._matched_retirements(session)],
        "engine_hash": (coding_estate._engine_sha256(binary)
                        if isinstance(binary, str) and binary else None),
        "pin_date": pin_date,
        "status_date": coding_estate._status_date(session, state),
        "launching_date": coding_estate.launching_date(session),
        "runs_count": len(runs),
        "repin_date": repin_date,
    }


def snapshot(root=None, today=""):
    """Return one JSON snapshot; no old classify/listed/context_view call occurs."""
    base = Path(root) if root is not None else Path(os.getcwd())
    coding_dir = base / "memory" / "coding"
    settings = coding_estate._read_json(coding_estate.settings_path(base))
    available = coding_dir.is_dir()
    paths = []
    if available:
        try:
            paths = sorted(
                (path for path in coding_dir.iterdir()
                 if path.is_dir() and not path.name.startswith(".")),
                key=lambda path: path.name,
            )
        except OSError:
            available = False

    matched = {}
    for session in paths:
        for entry in coding_estate._matched_retirements(session):
            matched[entry.name] = entry
    world = coding_estate._resolve_world(
        coding_estate.World(today=str(today) if today else None),
        tuple(matched.values()),
    ) if matched else coding_estate.World(today=str(today) if today else None)

    runtimes_dir = base / "memory" / "coding-runtimes"
    runtime_paths = []
    if runtimes_dir.is_dir():
        try:
            runtime_paths = sorted(
                (path for path in runtimes_dir.iterdir()
                 if path.is_dir() and not path.name.startswith(".")),
                key=lambda path: path.name,
            )
        except OSError:
            runtime_paths = []
    payload = {
        "available": available,
        "mode": settings.get("context", "open"),
        "today": coding_estate._today_str(world),
        "selection": world.selection or {},
        "sessions": [_session(session, world.today) for session in paths],
        "runtimes": [{"id": path.name, "blocks": coding_estate._dir_blocks(path)}
                     for path in runtime_paths],
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

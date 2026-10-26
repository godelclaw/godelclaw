"""Fixture operations and forbidden-loader witnesses; not runtime loaders."""
import base64
import json
import os
from pathlib import Path
import time

import active_queries
import commitment_projection
import context_source_loaders
import context_sources
import development_state
import engine_modes
import iter_authoring
import project_capabilities
import project_evidence
import task_phase
from receipt_context_fixture import ReceiptViewWitness

MODULES = {
    "active-query-declaration": active_queries,
    "commitment-state": commitment_projection,
    "task-phase": task_phase,
    "development-state": development_state,
    "project-capabilities": project_capabilities,
    "project-evidence": project_evidence,
}
forbidden_calls = 0
calls = {}
engine_calls = 0
boundary_calls = {}
inside_python = False
command = None
counter_path = None
replacement_events = []
original_command = engine_modes.engine_command
original_load = context_source_loaders.load
original_prepare = context_source_loaders.prepare
original_loaders = context_sources._loaders
receipts = ReceiptViewWitness()


def boundary(name):
    if not inside_python:
        boundary_calls[name] = boundary_calls.get(name, 0) + 1


def counted_prepare(*args):
    global inside_python
    boundary("prepare")
    inside_python = True
    try:
        return original_prepare(*args)
    finally:
        inside_python = False


def apply(path):
    global command, counter_path
    value = json.loads(Path(path).read_text())
    for name, text in value.get("env", {}).items():
        if text is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = text
    for name, encoded in value.get("files", {}).items():
        target = Path(name)
        if encoded is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded))
    for name, encoded in value.get("replace_files", {}).items():
        target = Path(name)
        old = target.stat()
        replacement = target.with_name(target.name + ".replacement")
        replacement.write_bytes(base64.b64decode(encoded))
        replacement.chmod(old.st_mode & 0o777)
        os.replace(replacement, target)
        new = target.stat()
        assert old.st_ino != new.st_ino
        replacement_events.append({"path": name, "old_device": old.st_dev, "old_inode": old.st_ino,
                                   "new_device": new.st_dev, "new_inode": new.st_ino})
    for name, stamp in value.get("utime", {}).items():
        os.utime(name, ns=tuple(stamp))
    if "command" in value:
        command = value["command"]
        if len(command) >= 3:
            counter_path = Path(command[2])
    if value.get("sleep"):
        time.sleep(value["sleep"])
    return 1


def probe_events():
    return counter_path.read_text().splitlines() if counter_path and counter_path.exists() else []


def engine_command(name):
    global engine_calls
    engine_calls += 1
    boundary("engine-command")
    return tuple(command) if command is not None else original_command(name)


def forbidden(*args, **kwargs):
    global forbidden_calls
    forbidden_calls += 1
    raise AssertionError("ported Python loader used on native route")


def retained_loaders(conversation=None, views=None):
    result = []
    for spec, loader in original_loaders(conversation, views):
        # No unrelated live loaders. Keep real supplied action/pins views and
        # their sole marker owner; provide deterministic other-source text.
        if spec.source_id not in MODULES and spec.source_id not in {
                "work-state", "affect-gestalt", "pins", "effect-receipts", "recent-proposals", "relevant-files"}:
            loader = lambda source=spec.source_id: "fixture " + source
        result.append((spec, loader))
    return result


def counted_load(loaders, source_id):
    global inside_python
    boundary("retained-load")
    calls[source_id] = calls.get(source_id, 0) + 1
    inside_python = True
    try:
        return original_load(loaders, source_id)
    finally:
        inside_python = False


def setup(disable=1, caller=0):
    import strategy_host
    engine_modes.engine_command = engine_command
    receipts.install(disable, lambda: inside_python)
    if disable:
        for module in MODULES.values():
            module.view = forbidden
        iter_authoring.list_transformations = forbidden
        context_sources.bundle = context_sources.bundle_with_views = forbidden
    if caller:
        context_sources._loaders = retained_loaders
    context_source_loaders.load = counted_load
    context_source_loaders.prepare = counted_prepare
    for module, name, label in ((engine_modes, "active_engine", "pins-engine"),
                                (strategy_host, "enabled_flag", "action-strategy"),
                                (strategy_host, "conversation_mode", "conversation-mode")):
        original = getattr(module, name)
        def counted(*args, original=original, label=label, **kwargs):
            boundary(label)
            return original(*args, **kwargs)
        setattr(module, name, counted)
    return 1


def record(path, text):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"text": str(text), "forbidden": forbidden_calls,
                                 "calls": calls.copy(), "engine_calls": engine_calls,
                                 "boundaries": boundary_calls.copy(),
                                 "receipt_views": receipts.calls.copy(),
                                 "replacements": replacement_events.copy(),
                                 "probe_events": probe_events()}) + "\n")
    return 1

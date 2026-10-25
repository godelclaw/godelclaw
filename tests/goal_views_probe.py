"""Isolated project-04 instrumentation, not runtime view implementations."""
import json
from pathlib import Path

import goals
import helper
import context_sources
import small_views_probe as base

ORIGINAL = {name: getattr(goals, name) for name in
            ("work_state_view", "affect_view", "goals_view")}
ORIGINAL_SMALL = {name: module.view for name, module in base.MODULES.items()}
original_iter = base.iter_authoring.list_transformations
original_bundle = context_sources.bundle
original_mark = helper.change_mark
original_fair = None
marker_calls = {}
fair_calls = 0
retained_views = {}


def loaders(conversation=None, views=None):
    result = []
    for spec, loader in base.original_loaders(conversation, views):
        if spec.source_id in retained_views:
            loader = lambda source=spec.source_id: retained_views[source]
        elif spec.source_id not in base.MODULES and spec.source_id not in {
                "work-state", "affect-gestalt", "pins", "recent-proposals", "relevant-files"}:
            loader = lambda source=spec.source_id: "fixture " + source
        result.append((spec, loader))
    return result


def marked(name, text):
    marker_calls[name] = marker_calls.get(name, 0) + 1
    base.boundary("marker/" + name)
    return original_mark(name, text)


def fair():
    global fair_calls
    fair_calls += 1
    base.boundary("fair-snapshot")
    return original_fair()


def setup(native=1, caller=0):
    global original_fair
    base.retained_loaders = loaders
    base.setup(native, caller)
    helper.change_mark = marked
    if native:
        import goal_views_boundary
        original_fair = goal_views_boundary.fair_suffix
        goal_views_boundary.fair_suffix = fair
        for name in ORIGINAL:
            setattr(goals, name, base.forbidden)
    return 1


def fixture_fair():
    import strategy_host
    strategy_host.fair_enabled = lambda: True
    strategy_host.goal_snapshot = lambda: {"z": ["日🙂", {"b": 2, "a": True}], "a": "fixture"}
    return 1


def fixture_fair_missing():
    import strategy_host
    strategy_host.fair_enabled = lambda: True
    if hasattr(strategy_host, "goal_snapshot"):
        del strategy_host.goal_snapshot
    return 1


def wire(value):
    return json.dumps(["value", str(value)], ensure_ascii=True)


def python_view(name):
    """Explicit mixed-route oracle, never a native failure fallback."""
    base.inside_python = True
    try:
        return json.dumps(["value", ORIGINAL[name]()], ensure_ascii=True)
    except Exception:
        return json.dumps(["unavailable"])
    finally:
        base.inside_python = False


def python_bundle():
    """An explicitly selected Python turn between native turns, NOT fallback."""
    previous = {name: getattr(goals, name) for name in ORIGINAL}
    small = {name: module.view for name, module in base.MODULES.items()}
    previous_iter = base.iter_authoring.list_transformations
    base.inside_python = True
    try:
        for name, function in ORIGINAL.items():
            setattr(goals, name, function)
        for name, module in base.MODULES.items():
            module.view = ORIGINAL_SMALL[name]
        base.iter_authoring.list_transformations = original_iter
        return json.dumps(original_bundle(), ensure_ascii=True)
    finally:
        for name, function in previous.items():
            setattr(goals, name, function)
        for name, module in base.MODULES.items():
            module.view = small[name]
        base.iter_authoring.list_transformations = previous_iter
        base.inside_python = False


def record(path, text):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"text": str(text), "forbidden": base.forbidden_calls,
                                 "calls": base.calls.copy(), "boundaries": base.boundary_calls.copy(),
                                 "markers": marker_calls.copy(), "fair_calls": fair_calls}) + "\n")
    return 1

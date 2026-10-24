"""Isolated fixture loaders and witnesses, never a runtime implementation."""
import json
from pathlib import Path

import context_sources

_original_loaders = context_sources._loaders
calls = {}
forbidden_calls = 0
fixture = {}


class BrokenText:
    def __str__(self):
        raise ValueError("fixture text conversion failed")


def configure(path, disable=1):
    global fixture
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    calls.clear()
    context_sources._loaders = fixture_loaders
    if disable:
        context_sources.bundle = context_sources.bundle_with_views = forbidden
    return 1


def forbidden(*args, **kwargs):
    global forbidden_calls
    forbidden_calls += 1
    raise AssertionError("Python bundle used on native route")


def fixture_loaders(conversation=None, views=None):
    # Keep the real registry/selection/strategy/pins marker logic. Replace only
    # fixture-specified source functions; the three native views still travel
    # through their real loaders. Calls count actual loader invocations.
    sources = _original_loaders(conversation, views)
    result = []
    for spec, loader in sources:
        def counted(spec=spec, loader=loader):
            calls[spec.source_id] = calls.get(spec.source_id, 0) + 1
            if spec.source_id not in fixture:
                return loader()
            value = fixture[spec.source_id]
            if isinstance(value, dict) and value.get("raises"):
                raise RuntimeError("fixture unavailable")
            if isinstance(value, dict) and value.get("broken_text"):
                return BrokenText()
            return value
        result.append((spec, counted))
    return result


def record(path, text):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"text": str(text), "calls": calls.copy(),
                                 "forbidden": forbidden_calls}, ensure_ascii=False) + "\n")
    return 1


def note(path, text):
    Path(path).write_text(str(text), encoding="utf-8")
    return 1


def fail_prepare():
    import context_source_loaders
    context_source_loaders.prepare = forbidden
    return 1


def trigger(value):
    import strategy_host
    strategy_host._context_trigger = str(value)
    return 1

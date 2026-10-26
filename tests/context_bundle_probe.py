"""Isolated fixture loaders and witnesses, never a runtime implementation."""
import json
import os
from pathlib import Path

import context_sources
from receipt_context_fixture import ReceiptViewWitness, install_receipts

_original_loaders = context_sources._loaders
calls = {}
forbidden_calls = 0
fixture = {}
receipts = ReceiptViewWitness()


class BrokenText:
    def __str__(self):
        raise ValueError("fixture text conversion failed")


def configure(path, disable=1):
    global fixture
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "effect-receipts" not in fixture, "receipt fixtures must use real files, not Python loader text"
    state = fixture.pop("_receipt", None)
    if "_receipt_path" in fixture:
        target = Path(fixture.pop("_receipt_path"))
        assert target.resolve().is_relative_to(Path(path).parent.resolve())
        assert str(target) == os.environ["METTACLAW_EFFECT_RECEIPT_PATH"]
        install_receipts(target, state)
    calls.clear()
    receipts.reset()
    receipts.install(disable)
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
            assert spec.source_id != "effect-receipts" or spec.source_id not in fixture
            if spec.source_id not in fixture:
                return loader()
            value = fixture[spec.source_id]
            if isinstance(value, dict) and value.get("raises"):
                raise RuntimeError("fixture unavailable")
            if isinstance(value, dict) and value.get("broken_text"):
                return BrokenText()
            if isinstance(value, dict) and "codepoints" in value:
                return "".join(chr(c) for c in value["codepoints"])
            if isinstance(value, dict) and "wire" in value:
                return "loaded before injected wire failure"
            return value
        result.append((spec, counted))
    return result


def record(path, text):
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"text": str(text), "calls": calls.copy(),
                                 "receipt_views": receipts.calls.copy(),
                                 "forbidden": forbidden_calls}, ensure_ascii=True) + "\n")
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


def inject_wire_faults():
    """Corrupt transport only AFTER the retained loader/conversion has run."""
    import context_source_loaders

    original = context_source_loaders.load

    def load(loaders, source_id):
        wire = original(loaders, source_id)
        value = fixture.get(source_id)
        if isinstance(value, dict) and "wire" in value:
            return tuple(value["wire"])
        return wire

    context_source_loaders.load = load
    return 1

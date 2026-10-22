"""Test-only bridge witnesses; never a backend for the native implementation."""
import builtins
import json
import os
import subprocess

_open = builtins.open
_expected = []


def load(path):
    global _expected
    with _open(path, encoding="utf-8") as stream:
        _expected = json.load(stream)
    return 1


def expected(index):
    return _expected[index]


def forbid():
    import helper

    def forbidden(*args, **kwargs):
        raise AssertionError("native action window used Python backend/I/O")

    for name in ("recent_actions", "relevant_files", "_action_blocks",
                 "_commands_of", "_is_noise"):
        setattr(helper, name, forbidden)
    builtins.open = subprocess.run = subprocess.Popen = os.system = forbidden
    return 1


ACTION_SOURCES = ("recent-proposals", "relevant-files")


def action_loaders(conversation=None, views=None, loaders=None):
    """context_sources' loaders for the two action-window sources only."""
    import context_sources

    loaders = loaders or context_sources._loaders
    return [(spec, loader) for spec, loader in loaders(conversation, views)
            if spec.source_id in ACTION_SOURCES]


def action_sources(views=None):
    """The two sources as the bundle renders them, from a fresh projector."""
    import context_sources

    return context_sources.Projector().render(action_loaders(None, views))


def write_action_sources(views, path):
    with _open(path, "w", encoding="utf-8") as stream:
        stream.write(action_sources(views))
    return 1


def record_bundle(path):
    """Let the real bundle() run with only the two sources and write its text."""
    import context_sources

    original = context_sources._loaders
    context_sources._loaders = lambda conversation=None, views=None: action_loaders(
        conversation, views, original)
    context_sources._PROJECTOR = context_sources.Projector()
    bundle = context_sources.bundle

    def recorded(*args, **kwargs):
        text = bundle(*args, **kwargs)
        with _open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
        return text

    context_sources.bundle = recorded
    return 1

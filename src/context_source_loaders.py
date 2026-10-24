"""Loader boundary only; context_sources remains the SWI bundle and oracle.

No projection, clipping, hashing or rendering happens here. A captured loader
registry gives every source its own exception boundary and exactly one call.
In particular, pins' existing loader remains the sole change-marker owner.
"""
import context_sources
import json


class Loaders:
    def __init__(self, sources):
        self.by_id = {spec.source_id: loader for spec, loader in sources}


def prepare(views, *conversation):
    sources = context_sources._loaders(*conversation, views=views)
    return (Loaders(sources), tuple(
        (spec.source_id, spec.max_chars, spec.keep_tail)
        for spec, _ in sources))


def load(loaders, source_id):
    try:
        value = loaders.by_id[source_id]()
    except Exception:
        return (0,)
    # Conversion failures are bundle failures, not failed observations, in
    # Projector.observe. Keep conversion outside the loader exception boundary.
    # Foreign string atoms have a hidden payload after NUL. JSON is a wire
    # encoding, not projection policy; the native JSON parser recovers a String.
    if value is None:
        return (1,)
    # Match the bytes the oracle hashes. Lone surrogates cannot be rendered by
    # the native decoder: intentionally display '?' rather than lose a source.
    # str(value) still runs outside the loader exception boundary.
    text = str(value).encode("utf-8", "replace").decode("utf-8")
    return (2, json.dumps(text, ensure_ascii=True))

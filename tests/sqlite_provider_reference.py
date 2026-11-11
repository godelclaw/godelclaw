"""Frozen pre-retirement SQL oracles; never imported by the runtime.

The original policy and readers are an independent comparison for the native
kernel. Loading today's native wrappers would merely test the kernel against
itself. Keep these snapshots unchanged and verify their recorded hashes.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parent / "oracles/provider_sqlite"
MANIFEST = json.loads((DIRECTORY / "manifest.json").read_text())


def load(name):
    path = DIRECTORY / (name + ".py")
    if hashlib.sha256(path.read_bytes()).hexdigest() != MANIFEST["files"][path.name]:
        raise ValueError("frozen SQL reference differs from its manifest")
    spec = importlib.util.spec_from_file_location("sqlite_reference_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load("llm_policy")
# Bind the archived lifecycle's dependency once, without replacing production
# modules during any test operation or installing a runtime fallback.
with patch.dict(sys.modules, {"llm_policy": policy}):
    lifetime = load("native_lifetime")

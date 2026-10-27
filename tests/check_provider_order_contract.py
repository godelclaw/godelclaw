#!/usr/bin/env python3
"""Compare the actual captured-maximum witness with its checked Lean model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import unittest


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--cetta-root", required=True, type=Path)
    parser.add_argument("--lean", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ["CETTA_VALUE_WORKER"] = str(args.worker.resolve())
    os.environ["CETTA_CHANNEL_ROOT"] = str(args.cetta_root.resolve())
    from test_provider_order_cetta import ProviderOrderTests
    case = ProviderOrderTests("test_maximum_scope_accepts_lower_changes_and_rejects_new_higher_key")
    result = unittest.TestResult()
    case.run(result)
    if not result.wasSuccessful() or result.skipped or not hasattr(case, "last_trace"):
        raise AssertionError(dict(errors=result.errors, failures=result.failures, skipped=result.skipped))
    compiled = subprocess.run([str(args.lean.resolve()), "-j1", str(args.model.resolve())],
                              capture_output=True, text=True, timeout=120)
    if compiled.returncode:
        raise RuntimeError(compiled.stdout + compiled.stderr)
    expected = [line for line in compiled.stdout.splitlines() if line.startswith("MAXIMUM_SCOPE_TRACE ")]
    if expected != [case.last_trace] or case.last_sqlite_trace != case.last_trace:
        raise AssertionError(dict(native=case.last_trace, sqlite=case.last_sqlite_trace, model=expected))
    root = Path(__file__).resolve().parents[1]
    sources = ["src/provider_store_cetta.metta", "src/provider_indexes_cetta.metta",
               "src/provider_index_store_cetta.metta", "src/provider_order_cetta.metta",
               "tests/support/provider_order_probe.metta", "tests/test_provider_order_cetta.py"]
    receipt = dict(trace=case.last_trace, sqlite_trace=case.last_sqlite_trace, sqlite_version=sqlite3.sqlite_version,
                   model_sha256=digest(args.model), worker_sha256=digest(args.worker),
                   sources={name: digest(root / name) for name in sources},
                   scope="checked maximum/read-scope model and actual MeTTa commits; not a C or filesystem proof")
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

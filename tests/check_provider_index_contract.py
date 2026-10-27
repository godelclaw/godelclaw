#!/usr/bin/env python3
"""Replay the collision-footprint witness through MeTTa and checked Lean.

This is finite conformance evidence, not verification of C or the filesystem.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
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
    from test_provider_index_store_cetta import ProviderIndexStoreTests
    case = ProviderIndexStoreTests("test_full_row_reads_protect_a_hash_bucket_that_itself_does_not_change")
    result = unittest.TestResult()
    case.run(result)
    if not result.wasSuccessful() or result.skipped or not hasattr(case, "last_witness"):
        raise AssertionError(dict(errors=result.errors, failures=result.failures, skipped=result.skipped))
    witness = case.last_witness
    actual = "INDEX_QUERY_TRACE %d %d %s accepted" % (witness[1], witness[2], witness[3][1].name)
    compiled = subprocess.run([str(args.lean.resolve()), "-j1", str(args.model.resolve())],
                              capture_output=True, text=True, timeout=120)
    if compiled.returncode:
        raise RuntimeError(compiled.stdout + compiled.stderr)
    expected = [line for line in compiled.stdout.splitlines() if line.startswith("INDEX_QUERY_TRACE ")]
    if expected != [actual]:
        raise AssertionError(dict(native=actual, model=expected))
    root = Path(__file__).resolve().parents[1]
    sources = ["src/provider_store_cetta.metta", "src/provider_indexes_cetta.metta",
               "src/provider_index_store_cetta.metta", "src/provider_index_query_cetta.metta",
               "tests/support/provider_index_probe.metta", "tests/test_provider_index_store_cetta.py"]
    receipt = dict(trace=actual, model_sha256=digest(args.model), worker_sha256=digest(args.worker),
                   sources={name: digest(root / name) for name in sources},
                   scope="checked model and actual MeTTa/scoped-commit witness; no full implementation proof")
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

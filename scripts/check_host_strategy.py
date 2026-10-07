#!/usr/bin/env python3
"""Replay host checkpoints against the existing executable Lean strategy."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--checked-lib", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "host-checkpoints.jsonl"
    receipt.write_text("")
    env = dict(os.environ, METTACLAW_STRATEGY_TEST_RECEIPT=str(receipt))
    checked = subprocess.run([sys.executable, "-m", "unittest", "tests.test_observed_strategy", "-q"], env=env)
    if checked.returncode:
        return checked.returncode
    values = [json.loads(line) for line in receipt.read_text().splitlines()]
    names = ["authority", "controls", "steering", "mode", "correlation", "duplicate",
             "observation", "finish", "failure", "rest", "wake", "search", "late"]
    if [v["name"] for v in values] != names:
        raise RuntimeError("host receipt checkpoints are incomplete or reordered")
    models = args.model_root.resolve()
    canonical = models.parent / "mettapedia"
    toolchain = (canonical / "lean-toolchain").read_text().strip()
    paths = [output, args.checked_lib.resolve(), canonical / ".lake/build/lib/lean"]
    paths += sorted((canonical / ".lake/packages").glob("*/.lake/build/lib/lean"))
    env = dict(os.environ, LEAN_PATH=":".join(map(str, paths)), LEAN_NUM_THREADS="1")
    base = ["nice", "-n", "19", "lean", "+" + toolchain, "-j", "1"]
    compiled = subprocess.run(base + ["-o", str(output / "HostObservedStrategy.olean"),
                                    str(models / "HostObservedStrategy.lean")], cwd=models, env=env,
                              capture_output=True, text=True)
    (output / "host-model.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        print(compiled.stdout + compiled.stderr)
        return compiled.returncode
    source = output / "HostReplayReceipt.lean"
    source.write_text("import HostObservedStrategy\n\n"
                      "theorem replayed_receipt_matches : HostObservedStrategy.projections = "
                      + json.dumps([v["vector"] for v in values]) + " := by decide\n"
                      "#print axioms replayed_receipt_matches\n")
    compared = subprocess.run(base + [str(source)], cwd=models, env=env, capture_output=True, text=True)
    (output / "host-correspondence.log").write_text(compared.stdout + compared.stderr)
    print(compared.stdout + compared.stderr)
    return compared.returncode


if __name__ == "__main__":
    sys.exit(main())

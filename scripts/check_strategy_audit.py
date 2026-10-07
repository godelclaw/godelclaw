#!/usr/bin/env python3
"""Qualify the trace adapter and six deliberate failures with Lean."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_strategy import export_trace
from tests.test_strategy_audit import example, broken_views


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checked = subprocess.run([sys.executable, "-m", "unittest", "tests.test_strategy_audit", "-q"])
    if checked.returncode:
        return checked.returncode
    # Each run starts from a new private test state, never a live ledger.
    import tempfile
    with tempfile.TemporaryDirectory(dir=output) as directory:
        view = example(Path(directory) / "ledger.sqlite3")
    source = output / "StrategyTraceAudit.lean"
    source.write_bytes(args.model.read_bytes())
    base = ["nice", "-n", "19", "lean", "+" + args.toolchain.removeprefix("leanprover/lean4:"), "-j", "1"]
    env = dict(os.environ, LEAN_NUM_THREADS="1", LEAN_PATH=str(output))
    compiled = subprocess.run(base + ["-o", str(output / "StrategyTraceAudit.olean"), str(source)],
                              cwd=output, env=env, capture_output=True, text=True)
    (output / "model.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        print(compiled.stdout + compiled.stderr)
        return compiled.returncode
    proofs = ["import StrategyTraceAudit", "open StrategyTraceAudit",
              "set_option maxRecDepth 8192", "set_option maxHeartbeats 2000000"]
    for index, (name, mutated) in enumerate(broken_views(view).items()):
        terms, expected, _ = export_trace(mutated)
        proofs += [f"def events{index} : List Event := [" + ", ".join(terms) + "]",
                   f"def expected{index} : State := " + expected,
                   f"theorem control{index} : replay {{}} events{index} ≠ some expected{index} := by decide",
                   f"#print axioms control{index}"]
        print(f"Checking deliberate failure: {name}")
    controls = output / "FailureControls.lean"
    controls.write_text("\n".join(proofs) + "\n")
    checked = subprocess.run(base + [str(controls)], cwd=output, env=env, capture_output=True, text=True)
    (output / "controls.log").write_text(checked.stdout + checked.stderr)
    print(checked.stdout + checked.stderr)
    return checked.returncode


if __name__ == "__main__":
    sys.exit(main())

"""Project 04b receipts. The capability suite MUST fail on an unqualified engine.

Use the matching Python, CETTA_BIN, and --evidence outside the checkout.
No full-shadow/tmux suite is provided: that qualification is reviewer-owned.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time

from goal_bookkeeping_support import frozen_phase, run

SUITES = {
    "arithmetic": ["-m", "unittest", "-v", "test_goal_bookkeeping_native.ArithmeticTests"],
    "capability": ["-m", "unittest", "-v", "test_goal_bookkeeping_native.CapabilityTests"],
    "candidates": ["-m", "unittest", "-v", "test_goal_bookkeeping_candidates.CandidateTests"],
    "gaps": ["-m", "unittest", "-v", "test_goal_bookkeeping_candidates.CandidateGapTests"],
    "hand-corrupted": ["-m", "unittest", "-v", "test_goal_bookkeeping_candidates.HandCorruptedStateDiagnostics"],
    "diagnostics": ["-m", "unittest", "-v", "test_goal_bookkeeping_native.OutOfDomainArithmeticDiagnostics"],
    "domain": ["-m", "unittest", "-v", "test_goal_bookkeeping_domain"],
    "load-policy": ["-m", "unittest", "-v", "test_goal_bookkeeping_missing_mood"],
    "load-native": ["-m", "unittest", "-v", "test_goal_bookkeeping_load"],
    "views": ["tests/goal_bookkeeping_views_check.py"],
    "callers": ["-m", "unittest", "-v", "test_goal_bookkeeping_callers"],
    "utf8": ["-m", "unittest", "-v", "test_goal_bookkeeping_utf8"],
    "primitives": ["-m", "unittest", "-v", "test_goal_bookkeeping_native.PrimitiveTests"],
    "oracle": ["-m", "unittest", "-v", "test_goal_bookkeeping_oracle"],
    "replay": ["-m", "unittest", "-v", "test_goal_bookkeeping_replay"],
    "fallback": ["-m", "unittest", "-v", "test_goal_bookkeeping_fallback"],
    "regression": ["-m", "unittest", "-v", "test_goals_kernel", "test_work_state"],
    "bench": ["tests/goal_bookkeeping_bench.py"],
    "candidate-bench": ["tests/goal_bookkeeping_candidate_bench.py"],
    "caller-bench": ["tests/goal_bookkeeping_caller_bench.py"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    target = args.evidence.resolve()
    target.mkdir(parents=True, exist_ok=True)
    os.environ["GOAL_BOOKKEEPING_EVIDENCE"] = str(target)
    with tempfile.TemporaryDirectory(prefix="goal04b-check-", dir="/tmp") as temp:
        with frozen_phase(args.suite, Path(temp)):
            result = run([sys.executable] + SUITES[args.suite], Path(temp))
    output = result.stdout + result.stderr + "\nACTUAL_EXIT_CODE=%d\n" % result.returncode
    (target / (args.suite + "-%d.txt" % time.time_ns())).write_text(output)
    print(output, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

"""Actual branch applyHeartbeat cost, not full-shadow or a live instance.

One pass per fresh process/wake; warm passes are separate, each with fresh
unread bounded affect data. No step profiling/optimization campaign here.
"""
import json
import os
from pathlib import Path
import statistics
import tempfile
from unittest import mock

from goal_bookkeeping_support import receipt
from goal_bookkeeping_bench import fixture
from test_goal_bookkeeping_callers import caller_prefix, guest


def measured(directory, native, count):
    fixture(directory)
    program = caller_prefix(native, trace_io=False)
    program += '!(import! &self ./tests/goal_bookkeeping_candidate_bench.py)\n'
    for index in range(count):
        program += '''!(py-call (goal_bookkeeping_callers_probe.begin "%d" ""))
!(py-call (goal_bookkeeping_candidate_bench.append_trace))
!(let* (($start (py-call (goal_bookkeeping_callers_probe.clock)))
        ($next (applyHeartbeat (get-state &caller-periphery) 1000 True))
        ($end (py-call (goal_bookkeeping_callers_probe.clock))))
  (progn (change-state! &caller-periphery $next)
    (py-call (goal_bookkeeping_callers_probe.bench $start $end))))
''' % index
    with mock.patch.dict(os.environ, {"GK_TEST_WRITE_FAILURE": "never-injected",
         "GK_TEST_RENAME_FAILURE": "never-injected", "GK_TEST_APPEND_FAILURE": "0"}):
        result = guest(directory, program, native)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (directory / "caller-bench.jsonl").read_text().splitlines()]
    assert len(rows) == count
    return rows


def main():
    distributions = {}
    for native in (True, False):
        for warm in (False, True):
            name = ("native" if native else "swi-python") + ("-warm" if warm else "-fresh")
            rows = []
            for _ in range(1 if warm else 20):
                with tempfile.TemporaryDirectory(prefix="goal04b-real-heartbeat-", dir="/tmp") as temp:
                    sample = measured(Path(temp), native, 20 if warm else 1)
                    receipt("real-caller-cost-sample", {"route": name, "samples": sample,
                        "caller": "actual src/loop.metta applyHeartbeat, fresh unread log before every pass"})
                    rows.extend(sample)
            ns = [row["ns"] for row in rows]
            distributions[name] = {"ns": ns, "median_ms": statistics.median(ns) / 1e6,
                                   "min_ms": min(ns) / 1e6, "max_ms": max(ns) / 1e6}
    receipt("real-caller-cost-distributions", distributions)
    print(json.dumps({route: {k: v for k, v in row.items() if k != "ns"}
                      for route, row in distributions.items()}, indent=2))
    print("Actual branch heartbeat body; clock/channel/boot-continuation fixtures, NOT reviewer-owned full shadow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

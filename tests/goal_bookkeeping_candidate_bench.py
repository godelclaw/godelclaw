"""INACTIVE candidate versus oracle: one real pass/wake, fresh unread log data.

No production loop integration claim. Warm and instrumented distributions are
separate. Python supplies fixtures/observations only, never native goal writes.
"""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile

from goal_bookkeeping_support import ROOT, engine, receipt, run
from goal_bookkeeping_bench import fixture
from test_goal_bookkeeping_candidates import HEADER


def append_trace():
    path = Path(os.environ["METTACLAW_TELEGRAM_LOG_PATH"])
    with path.open("a") as stream:
        stream.write(json.dumps({"kind": "outbound", "text":
            "generated fresh ⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 A:.7 S:.8 Co:.9 Sp:.1⟩"}, ensure_ascii=False) + "\n")
    return 0


def measured(label, start, end, value):
    row = {"label": str(label), "ns": int(end) - int(start), "value": str(value)}
    if str(label) == "kernel":
        assert str(value).startswith("goal-kernel: "), value
        directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
        assert json.loads((directory / "goals_kernel.json").read_bytes())["log_offset"] == (directory / "updates.jsonl").stat().st_size
    with (Path(os.environ["METTACLAW_GOALS_PATH"]).parent / "bench.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    return 0


PROFILES = {
    "inventory": "$path $missing", "parse": "$lines", "state": "$path",
    "mood-samples": "$path $offset",
    "prepare-decay": "$goals $flags $budget $other $state $log $wake $previous $streaks",
    "emit-result": "$goals $flags $budget $other $mood", "json": "$value",
    "write": "$path $text", "rename": "$source $target",
}


def native(directory, count, profile):
    fixture(directory)
    program = HEADER + '!(import! &self ./tests/goal_bookkeeping_candidate_bench.py)\n'
    if profile:
        for name, args in PROFILES.items():
            program += ('!(match &self (= (gk:%s %s) $body) (progn '
                '(add-atom &self (= (gkb:%s %s) $body)) (remove-atom &self (= (gk:%s %s) $body))))\n'
                '(= (gk:%s %s) (let* (($gkb-start (system:monotonic-ns)) ($gkb-result (gkb:%s %s)) '
                '($gkb-end (system:monotonic-ns))) (progn '
                '(py-call (goal_bookkeeping_candidate_bench.measured "%s" $gkb-start $gkb-end (repr $gkb-result))) $gkb-result)))\n') % (
                name, args, name, args, name, args, name, args, name, args, name)
    for _ in range(count):
        program += '''!(py-call (goal_bookkeeping_candidate_bench.append_trace))
!(let* (($start (system:monotonic-ns)) ($value (gk:kernel-pass)) ($end (system:monotonic-ns)))
 (py-call (goal_bookkeeping_candidate_bench.measured "kernel" $start $end $value)))
'''
    result = engine(directory, program)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (directory / "bench.jsonl").read_text().splitlines()]
    steps = {}
    for row in rows:
        if row["label"] != "kernel":
            steps.setdefault(row["label"], []).append(row["ns"])
    samples = [row["ns"] for row in rows if row["label"] == "kernel"]
    assert len(samples) == count, "instrumented pass did not complete; inspect retained transcripts"
    return {"ns": samples, "steps_ns": steps}


def main():
    distributions = {}
    for implementation in ("oracle", "native"):
        for cache in ("fresh", "warm"):
            for profile in (False, True):
                label = implementation + "-" + cache + ("-profile" if profile else "")
                samples, steps = [], {}
                for _ in range(20 if cache == "fresh" else 1):
                    with tempfile.TemporaryDirectory(prefix="goal04b-native-bench-", dir="/tmp") as temp:
                        directory = Path(temp)
                        count = 1 if cache == "fresh" else 20
                        if implementation == "native":
                            row = native(directory, count, profile)
                        else:
                            command = [sys.executable, str(ROOT / "tests/goal_bookkeeping_bench.py"), "--worker", "--count", str(count)]
                            if profile:
                                command.append("--profile")
                            result = run(command, directory)
                            assert result.returncode == 0, result.stdout + result.stderr
                            row = json.loads(result.stdout)
                        receipt("candidate-bench-sample", {"mode": label, **row})
                        samples.extend(row["ns"])
                        for name, values in row["steps_ns"].items():
                            steps.setdefault(name, []).extend(values)
                distributions[label] = {"ns": samples, "steps_ns": steps,
                    "median_ms": statistics.median(samples) / 1e6,
                    "min_ms": min(samples) / 1e6, "max_ms": max(samples) / 1e6}
    receipt("candidate-bench-distributions", distributions)
    print(json.dumps({mode: {key: value for key, value in row.items() if key not in ("ns", "steps_ns")}
                      for mode, row in distributions.items()}, indent=2))
    print("Inactive candidate function cost only; production loop/skills remain unwired. All distributions retained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Oracle-only cost, NOT a native/caller speedup claim while writer is blocked.

Fresh processes each execute ONE pass with saved historical log offset plus
fresh unread data. Warm samples stay separate and also append fresh data.
Instrumented individual steps have a separate distribution, not headline cost.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest import mock

from goal_bookkeeping_support import ROOT, environment, receipt, run

sys.path.insert(0, str(ROOT / "src"))
import goals


def fixture(directory):
    path = directory / "goals.metta"
    path.write_text(";; generated benchmark\n(goal-budget 0.5)\n" + "\n".join(
        '(goal g%d Work sti:0.8 lti:0.3 vibes:(care) blocked-by:wait '
        'last-verified:never note:"generated 日本 🙂")' % i for i in range(12)) + "\n")
    log = directory / "updates.jsonl"
    log.write_text((json.dumps({"kind": "inbound", "text": "generated historical data"}) + "\n") * 1000)
    (directory / "goals_kernel.json").write_text(json.dumps({"streaks": {},
        "log_offset": log.stat().st_size, "mood": {d: 0.1 for d in goals.MOOD_DIMS}}))


def worker(directory, count, profile):
    fixture(directory)
    env = environment(directory)
    env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
    samples = []
    step_times = {}
    with mock.patch.dict(os.environ, env, clear=True), contextlib.ExitStack() as stack:
        if profile:
            for module, name in [(goals, name) for name in ("_read_lines", "_parse", "_load_kernel_state",
                     "_mood_samples", "_emit", "_write_lines")] + [(goals.json, "dump"), (goals.os, "replace")]:
                original = getattr(module, name)
                def measured(*args, _original=original, _name=name, **kwargs):
                    start = time.perf_counter_ns()
                    try:
                        return _original(*args, **kwargs)
                    finally:
                        step_times.setdefault(_name, []).append(time.perf_counter_ns() - start)
                stack.enter_context(mock.patch.object(module, name, measured))
        for index in range(count):
            with (directory / "updates.jsonl").open("a") as stream:
                stream.write(json.dumps({"kind": "outbound", "text":
                    "generated fresh ⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 A:.7 S:.8 Co:.9 Sp:.1⟩"}, ensure_ascii=False) + "\n")
            start = time.perf_counter_ns()
            result = goals.kernel_pass()
            duration = time.perf_counter_ns() - start
            assert not result.startswith("goal-kernel error:"), result
            assert json.loads((directory / "goals_kernel.json").read_bytes())["log_offset"] == (directory / "updates.jsonl").stat().st_size
            samples.append(duration)
    return {"ns": samples, "steps_ns": step_times, "fresh_unread_log_each_sample": True,
            "historical_records": 1000, "scope": "actual Python function, NOT real loop caller or native writer"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.worker:
        directory = Path(os.environ["METTACLAW_GOALS_PATH"]).parent
        print(json.dumps(worker(directory, args.count, args.profile)))
        return 0
    distributions = {}
    for mode in ("fresh-process", "fresh-process-profile", "warm-process", "warm-process-profile"):
        samples = []
        steps = {}
        for _ in range(20 if mode.startswith("fresh") else 1):
            with tempfile.TemporaryDirectory(prefix="goal04b-bench-", dir="/tmp") as temp:
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--count",
                           "1" if mode.startswith("fresh") else "20"]
                if mode.endswith("profile"):
                    command.append("--profile")
                start = time.perf_counter_ns()
                result = run(command, Path(temp))
                process_ns = time.perf_counter_ns() - start
                assert result.returncode == 0, result.stdout + result.stderr
                row = json.loads(result.stdout)
                row["process_ns"] = process_ns
                receipt("bench-sample", {"mode": mode, **row})
                samples.extend(row["ns"])
                for name, values in row["steps_ns"].items():
                    steps.setdefault(name, []).extend(values)
        distributions[mode] = {"ns": samples, "steps_ns": steps,
            "median_ms": statistics.median(samples) / 1e6, "min_ms": min(samples) / 1e6,
            "max_ms": max(samples) / 1e6}
    receipt("bench-distributions", distributions)
    print(json.dumps({mode: {k: v for k, v in data.items() if k not in ("ns", "steps_ns")}
                      for mode, data in distributions.items()}, indent=2))
    print("Oracle ONLY. Native writer and real caller performance BLOCKED, not measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

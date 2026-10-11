#!/usr/bin/env python3
"""Compare serial waiting with retained activities using CeTTa native HTTP.

Only loopback HTTP is used. No Telegram messages or model credentials are used.
Pass an HTTP/Python-enabled CeTTa executable and an output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-prefix", type=Path)
    parser.add_argument("--slow-ms", type=int, default=16000)
    parser.add_argument("--period-ms", type=int, default=5000)
    parser.add_argument("--input-ms", type=int, default=1000)
    parser.add_argument("--fast-ms", type=int, default=100)
    args = parser.parse_args()
    assert args.slow_ms > args.period_ms > args.input_ms > 0
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent
    env = dict(os.environ)
    if args.python_prefix:
        env.update(PYTHONHOME=str(args.python_prefix),
                   PATH=str(args.python_prefix / "bin") + ":" + env.get("PATH", ""),
                   LD_LIBRARY_PATH=str(args.python_prefix / "lib") + ":" + env.get("LD_LIBRARY_PATH", ""))
    env.update(RESPONSIVENESS_SLOW_MS=str(args.slow_ms),
               RESPONSIVENESS_PERIOD_MS=str(args.period_ms),
               RESPONSIVENESS_INPUT_MS=str(args.input_ms),
               RESPONSIVENESS_FAST_MS=str(args.fast_ms))
    summary = {"binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
               "fixture": {"slow_ms": args.slow_ms, "period_ms": args.period_ms,
                           "input_ms": args.input_ms, "fast_ms": args.fast_ms}, "runs": {}}
    for mode in ("blocking", "cooperative"):
        trace = (args.output / (mode + ".jsonl")).resolve()
        run_env = dict(env, RESPONSIVENESS_TRACE=str(trace), RESPONSIVENESS_MODE=mode)
        result = subprocess.run(["nice", "-n", "19", str(args.binary.resolve()),
                                 "--lang", "petta", str(root / "scheduler.metta")],
                                env=run_env, cwd=root, capture_output=True, text=True,
                                timeout=(args.slow_ms + args.fast_ms) / 1000 + 20)
        (args.output / (mode + ".stdout")).write_text(result.stdout)
        (args.output / (mode + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0 and not result.stderr, (mode, result)
        assert "Error" not in result.stdout, (mode, result.stdout)
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        def one(name):
            found = [event for event in events if event["event"] == name]
            assert len(found) == 1, (name, found)
            return found[0]
        noticed = one("input-noticed")
        short = one("short-completed")
        slow = one("slow-completed")
        requests = [event["value"] for event in events if event["event"] == "request-started"]
        assert requests == ["slow", "short"], requests
        assert short["value"] != slow["value"], (short, slow)
        ticks = [event for event in events if event["event"] == "alphabet"]
        assert ticks, events
        stats = {"input_delay_ms": noticed["at_ms"] - args.input_ms,
                 "short_reply_delay_ms": short["at_ms"] - args.input_ms,
                 "slow_completed_ms": slow["at_ms"],
                 "timer_lateness_ms": [event["at_ms"] - event["value"] for event in ticks],
                 "alphabet_at_ms": [event["at_ms"] for event in ticks],
                 "transport_ids": {"short": short["value"], "slow": slow["value"]}}
        if mode == "blocking":
            assert noticed["at_ms"] >= slow["at_ms"], events
            assert stats["input_delay_ms"] >= args.slow_ms - args.input_ms, stats
        else:
            assert short["at_ms"] < slow["at_ms"], events
            assert stats["input_delay_ms"] < args.period_ms, stats
            assert max(stats["timer_lateness_ms"]) < args.period_ms, stats
            assert len(ticks) >= args.slow_ms // args.period_ms, ticks
        summary["runs"][mode] = stats
        print(json.dumps({"mode": mode, **stats}), flush=True)
    summary["max_children_rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    (args.output / "qualification.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()

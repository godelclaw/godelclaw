#!/usr/bin/env python3
"""Offline reference/CeTTa comparison for Omega's fixed upstream core matrix."""
import argparse
import difflib
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from fixtures import omega_cases

MODES = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def normalized(value, root):
    if isinstance(value, str):
        return value.replace(str(root), "$ROOT")
    if isinstance(value, list):
        return [normalized(x, root) for x in value]
    if isinstance(value, dict):
        return {k: normalized(v, root) for k, v in value.items()}
    return value


def run(args, mode, name, scenario, evaluator, mutant=None):
    # Equal-length roots keep source CHARS_SENT observations comparable without
    # normalizing a semantic numeric field derived from an absolute path.
    root = Path(tempfile.mkdtemp(prefix=f"{mode}-case-", dir=args.output))
    prepare = module(f"{mode}_offline", MODES / mode / "offline.py").prepare
    entry = prepare(root, scenario, native_fs=evaluator == "cetta")
    (root / "identity.json").write_text(json.dumps({"mode": mode, "scenario": name, "evaluator": evaluator}))
    if mutant:
        path = root / "repos/Omega/src/loop.metta"
        path.write_text(mutant(path.read_text()))
    env = {"PATH": os.pathsep.join([str(args.python.parent), os.environ.get("PATH", "/usr/bin:/bin")]),
           "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8",
           "PYTHONNOUSERSITE": "1",
           "LD_LIBRARY_PATH": str(args.python.resolve().parent.parent / "lib"),
           "PYTHONPATH": os.pathsep.join((str(root), str(MODES))),
           f"{mode.upper()}_FIXTURE_ROOT": str(root)}
    if evaluator == "cetta":
        cmd = [str(args.cetta), "--lang", "petta", str(entry)]
    else:
        cmd = ["sh", str(args.petta / "run.sh"), str(entry), "--silent"]
    result = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=120)
    (root / "stdout.log").write_text(result.stdout)
    (root / "stderr.log").write_text(result.stderr)
    trace = root / "trace.jsonl"
    rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    if not scenario.get("expect_failure") and result.returncode:
        raise AssertionError(f"{mode}/{name}/{evaluator} exited {result.returncode}: {result.stderr[-400:]}")
    if scenario.get("expect_failure") and not result.returncode:
        raise AssertionError(f"{mode}/{name}/{evaluator} swallowed provider failure")
    completed = sum(row["kind"] == "boundary" for row in rows)
    expected = 0 if scenario.get("expect_failure") else scenario["iterations"]
    if completed != expected:
        raise AssertionError(f"{mode}/{name}/{evaluator}: {completed} boundaries, expected {expected}; {root}")
    if not any(row["kind"] == "request" for row in rows):
        raise AssertionError(f"{mode}/{name}/{evaluator}: no provider request")
    rendered = repr(rows)
    for marker in scenario.get("trace_contains", []):
        if marker not in rendered:
            raise AssertionError(
                f"{mode}/{name}/{evaluator}: missing trace witness {marker!r}")
    return normalized(rows, root), root


def compare(left, right):
    if left == right:
        return
    a = json.dumps(left, indent=2, ensure_ascii=False).splitlines()
    b = json.dumps(right, indent=2, ensure_ascii=False).splitlines()
    diff = "\n".join(list(difflib.unified_diff(a, b, fromfile="reference", tofile="cetta"))[:65])
    raise AssertionError(diff)


def verify_manifest(mode):
    manifest = json.loads((MODES / mode / "upstream-manifest.json").read_text())
    for item in manifest["files"]:
        if item.get("vendored"):
            path = MODES / mode / "upstream" / item["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise AssertionError(f"modified upstream: {path}")
    return manifest["commit"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cetta", type=Path, required=True)
    parser.add_argument("--petta", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["omega"], default="omega")
    parser.add_argument("--case")
    parser.add_argument("--mutants", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    matrix = json.loads((MODES / "conformance/scenarios.json").read_text())
    if args.case and not any(row["id"] == args.case for row in matrix["omega"]):
        parser.error("unknown scenario for the selected mode")
    summary = {"cases": [], "failures": [], "mutants": []}
    references = {}
    for mode, cases in [("omega", omega_cases())]:
        verify_manifest(mode)
        assert set(cases) == {row["id"] for row in matrix[mode]} and len(cases) <= 12
        for name, scenario in cases.items():
            if args.case and args.case != name:
                continue
            for variant, overrides in enumerate(scenario.get("variants", [{}])):
                fixture = {**scenario, **overrides}
                try:
                    reference, rroot = run(args, mode, name, fixture, "reference")
                    candidate, croot = run(args, mode, name, fixture, "cetta")
                    compare(reference, candidate)
                    references[mode, name] = reference
                    if scenario.get("restart"):
                        persisted = (rroot / "repos/Omega/memory/history.metta").read_text()
                        restart = {**fixture, "iterations": 1, "events": [], "replies": fixture["replies"][-1:]}
                        restart["history"] = persisted
                        compare(run(args, mode, name + "-restart", restart, "reference")[0],
                                run(args, mode, name + "-restart", restart, "cetta")[0])
                    summary["cases"].append([mode, name, variant])
                    print(f"PASS {mode}/{name}/{variant}", flush=True)
                except Exception as error:
                    summary["failures"].append([mode, name, variant, str(error)])
                    print(f"FAIL {mode}/{name}/{variant}: {error}", flush=True)
    if args.mutants:
        mutants = [
            ("omega", "duplicate-input", "duplicate-rearms", '(!= $msgrcv (get-state &prevmsg))', 'True'),
            ("omega", "wake-boundary", "inclusive-wake", '(> (get_time) (get-state &nextWakeAt))', '(>= (get_time) (get-state &nextWakeAt))'),
            ("omega", "budget-exhaustion", "no-decrement", '(- (get-state &loops) 1)', '(- (get-state &loops) 0)')]
        for mode, name, ident, before, after in mutants:
            if (mode, name) not in references:
                continue
            fixture = omega_cases()[name]
            def mutate(text):
                if text.count(before) != 1:
                    raise ValueError(f"{ident}: mutation target not unique")
                return text.replace(before, after)
            try:
                changed, root = run(args, mode, ident, fixture, "cetta", mutate)
                detected = changed != references[mode, name]
            except AssertionError:
                detected = True
            summary["mutants"].append([mode, ident, detected])
            print(f"{'DETECTED' if detected else 'MISSED'} {mode}/{ident}", flush=True)
            if not detected:
                summary["failures"].append([mode, ident, "mutant undetected"])
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    return bool(summary["failures"])


if __name__ == "__main__":
    sys.exit(main())

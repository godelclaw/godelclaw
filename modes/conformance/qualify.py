#!/usr/bin/env python3
"""One strict offline command: matrix, mutants, source tests and Lean replay."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PETTA_PIN = "7037f4c2ad378c52fc328004fe216d5118b674f0"


def main():
    parser = argparse.ArgumentParser()
    for field in ("cetta", "petta", "python", "formal-root", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--cetta-source", type=Path,
                        help="CeTTa source tree when --cetta names an installed bundle")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    oracle = subprocess.check_output(["git", "-C", str(args.petta), "rev-parse", "HEAD"], text=True).strip()
    if oracle != PETTA_PIN:
        raise SystemExit("Omega oracle must be the recorded PeTTa v1.0.4 pin")
    steps = []
    def run(label, command, env=None, cwd=None):
        with (args.output / (label + ".log")).open("w") as stream:
            result = subprocess.run(command, env=env, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        steps.append({"stage": label, "exit": result.returncode})
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {label}", flush=True)
        return result.returncode == 0
    common = ["--cetta", str(args.cetta), "--petta", str(args.petta), "--python", str(args.python)]
    matrix = args.output / "matrix"
    matrix_ok = run("matrix", [sys.executable, str(HERE / "verify.py"), *common, "--output", str(matrix), "--mutants"])
    if matrix_ok:
        summary = json.loads((matrix / "summary.json").read_text())
        assert len({(m, n) for m, n, _ in summary["cases"]}) == 24
        assert len(summary["mutants"]) == 6 and all(row[2] for row in summary["mutants"])
    run("upstream-tests", [sys.executable, str(HERE / "upstream_tests.py"), *common,
                            "--output", str(args.output / "upstream-tests")])
    lean_root = args.formal_root / "lean/pettaclaw"
    build = lean_root / ".build/upstream-cores"
    build.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(LEAN_PATH=str(build))
    for name in ("ClawArchitectures", "PresentMoment", "IterArchitecture", "OmegaArchitecture"):
        if not run("lean-" + name, ["lean", "-o", str(build / (name + ".olean")), str(lean_root / (name + ".lean"))], env=env, cwd=args.formal_root):
            break
    if matrix_ok:
        replay = build / "TraceReplay.lean"
        if run("generate-replay", [sys.executable, str(HERE / "replay.py"), "--traces", str(matrix), "--output", str(replay)]):
            run("lean-replay", ["lean", str(replay)], env=env, cwd=args.formal_root)
    # Small focused runtime check for the lib/proc text boundary used by Iter.
    source = args.cetta_source or args.cetta.parent
    proc = source / "tests/petta/proc_text_boundary.metta"
    if not proc.is_file():
        raise SystemExit("proc boundary fixture missing; pass --cetta-source with the matching CeTTa checkout")
    build_manifest = args.cetta.parent / "build-manifest.json"
    if build_manifest.is_file() and (source / ".git").exists():
        built_revision = json.loads(build_manifest.read_text()).get("revision")
        source_revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if built_revision and source_revision != built_revision:
            raise SystemExit("--cetta-source revision does not match the installed CeTTa bundle")
    runtime_env = os.environ.copy()
    runtime_env.update(PYTHONHOME=str(args.python.parent.parent), LD_LIBRARY_PATH=str(args.python.parent.parent / "lib"))
    run("proc-text-boundary", [str(args.cetta), "--lang", "petta", str(proc)], env=runtime_env)
    provenance = {"petta": oracle, "cetta_sha256": hashlib.sha256(args.cetta.read_bytes()).hexdigest(),
                  "lean": subprocess.check_output(["lean", "--version"], text=True).strip(),
                  "stages": steps, "complete": all(step["exit"] == 0 for step in steps)}
    (args.output / "qualification.json").write_text(json.dumps(provenance, indent=2))
    return not provenance["complete"]


if __name__ == "__main__":
    sys.exit(main())

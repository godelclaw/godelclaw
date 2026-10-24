"""Reproducible, isolated project-02 qualification and complete transcripts."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/home/oruzi/miniforge3/envs/petta/bin/python"
ENGINE = "/home/oruzi/.local/lib/pettaclaw/cetta-ours-streaming-20261022/cetta"
SUITES = {
    "native": ["-m", "unittest", "-v", "test_context_bundle_native"],
    "regression": ["-m", "unittest", "-v", "test_pins_context_native", "test_turn_additions",
                   "test_context_continuity", "test_action_window_native", "test_undelivered_prose",
                   "test_context_sources"],
    "shadow": ["-m", "unittest", "-v", "test_godel_full_shadow"],
    "bench": ["tests/context_bundle_bench.py"],
}


def limit():
    bound = 25165824 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="context-bundle-check-", dir="/tmp") as temp:
        directory = Path(temp)
        deny = directory / "bin"
        deny.mkdir()
        (deny / "tmux").write_text("#!/bin/sh\nprintf '%s\\n' 'BLOCKED: project 02 prohibits tmux' >&2\nexit 126\n")
        (deny / "tmux").chmod(0o700)
        env = isolated_environ()
        # No inherited channel/provider secrets, including non-METTACLAW keys.
        for name in list(env):
            if any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")):
                env.pop(name)
        env.update(CETTA_BIN=ENGINE, PETTA_PY_ENV=str(Path(PYTHON).parents[1]),
                   PETTA_ROOT="/home/oruzi/repos/PeTTa", OPENAI_API_KEY="test-only-unused",
                   PYTHONPATH=os.pathsep.join(str(ROOT / part) for part in ("tests", "src", "channels")),
                   PYTHONDONTWRITEBYTECODE="1", TMPDIR=temp,
                   XDG_STATE_HOME=str(directory / "state"), XDG_CONFIG_HOME=str(directory / "config"),
                   XDG_CACHE_HOME=str(directory / "cache"),
                   PATH=str(deny) + os.pathsep + env.get("PATH", ""),
                   CONTEXT_BUNDLE_EVIDENCE=str(evidence))
        # Record the existing harnesses' captured engine outputs too, without
        # changing their assertions or subprocess environments.
        entry = ("import context_bundle_transcripts,runpy,sys; "
                 "sys.argv=sys.argv[1:]; runpy.run_module('unittest',run_name='__main__')")
        command = ([PYTHON, "-c", entry, "unittest"] + SUITES[args.suite][2:]
                   if args.suite != "bench" else [PYTHON] + SUITES[args.suite])
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, preexec_fn=limit)
    transcript = result.stdout + "\nACTUAL_EXIT_CODE=%d\n" % result.returncode
    path = evidence / (args.suite + ".txt")
    if path.exists():
        path.rename(evidence / ("%s-previous-%d.txt" % (args.suite, time.time_ns())))
    path.write_text(transcript, encoding="utf-8")
    (evidence / (args.suite + "-result.json")).write_text(json.dumps({
        "command": command, "cwd": str(ROOT), "returncode": result.returncode,
        "engine": ENGINE, "ulimit_v_kib": 25165824,
        "tmux": "denied by PATH stub, no server access"}, indent=2) + "\n")
    print(transcript, end="")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

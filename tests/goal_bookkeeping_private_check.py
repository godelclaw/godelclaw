"""Read only the three authorized source files; write PRIVATE disposable copies.

Compares corrected Python and direct native interfaces on private copies.
Private receipts must never be committed or published. Native Python writers
are disabled; no Python serializer supplies any native persisted bytes.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from goal_bookkeeping_support import ROOT, engine, environment, frozen_phase

sys.path.insert(0, str(ROOT / "src"))
import goals

NAMES = ("goals.metta", "goals_kernel.json", "goals_compost.log")


def hashes(directory):
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in NAMES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert args.evidence.stat().st_mode & 0o077 == 0, "private receipt directory must be 0700"
    with mock.patch.dict(os.environ, {"GOAL_BOOKKEEPING_EVIDENCE": str(args.evidence.resolve())}):
        with tempfile.TemporaryDirectory(prefix="goal04b-private-identity-") as temp:
            with frozen_phase("private-copy", Path(temp)):
                return check_private(args)


def check_private(args):
    before = hashes(args.source_dir)
    rows = []
    with tempfile.TemporaryDirectory(prefix="goal04b-private-copy-", dir="/tmp") as temp:
        directory = Path(temp)
        initial = {}
        for name in NAMES:
            # Read ONLY these three source files; all writes are in temp.
            initial[name] = (args.source_dir / name).read_bytes()
            (directory / name).write_bytes(initial[name])
        env = environment(directory)
        env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
        (directory / "updates.jsonl").write_text(json.dumps({"kind": "outbound", "text":
            "generated ⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 A:.7 S:.8 Co:.9 Sp:.1⟩"}, ensure_ascii=False) + "\n")
        generated = ('(goal project04b-private-generated-qualification Work sti:0.5 lti:0.3 vibes:(care) '
                     'blocked-by:none last-verified:2026-01-02 note:"generated 日本 🙂")')
        assert "project04b-private-generated-qualification" not in {
            g["name"] for g in goals._parse(initial["goals.metta"].decode().splitlines())[0]}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(goals, "_runtime_human_wake", None), \
             mock.patch.object(goals, "_retire_in_ledger", return_value=2), \
             mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"):
            for label, function in (("kernel", goals.kernel_pass),
                    ("fresh", lambda: goals.human_wake("generated-private-batch")),
                    ("repeated", lambda: goals.human_wake("generated-private-batch")),
                    ("write", lambda: goals.goal_write(generated)),
                    ("drop", lambda: goals.goal_drop("project04b-private-generated-qualification", "retire"))):
                result = function()
                assert not (isinstance(result, str) and result.startswith("goal-kernel error:"))
                rows.append({"label": label, "result": result, "files_base64": {
                    name: base64.b64encode((directory / name).read_bytes()).decode() for name in NAMES}})
            assert rows[1]["result"]["fresh"] == 1
            assert rows[2]["result"]["fresh"] == 0
            assert rows[1]["files_base64"] == rows[2]["files_base64"]
        for name in NAMES:
            (directory / name).write_bytes(initial[name])
        from test_goal_bookkeeping_candidates import HEADER, FROZEN
        program = HEADER + FROZEN
        expressions = [("kernel", "(gk:kernel-pass)"),
                       ("fresh", '(gk:human-wake "generated-private-batch")'),
                       ("repeated", '(gk:human-wake "generated-private-batch")'),
                       ("write", "(gk:goal-write %s)" % json.dumps(generated, ensure_ascii=False)),
                       ("drop", '(gk:goal-drop "project04b-private-generated-qualification" "retire")')]
        for label, expression in expressions:
            program += '!(py-call (goal_bookkeeping_probe.candidate_record "%s" %s))\n' % (label, expression)
        # All native transcripts/observations for this fixture MUST be private.
        with mock.patch.dict(os.environ, {"GOAL_BOOKKEEPING_EVIDENCE": str(args.evidence.resolve())}):
            result = engine(directory, program)
        assert result.returncode == 0, "private native check failed; inspect private receipts"
        actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
        (args.evidence / "candidate-private-attempt.json").write_text(json.dumps({
            "expected": rows, "actual": actual, "source_hashes": before}, indent=2))
        for expected, observed in zip(rows, actual, strict=True):
            value = expected["result"]
            if isinstance(value, dict):
                value = ["GoalWake", value["fresh"], value["maintenance_applied"], value["message"]]
            assert observed["value"] == value, "private message mismatch; inspect private receipts"
            assert observed["files"] == expected["files_base64"], "private byte mismatch; inspect private receipts"
    unchanged = before == hashes(args.source_dir)
    (args.evidence / "candidate-private.json").write_text(json.dumps({"expected": rows, "actual": actual,
        "source_hashes": before, "originals_unchanged": unchanged, "native_writer_parity": True,
        "scope": "five operations on private disposable copies; no production activation"}, indent=2))
    assert unchanged, "source changed during read-only private-copy check"
    print(json.dumps({"returncode": 0, "originals_unchanged": unchanged,
                      "scope": "5 corrected-oracle/native candidate operations on private disposable copies"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

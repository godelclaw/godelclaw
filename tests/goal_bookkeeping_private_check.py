"""Read only the three authorized source files; write PRIVATE disposable copies.

This characterizes the Python oracle only. The blocked native writer is not
silently replaced by it. Private receipts must never be committed or published.
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

from goal_bookkeeping_support import ROOT, environment

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
    before = hashes(args.source_dir)
    rows = []
    with tempfile.TemporaryDirectory(prefix="goal04b-private-copy-", dir="/tmp") as temp:
        directory = Path(temp)
        for name in NAMES:
            # Read ONLY these three source files; all writes are in temp.
            (directory / name).write_bytes((args.source_dir / name).read_bytes())
        env = environment(directory)
        env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
        (directory / "updates.jsonl").write_text(json.dumps({"kind": "outbound", "text":
            "generated ⋄⟨Cn:.2 C:.3 Ct:.4 I:.5 J:.6 A:.7 S:.8 Co:.9 Sp:.1⟩"}, ensure_ascii=False) + "\n")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(goals, "_runtime_human_wake", None):
            for label, function in (("kernel", goals.kernel_pass),
                    ("fresh", lambda: goals.human_wake("generated-private-batch")),
                    ("repeated", lambda: goals.human_wake("generated-private-batch"))):
                result = function()
                assert not (isinstance(result, str) and result.startswith("goal-kernel error:"))
                rows.append({"label": label, "result": result, "files_base64": {
                    name: base64.b64encode((directory / name).read_bytes()).decode() for name in NAMES}})
            assert rows[-2]["result"]["fresh"] == 1
            assert rows[-1]["result"]["fresh"] == 0
            assert rows[-2]["files_base64"] == rows[-1]["files_base64"]
    unchanged = before == hashes(args.source_dir)
    (args.evidence / "oracle-private.json").write_text(json.dumps({"rows": rows,
        "source_hashes": before, "originals_unchanged": unchanged, "native_writer_parity": False}, indent=2))
    assert unchanged, "source changed during read-only private-copy check"
    print(json.dumps({"returncode": 0, "originals_unchanged": unchanged,
                      "scope": "3 oracle steps on disposable private copies, NOT native writer parity"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

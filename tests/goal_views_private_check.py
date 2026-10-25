"""Read ONLY the three task-authorized files; compare PRIVATE disposable copies.

Pass --source-dir explicitly. Never run bookkeeping. Personal fixture bytes and
all captured view output go only into --evidence (required mode 0700), never Git.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest import mock

from isolated_env import isolated_environ
from test_goal_views_native import GoalViewsTest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert args.evidence.stat().st_mode & 0o077 == 0, "private receipts must be mode 0700"
    names = ("goals.metta", "goals_kernel.json", "goals_compost.log")
    before = {name: digest(args.source_dir / name) for name in names}
    status = 1
    with (args.evidence / "check.txt").open("w") as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        try:
            env = isolated_environ(CONTEXT_BUNDLE_EVIDENCE=str(args.evidence))
            for name in list(env):
                if any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")):
                    env.pop(name)
            with mock.patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory(prefix="private-goal-copy-") as temp:
                copies = Path(temp)
                for name in names:
                    shutil.copyfile(args.source_dir / name, copies / name)
                    (copies / name).chmod(0o400)
                import context_bundle_transcripts  # noqa: F401
                case = GoalViewsTest()
                case.setUp()
                try:
                    present = case.step((copies / names[0]).read_bytes(),
                                        mood=(copies / names[1]).read_bytes(),
                                        compost=(copies / names[2]).read_text())
                    for name in ("work_state_view", "affect_view", "goals_view"):
                        case.compare(name, [present, present], skill=name == "goals_view")
                    case.compare("work_state_view", [present, present], caller=True)
                    case.compare("work_state_view", [present] * 4, mixed=True)
                finally:
                    case.doCleanups()
            assert before == {name: digest(args.source_dir / name) for name in names}
            status = 0
        except Exception:
            import traceback
            traceback.print_exc()
    (args.evidence / "result.json").write_text(json.dumps({"returncode": status,
        "originals_unchanged": before == {name: digest(args.source_dir / name) for name in names},
        "python": sys.executable, "views": 3, "caller_steps": 2, "mixed_route_steps": 4}) + "\n")
    print(json.dumps({"returncode": status, "private_receipts": str(args.evidence)}))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

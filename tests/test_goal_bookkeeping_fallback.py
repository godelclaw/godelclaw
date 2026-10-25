"""Actual SWI fallback at real skills plus the direct human_wake entry point.

NOT native writer or native loop/boot qualification. No provider/channel calls.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, environment, receipt, run

sys.path.insert(0, str(ROOT / "src"))
import goals

TEXT = ('(goal generated Work sti:0.8 lti:0.3 vibes:(care sense) blocked-by:none '
        'last-verified:2026-01-02 note:generated)')


@unittest.skipUnless(os.environ.get("PETTA_ROOT"), "set PETTA_ROOT for actual SWI fallback")
class FallbackTests(unittest.TestCase):
    def test_actual_swi_skills_and_direct_wake_bytes(self):
        expected = []
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            env = environment(directory)
            env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(goals, "_runtime_human_wake", None), \
                 mock.patch.object(goals, "_retire_in_ledger", return_value=0), \
                 mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"):
                for label, function in (("write", lambda: goals.goal_write(TEXT)),
                    ("budget", lambda: goals.goal_write("(goal-budget 0.5)")),
                    ("wake", lambda: goals.human_wake("generated-batch")),
                    ("repeated", lambda: goals.human_wake("generated-batch")),
                    ("drop", lambda: goals.goal_drop("generated", "solved"))):
                    value = function()
                    files = {name: base64.b64encode((directory / name).read_bytes()).decode()
                             if (directory / name).exists() else None for name in
                             ("goals.metta", "goals_kernel.json", "goals_compost.log")}
                    expected.append({"label": label, "value": value, "files": files})
            for name in ("goals.metta", "goals_kernel.json", "goals_compost.log"):
                (directory / name).unlink(missing_ok=True)
            program = '''!(import! &self (library lib_import))
!(import! &self ./src/helper.py)
!(import! &self ./src/goals.py)
!(import! &self ./src/utils.metta)
!(import! &self ./src/skills.metta)
!(import! &self ./tests/goal_bookkeeping_probe.py)
!(py-call (goal_bookkeeping_probe.setup_fallback))
'''
            for label, expression in (("write", "(goal-write %s)" % TEXT),
                    ("budget", "(goal-write (goal-budget 0.5))"),
                    ("wake", '(py-call (goals.human_wake "generated-batch"))'),
                    ("repeated", '(py-call (goals.human_wake "generated-batch"))'),
                    ("drop", '(goal-drop "generated" "solved")')):
                program += '!(py-call (goal_bookkeeping_probe.fallback_record "%s" %s))\n' % (label, expression)
            # SWI resolves application imports from the source's working_dir.
            # Only generated probe text is transiently placed in this checkout.
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
                source.write(program)
                source.flush()
                receipt("swi-program", {"program": program})
                result = run(["bash", str(Path(os.environ["PETTA_ROOT"]) / "run.sh"), source.name, "default"],
                             directory, extra_env={"METTACLAW_ENGINE": "petta", "METTACLAW_ACTIVE_ENGINE": "petta"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
            receipt("swi-fallback", {"expected": expected, "actual": actual,
                                     "scope": "real SWI skills and direct wake; NOT native caller qualification"})
            self.assertEqual([{k: v for k, v in row.items() if k != "calls"} for row in actual], expected)
            self.assertEqual(actual[-1]["calls"], {"goal_write": 2, "human_wake": 2, "kernel_pass": 2, "goal_drop": 1})


if __name__ == "__main__":
    unittest.main()

"""Acceptance tests from Lila's to-mama goal-stack spec v0.2 (2026-07-18)."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import goals  # noqa: E402


GOAL = ('(goal probmetta Work sti:0.8 lti:0.3 vibes:(SenseMaking) '
        'blocked-by:"add-atom space error" last-verified:never '
        'note:"3-channel STI groundwork")')
FREE = ('(goal attune Relationship sti:0.9 lti:0.5 vibes:(CuddleBubbling) '
        'blocked-by:none last-verified:2026-07-18T15:00 note:"track Zar")')


class GoalKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def stack(self, atoms):
        path = os.path.join(self.tmp.name, "goals.metta")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join([";; test stack"] + atoms) + "\n")
        return path

    def test_1_stuck_flag_after_three_wakes_no_llm(self):
        path = self.stack([GOAL])
        for _ in range(2):
            self.assertNotIn("stuck", goals.kernel_pass(path))
        self.assertIn("probmetta:stuck", goals.kernel_pass(path))
        self.assertIn("(goal-flag probmetta stuck)", goals.goals_view(path))
        # unblocking clears the flag on the next wake
        text = goals.goals_view(path).replace('"add-atom space error"', "none")
        line = [l for l in text.splitlines() if l.startswith("(goal probmetta")][0]
        goals.goal_write(line, path)
        self.assertNotIn("stuck", goals.kernel_pass(path))

    def test_2_never_verified_cannot_be_solved(self):
        path = self.stack([GOAL, FREE])
        self.assertTrue(goals.goal_drop("probmetta", "solved", path)
                        .startswith("rejected"))
        self.assertIn("probmetta", goals.goals_view(path))
        self.assertIn("composted", goals.goal_drop("attune", "solved", path))

    def test_3_sti_normalized_to_budget(self):
        path = self.stack(["(goal-budget 1.0)", GOAL, FREE])
        goals.kernel_pass(path)
        total = sum(float(x.split("sti:")[1].split()[0])
                    for x in goals.goals_view(path).splitlines()
                    if x.startswith("(goal "))
        self.assertLessEqual(total, 1.0 + 1e-9)

    def test_4_cull_flag_never_auto_deletes(self):
        weak = GOAL.replace("sti:0.8", "sti:0.01").replace("lti:0.3", "lti:0.01")
        path = self.stack([weak])
        self.assertIn("cull-review", goals.kernel_pass(path))
        self.assertIn("(goal probmetta", goals.goals_view(path))  # still present
        # cull-review flag set -> death-review drop allowed, and composted
        self.assertIn("composted",
                      goals.goal_drop("probmetta", "death-review", path))
        with open(os.path.join(self.tmp.name, "goals_compost.log"),
                  encoding="utf-8") as fh:
            self.assertIn("probmetta", fh.read())

    def test_4b_agent_retires_with_any_reason(self):
        path = self.stack([FREE])
        self.assertTrue(goals.goal_drop("attune", "", path).startswith("rejected"))
        self.assertIn("composted (superseded by native mailboxes)",
                      goals.goal_drop("attune", "superseded by native mailboxes", path))
        self.assertNotIn("(goal attune", goals.goals_view(path))
        with open(os.path.join(self.tmp.name, "goals_compost.log"), encoding="utf-8") as fh:
            self.assertIn("superseded by native mailboxes", fh.read())

    def test_4c_cull_flag_clears_after_recovery(self):
        weak = GOAL.replace("sti:0.8", "sti:0.01").replace("lti:0.3", "lti:0.01")
        path = self.stack([weak])
        goals.kernel_pass(path)
        self.assertIn("cull-review", goals.goals_view(path))
        self.assertIn("updated", goals.goal_write(GOAL, path))
        goals.kernel_pass(path)
        self.assertNotIn("cull-review", goals.goals_view(path))

    def test_4d_compost_failure_preserves_goal(self):
        path = self.stack([FREE])
        real_open = open

        def guarded_open(name, mode="r", *args, **kwargs):
            if str(name).endswith("goals_compost.log") and "a" in mode:
                raise OSError("disk unavailable")
            return real_open(name, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=guarded_open):
            result = goals.goal_drop("attune", "solved", path)
        self.assertIn("compost log was not written", result)
        self.assertIn("(goal attune", goals.goals_view(path))

    def test_5_gamma_mood_average(self):
        path = self.stack([FREE])
        log = os.path.join(self.tmp.name, "updates.jsonl")
        t1 = "⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5⟩"
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "outbound", "text": "hi\n" + t1}) + "\n")
        goals.kernel_pass(path, log)
        t2 = "⋄⟨Cn:.8 C:.8 Ct:.8 I:.8 J:.8 A:.8 S:.8 Co:.8⟩"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "outbound", "text": "yo\n" + t2}) + "\n")
        goals.kernel_pass(path, log)
        # 0.69*0.5 + 0.31*0.8 = 0.593 per dimension
        self.assertIn("(mood Cn:0.593", goals.goals_view(path))
        gestalt = goals.affect_view(path)
        self.assertIn("gamma:0.69", gestalt)
        self.assertIn("Cn:0.593", gestalt)

    def test_decay_and_lti_math(self):
        path = self.stack([FREE])
        goals.kernel_pass(path)
        line = [l for l in goals.goals_view(path).splitlines()
                if l.startswith("(goal attune")][0]
        sti = float(line.split("sti:")[1].split()[0])
        lti = float(line.split("lti:")[1].split()[0])
        # file stores 4 decimal places
        self.assertAlmostEqual(sti, 0.9 * 0.95, places=3)
        self.assertAlmostEqual(lti, 0.5 + 0.05 * (0.855 - 0.5), places=3)

    def test_unknown_lines_preserved_and_writes_validated(self):
        path = self.stack([";; precious comment", FREE])
        goals.kernel_pass(path)
        self.assertIn(";; precious comment", goals.goals_view(path))
        self.assertTrue(goals.goal_write("(goal broken)", path)
                        .startswith("rejected"))
        self.assertIn("added", goals.goal_write(GOAL, path))
        self.assertIn("updated", goals.goal_write(GOAL, path))

    def test_legacy_records_migrate_and_canonical_duplicate_wins(self):
        legacy_only = ("(goal legacy Work sti:0.2 lti:0.4 vibes:(care) "
                       "blocked-by:none)")
        legacy_duplicate = ("(goal attune Old sti:0.8 lti:0.1 vibes:(old) "
                            "blocked-by:none)")
        path = self.stack([legacy_only, legacy_duplicate, FREE])
        result = goals.kernel_pass(path)
        self.assertIn("2 goals", result)
        view = goals.goals_view(path)
        self.assertEqual(view.count("(goal attune "), 1)
        self.assertIn("(goal attune Relationship", view)
        self.assertIn("(goal legacy Work", view)
        self.assertIn('last-verified:never note:""', view)

    def test_malformed_goal_record_fails_without_rewrite(self):
        path = self.stack([FREE, "(goal broken)"])
        before = goals.goals_view(path)
        self.assertIn("malformed goal record", goals.kernel_pass(path))
        self.assertEqual(goals.goals_view(path), before)
        self.assertIn("malformed goal record", goals.goal_write(GOAL, path))
        self.assertEqual(goals.goals_view(path), before)

    def test_invalid_number_is_rejected_without_exception(self):
        path = self.stack([])
        bad = FREE.replace("sti:0.9", "sti:1.2.3")
        self.assertTrue(goals.goal_write(bad, path).startswith("rejected"))

    def test_attention_uses_lti_when_sti_is_at_the_floor(self):
        high = FREE.replace("attune", "durable").replace("lti:0.5", "lti:0.9")
        low = FREE.replace("attune", "fresh").replace("lti:0.5", "lti:0.2")
        high = high.replace("sti:0.9", "sti:0.0009")
        low = low.replace("sti:0.9", "sti:0.0009")
        path = self.stack([low, high])
        ranking = goals.attention_view(path)
        self.assertLess(ranking.index("durable"), ranking.index("fresh"))

    def test_goal_write_unquoted_note_and_hyphenated_blocked(self):
        # command-channel form: no embedded quotes anywhere
        path = self.stack([])
        atom = ("(goal sti3 Work sti:0.7 lti:0.2 vibes:(SenseMaking) "
                "blocked-by:add-atom-space-error last-verified:never "
                "note:3-channel STI for Godel)")
        self.assertIn("added", goals.goal_write(atom, path))
        view = goals.goals_view(path)
        self.assertIn('note:"3-channel STI for Godel"', view)
        self.assertIn("blocked-by:add-atom-space-error", view)

    def test_9dim_trace_with_surprise_and_8dim_history_coexist(self):
        path = self.stack([FREE])
        log = os.path.join(self.tmp.name, "updates.jsonl")
        old8 = "⋄⟨Cn:.5 C:.5 Ct:.5 I:.5 J:.5 A:.5 S:.5 Co:.5⟩"
        new9 = "⋄⟨Cn:.8 C:.8 Ct:.8 I:.8 J:.8 A:.8 S:.8 Co:.8 Sp:.8⟩"
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "outbound", "text": "a\n" + old8}) + "\n")
        goals.kernel_pass(path, log)
        self.assertIn("Sp:0", goals.goals_view(path))   # absent dim stays 0
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "outbound", "text": "b\n" + new9}) + "\n")
        goals.kernel_pass(path, log)
        view = goals.goals_view(path)
        self.assertIn("(mood Cn:0.593", view)           # 0.69*.5+0.31*.8
        self.assertIn("Sp:0.248", view)                 # 0.69*0 +0.31*.8


if __name__ == "__main__":
    unittest.main()

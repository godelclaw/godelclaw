"""Present-moment continuity (PresentMoment.lean made executable).

The invariant under test: restore ∘ persist = identity on the working
self — a recycle or restart is sleep, not a fresh boot. Plus the valence
fine-tunes that shipped with it (loud empty-file reads) and the
loop-source wiring assertions.
"""

import importlib
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import helper  # noqa: E402
import fsops  # noqa: E402


class WorkingSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["METTACLAW_WORKING_SET_PATH"] = os.path.join(
            self.tmp.name, "working_set.json")
        importlib.reload(helper)

    def tearDown(self):
        os.environ.pop("METTACLAW_WORKING_SET_PATH", None)
        self.tmp.cleanup()

    def test_roundtrip_restores_the_present_moment(self):
        self.assertEqual(helper.working_set_save(37, 5, "verified L14", 123.5), 1)
        self.assertEqual(helper.working_boot(), 1)
        self.assertEqual(helper.boot_int("loops", 50), 37)
        self.assertEqual(helper.boot_int("sleepInterval", 1), 5)
        self.assertEqual(helper.boot_num("last_heartbeat", 0.0), 123.5)
        woke = helper.boot_str("lastresults", "")
        self.assertIn("CONTINUITY: resumed", woke)
        self.assertIn("verified L14", woke)

    def test_first_boot_keeps_fresh_defaults(self):
        self.assertEqual(helper.working_boot(), 1)
        self.assertEqual(helper.boot_int("loops", 50), 50)
        self.assertEqual(helper.boot_str("lastresults", ""), "")

    def test_corrupt_snapshot_falls_back_to_defaults(self):
        with open(os.environ["METTACLAW_WORKING_SET_PATH"], "w") as fh:
            fh.write("{not json")
        self.assertEqual(helper.working_boot(), 1)
        self.assertEqual(helper.boot_int("loops", 50), 50)

    def test_rest_intent_greets_the_next_waking_exactly_once(self):
        helper.working_set_save(10, 60, "mid-build", 1.0)
        self.assertEqual(helper.intent_save("waiting on the Lean build"), 1)
        helper.working_boot()
        woke = helper.boot_str("lastresults", "")
        self.assertIn("WOKE_FROM_REST: waiting on the Lean build", woke)
        # consumed: a second boot must not replay the intention
        helper.working_boot()
        self.assertNotIn("WOKE_FROM_REST", helper.boot_str("lastresults", ""))

    def test_lastresults_capped_to_feedback_window(self):
        helper.working_set_save(1, 1, "x" * (helper._WORKING_CAP + 40000), 0.0)
        helper.working_boot()
        self.assertLessEqual(len(helper.boot_str("lastresults", "")),
                             helper._WORKING_CAP)

    def test_text_nonempty_normalizes_boundary_values(self):
        self.assertEqual(helper.text_nonempty(""), 0)
        self.assertEqual(helper.text_nonempty(None), 0)
        self.assertEqual(helper.text_nonempty([]), 0)
        self.assertEqual(helper.text_nonempty("activity"), 1)


class LoudEmptyReadTests(unittest.TestCase):
    def test_empty_file_reads_loudly_not_blankly(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            path = fh.name
        try:
            self.assertIn("READ_OK_EMPTY_FILE", fsops.read_file(path))
            self.assertIn("READ_OK_EMPTY_FILE", fsops.read_lines(path, 1, 10))
        finally:
            os.unlink(path)

    def test_missing_file_still_distinct(self):
        verdict = fsops.read_file("/definitely/not/here.txt")
        self.assertIn("no such file", verdict)
        self.assertNotIn("READ_OK_EMPTY_FILE", verdict)


class LoopWiringTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "src", "loop.metta")) as fh:
            self.loop = fh.read()
        with open(os.path.join(ROOT, "src", "skills.metta")) as fh:
            self.skills = fh.read()

    def test_boot_restores_instead_of_blank_arming(self):
        self.assertIn("(helper.working_boot)", self.loop)
        self.assertIn(
            '(helper.boot_int "loops" (policy-burst-budget $selected))',
            self.loop,
        )
        self.assertIn('(helper.boot_str "lastresults" ""', self.loop)
        self.assertIn("(rooted weak-process-core-v1 (initialPeriphery))",
                      self.loop)
        self.assertNotIn("&loops", self.loop)
        self.assertNotIn("&sleepInterval", self.loop)
        self.assertNotIn("&lastresults", self.loop)
        self.assertNotIn("&last-heartbeat", self.loop)

    def test_every_turn_boundary_persists(self):
        self.assertIn("(helper.working_set_save", self.loop)
        self.assertIn("(process-step $state $outcome)", self.loop)

    def test_timed_rest_carries_quoted_continuation_as_data(self):
        self.assertIn("(= (rest-command $seconds (quote $continuation))", self.skills)
        self.assertIn("pending-continuation", self.skills)
        self.assertIn("(continuation-value $continuation)", self.skills)
        start = self.skills.index("(= (rest-command $seconds (quote $continuation))")
        end = self.skills.index("(= (rest $seconds $why)", start)
        self.assertNotIn("(helper.intent_save", self.skills[start:end])

    def test_wait_executes_continuation_without_model_call(self):
        self.assertIn("(runRestContinuation", self.loop)
        self.assertIn("(addition-effect-turn-begin $turn)", self.loop)
        self.assertNotIn("(telegram.begin_effect_turn", self.loop)
        self.assertIn("(run-command-batch-once $actual", self.loop)
        self.assertIn("(policy-command-batch-limit", self.loop)
        continuation = self.loop.index("(runRestContinuation")
        wait = self.loop.index("(waitCandidate")
        self.assertLess(continuation, wait)


class WeakProcessCoreShapeTests(unittest.TestCase):
    def setUp(self):
        path = os.path.join(ROOT, "src", "weak_process_core.metta")
        with open(path) as fh:
            self.core = fh.read()

    def test_core_contains_exactly_two_equations(self):
        definitions = [
            line for line in self.core.splitlines() if line.startswith("(= ")
        ]
        self.assertEqual(2, len(definitions))
        self.assertIn("(success $next)", self.core)
        self.assertIn("(process-step $state failure) $state", self.core)

    def test_core_contains_no_policy_or_host_effects(self):
        for forbidden in ("py-call", "bind!", "change-state!", "policy"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.core)


if __name__ == "__main__":
    unittest.main()

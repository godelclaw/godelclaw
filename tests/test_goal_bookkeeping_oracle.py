"""Generated public fixtures: characterize the UNCHANGED Python writer oracle.

Not native writer qualification. Every file is disposable and every captured
byte is generated here; no live/personal fixture text enters these receipts.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from goal_bookkeeping_support import ROOT, environment, receipt

sys.path.insert(0, str(ROOT / "src"))
import goals


def goal(name="alpha", sti="0.8", lti="0.3", verified="never", blocked="none"):
    return ('(goal %s Work sti:%s lti:%s vibes:(care sense) blocked-by:%s '
            'last-verified:%s note:"generated 日本 🙂 é")' % (name, sti, lti, blocked, verified))


def trace(value=".5", dims=9):
    return "⋄⟨" + " ".join(d + ":" + value for d in goals.MOOD_DIMS[:dims]) + "⟩"


class OracleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        env = environment(self.directory)
        env.update(METTACLAW_ENGINE="petta", METTACLAW_ACTIVE_ENGINE="petta")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime = mock.patch.object(goals, "_runtime_human_wake", None)
        runtime.start()
        self.addCleanup(runtime.stop)
        # Ledger integration is out of scope for this oracle characterization.
        # Its one permitted retire_goal boundary is counted, never contacted.
        retire = mock.patch.object(goals, "_retire_in_ledger", return_value=0)
        self.retire = retire.start()
        self.addCleanup(retire.stop)
        self.path = self.directory / "goals.metta"
        self.kernel = self.directory / "goals_kernel.json"
        self.log = self.directory / "updates.jsonl"
        self.compost = self.directory / "goals_compost.log"
        self.path.write_text(";; generated fixture\n" + goal() + "\n", encoding="utf-8")

    def snapshot(self, label, result=None):
        files = {}
        for name in ("goals.metta", "goals.metta.tmp", "goals_kernel.json",
                     "goals_kernel.json.tmp", "goals_compost.log", "updates.jsonl"):
            path = self.directory / name
            files[name] = base64.b64encode(path.read_bytes()).decode() if path.is_file() else None
        receipt("oracle", {"test": self.id(), "label": label, "result": result,
                           "files_base64": files, "scope": "Python oracle ONLY"})
        return files

    def test_generated_sequence_bytes_migration_flags_budget_wakes_and_compost(self):
        legacy = "(goal legacy Old sti:0.2 lti:0.1 vibes:() blocked-by:wait)"
        self.path.write_text("\n".join([";; generated fixture", legacy,
            goal("alpha", blocked="wait"), goal("alpha", sti="0.9", blocked="wait"),
            "(goal alpha Ignored sti:0.1 lti:0.1 vibes:() blocked-by:none)",
            "(goal-flag gone saturated)", "(goal-flag alpha saturated)",
            "(goal-budget 0.5)", "(mood Cn:0.1)"]) + "\n", encoding="utf-8")
        self.kernel.write_text(json.dumps({"streaks": {"gone": {"blocked": "x", "streak": 9}},
                                           "log_offset": 0, "mood": {}}))
        self.log.write_text("bad json\n" + json.dumps({"kind": "inbound", "text": trace(".9")}) + "\n" +
                            json.dumps({"kind": "outbound", "text": "日本 " + trace(".2", 5) +
                                        trace(".5", 6) + trace(".8")}, ensure_ascii=False) + "\n")
        for index in range(3):
            result = goals.kernel_pass()
            self.snapshot("pass-%d" % index, result)
            self.assertNotIn("error", result)
        text = self.path.read_text()
        self.assertEqual(text.count("(goal alpha "), 1)
        self.assertLess(text.index("(goal legacy "), text.index("(goal alpha "))
        self.assertIn('(goal-flag alpha stuck)\n(goal-flag legacy stuck)', text)
        self.assertNotIn("gone", text)
        self.assertTrue(text.startswith(";; generated fixture\n\n(goal-budget 0.5)\n"))
        state = json.loads(self.kernel.read_bytes())
        self.assertEqual(list(state), ["streaks", "log_offset", "mood"])
        self.assertEqual(list(state["mood"]), list(goals.MOOD_DIMS))
        self.assertEqual(state["log_offset"], self.log.stat().st_size)
        self.assertEqual(state["mood"]["Cn"], 0.69 * 0.5 + (1 - 0.69) * 0.8)
        self.assertEqual(self.kernel.read_bytes(), json.dumps(state).encode())
        first = goals.human_wake("generated-batch")
        before = self.snapshot("fresh", first)
        again = goals.human_wake("generated-batch")
        self.assertEqual(again, {"fresh": 0, "maintenance_applied": 0,
                                 "message": "goal-kernel: human wake already applied"})
        self.assertEqual(before, self.snapshot("repeated", again))
        self.assertEqual(list(json.loads(self.kernel.read_bytes())),
                         ["streaks", "log_offset", "mood", "last_human_wake"])
        updated = goals.goal_write(goal("alpha", verified="2026-01-02T03:04:05"))
        self.assertEqual(updated, "goal alpha updated")
        self.snapshot("updated", updated)
        # Freeze ONLY compost comparison; local native clock checked separately.
        with mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"):
            result = goals.goal_drop("alpha", "solved")
        self.assertEqual(result, "goal alpha composted (solved) — remember a compost note")
        self.assertEqual(self.compost.read_bytes(),
            ('2026-01-02T03:04:05 solved ' + goal("alpha", verified="2026-01-02T03:04:05") + "\n").encode())
        self.assertNotIn("alpha", self.path.read_text())
        self.snapshot("drop", result)

    def test_validation_and_exact_malformed_messages(self):
        for budget, expected in (("0.49", "goal-budget must be in [0.5, 10]"),
                                 ("10.01", "goal-budget must be in [0.5, 10]"),
                                 ("0.5", "budget set to 0.5"), ("10", "budget set to 10")):
            result = goals.goal_write("(goal-budget %s)" % budget)
            self.assertEqual(result, expected)
            self.snapshot("budget-" + budget, result)
        self.assertEqual(goals.goal_drop("missing", "retire"), "no goal named missing")
        self.assertEqual(goals.goal_drop("alpha", " \t"),
                         "rejected: give a reason, or 'solved' for a verified goal")
        self.assertEqual(goals.goal_drop("alpha", "solved"),
            "rejected: last-verified is 'never' — verify progress (goal-write with a last-verified timestamp) "
            "before claiming solved, or retire it with another reason")
        self.assertEqual(goals.goal_write("(goal broken)"),
            'rejected: expected (goal <name> <area> sti:<0-1> lti:<0-1> vibes:(..) '
            'blocked-by:<none|hyphenated-token> last-verified:<never|timestamp> note:free text) '
            'on one line — no quotes needed, hyphenate blocked-by')
        self.path.write_text(goal() + "\n(goal broken)\n")
        before = self.path.read_bytes()
        for function, args, expected in (
            (goals.kernel_pass, (), "goal-kernel error: malformed goal record at line 2"),
            (goals.goal_write, (goal(),), "goal-write rejected: malformed goal record at line 2"),
            (goals.goal_drop, ("alpha", "retire"), "goal-drop rejected: malformed goal record at line 2")):
            result = function(*args)
            self.assertEqual(result, expected)
            self.assertEqual(self.path.read_bytes(), before)
            self.snapshot("malformed", result)

    def test_first_write_failure_and_nonobject_log_error(self):
        before = self.path.read_bytes()
        real_open = open
        def fail(path, mode="r", *args, **kwargs):
            if str(path).endswith("goals.metta.tmp") and mode == "w":
                raise OSError("generated first write failure")
            return real_open(path, mode, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=fail):
            self.assertEqual(goals.kernel_pass(), "goal-kernel error: generated first write failure")
            with self.assertRaisesRegex(OSError, "generated first write failure"):
                goals.goal_write(goal())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.kernel.exists())
        self.snapshot("first-write-failure")
        self.log.write_text("[]\n")
        result = goals.kernel_pass()
        self.assertEqual(result, "goal-kernel error: 'list' object has no attribute 'get'")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.kernel.exists())
        self.snapshot("valid-json-wrong-shape", result)

    def test_mood_byte_offsets_invalid_lines_shrink_and_mid_codepoint(self):
        invalid = b'not json\n{"kind":"outbound","text":"bad \xff"}\n'
        self.log.write_bytes(invalid + (json.dumps({"kind": "outbound", "text": "🙂 " + trace(".1")},
                                                   ensure_ascii=False) + "\n").encode())
        samples, offset = goals._mood_samples(str(self.log), 0)
        self.assertEqual(len(samples), 1)
        self.assertEqual(offset, self.log.stat().st_size)
        self.assertEqual(goals._mood_samples(str(self.log), offset), ([], offset))
        # Shrink below saved byte offset starts at zero, not EOF.
        self.log.write_text(json.dumps({"kind": "outbound", "text": trace(".2", 6)}) + "\n")
        samples, shrunk = goals._mood_samples(str(self.log), offset)
        self.assertEqual(len(samples), 1)
        self.assertLess(shrunk, offset)
        self.assertEqual(samples[0], {d: 0.2 for d in goals.MOOD_DIMS[:6]})
        # Arbitrary byte seek in a UTF-8 character must not become a character offset.
        self.log.write_bytes("🙂\n".encode() + self.log.read_bytes())
        self.assertEqual(goals._mood_samples(str(self.log), 2)[0], samples)
        self.snapshot("byte-offsets", {"old_offset": offset, "shrunk_offset": shrunk, "samples": samples})

    def test_each_rename_and_failure_between_writes_no_transaction(self):
        self.kernel.write_text('{"streaks": {}, "log_offset": 0, "mood": {}}')
        original_goals, original_kernel = self.path.read_bytes(), self.kernel.read_bytes()
        real_replace = os.replace
        events = []

        def replace(source, target):
            target = Path(target)
            self.assertEqual(Path(source), Path(str(target) + ".tmp"))
            self.assertTrue(Path(source).is_file())
            if target == self.path:
                self.assertEqual(target.read_bytes(), original_goals)
                self.assertEqual(self.kernel.read_bytes(), original_kernel)
            else:
                self.assertNotEqual(self.path.read_bytes(), original_goals)
                self.assertEqual(target.read_bytes(), original_kernel)
            events.append(target.name)
            return real_replace(source, target)

        with mock.patch.object(goals.os, "replace", side_effect=replace):
            self.assertNotIn("error", goals.kernel_pass())
        self.assertEqual(events, ["goals.metta", "goals_kernel.json"])
        self.snapshot("atomic-per-file-not-pair")
        for failing in ("goals.metta", "goals_kernel.json"):
            self.path.write_bytes(original_goals)
            self.kernel.write_bytes(original_kernel)
            def fail(source, target):
                if Path(target).name == failing:
                    raise OSError("generated rename failure")
                return real_replace(source, target)
            with mock.patch.object(goals.os, "replace", side_effect=fail):
                result = goals.kernel_pass()
            self.assertEqual(result, "goal-kernel error: generated rename failure")
            self.assertEqual(self.kernel.read_bytes(), original_kernel)
            self.assertEqual(self.path.read_bytes() == original_goals, failing == "goals.metta")
            self.assertTrue((self.directory / (failing + ".tmp")).exists())
            self.snapshot("rename-failure-" + failing, result)
            for name in ("goals.metta.tmp", "goals_kernel.json.tmp"):
                (self.directory / name).unlink(missing_ok=True)

    def test_durable_failure_runtime_digest_blocks_freshness_not_write_attempt(self):
        real_open = open
        calls = []
        def fail(path, mode="r", *args, **kwargs):
            if str(path).endswith("goals_kernel.json.tmp") and mode == "w":
                calls.append(str(path))
                raise OSError("generated durable failure")
            return real_open(path, mode, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=fail):
            first = goals.human_wake("durable-failure")
            after_first = self.path.read_bytes()
            repeated = goals.human_wake("durable-failure")
        self.assertEqual(first, {"fresh": 1, "maintenance_applied": 0,
                                "message": "goal-kernel error: generated durable failure"})
        self.assertEqual(repeated, dict(first, fresh=0))
        self.assertEqual(len(calls), 2)
        # Critical oracle fact: digest suppresses re-arming, NOT the kernel
        # attempt, which occurs before the in-process digest test.
        self.assertNotEqual(self.path.read_bytes(), after_first)
        self.assertFalse(self.kernel.exists())
        self.snapshot("explicit-second-presented-wake-not-automatic-replay", [first, repeated])

    def test_append_failure_and_append_success_followed_by_goal_rename_failure(self):
        before = self.path.read_bytes()
        real_open = open
        def fail(path, mode="r", *args, **kwargs):
            if str(path).endswith("goals_compost.log") and mode == "a":
                raise OSError("generated append failure")
            return real_open(path, mode, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=fail):
            result = goals.goal_drop("alpha", "retire")
        self.assertEqual(result, "goal-drop failed: compost log was not written: generated append failure")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.compost.exists())
        self.snapshot("append-failure", result)
        with mock.patch.object(goals.time, "strftime", return_value="2026-01-02T03:04:05"), \
             mock.patch.object(goals.os, "replace", side_effect=OSError("generated rename failure")):
            with self.assertRaisesRegex(OSError, "generated rename failure"):
                goals.goal_drop("alpha", "retire")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.compost.read_bytes(), ("2026-01-02T03:04:05 retire " +
            goals._fmt_goal(goals._parse(self.path.read_text().splitlines())[0][0]) + "\n").encode())
        self.retire.assert_not_called()
        self.snapshot("uncertain-drop-no-replay")


if __name__ == "__main__":
    unittest.main()

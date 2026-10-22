"""Offline fixtures for the public-work projector in effect_receipts.

No provider, Telegram, live agent state or service is used.  The sink is a
fixture that mirrors the worklog's rules (enabled flag and epoch checked at
publication); a second class runs the same cases against the real installed
worklog library when it is present.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import effect_receipts  # noqa: E402

LIBRARY = pathlib.Path.home() / ".local/lib/godel-worklog-observer"
SECRET_ARGUMENT = "SECRET_ARGUMENT_7f3"
SECRET_RESULT = "SECRET_RESULT_9c1"
COMMAND = ["shell", "echo " + SECRET_ARGUMENT]


class FakeSink:
    """Same publication rule as worklog.transact: enabled and epoch."""

    def __init__(self):
        self.enabled, self.epoch, self.notes = True, 1, []
        self.fail_add = self.fail_status = None

    def toggle(self, enabled):
        self.enabled = enabled
        self.epoch += 1

    def status(self):
        if self.fail_status:
            raise self.fail_status
        return self.enabled, self.epoch

    def add(self, text, epoch):
        if self.fail_add:
            raise self.fail_add
        if epoch != self.epoch or not self.enabled:
            return False
        self.notes.append(text)
        return True


class Base:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = pathlib.Path(self.temporary.name) / "receipts.jsonl"
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_PUBLIC_TOOL_RECEIPTS": "1",
            "METTACLAW_EFFECT_RECEIPT_PATH": str(self.path),
            "METTACLAW_OBSERVED_STRATEGY": "0",
        })
        environment.start()
        self.addCleanup(environment.stop)
        effect_receipts._published.clear()
        effect_receipts._in_flight.clear()
        effect_receipts._captured.clear()
        self.make_sink()

    def work(self, turn=3, disposition="returned", command=COMMAND,
             result=SECRET_RESULT):
        """Capture before the work, then record its return."""
        effect_receipts.capture_public_epoch(turn)
        return effect_receipts.record_command(
            turn, disposition, command, result)

    def receipts(self):
        return self.path.read_text(encoding="utf-8").splitlines()


class ProjectorTest(unittest.TestCase):
    def test_event_has_only_the_four_allowlisted_fields(self):
        event = effect_receipts.public_event(
            3, "returned", COMMAND, SECRET_RESULT, 1_700_000_000)
        self.assertEqual(sorted(event),
                         ["at", "disposition", "receipt_id", "tool"])
        self.assertEqual(event["tool"], "shell")
        self.assertEqual(event["at"], "2023-11-14T22:13:20+00:00")
        rendered = json.dumps(event) + effect_receipts.public_text(event)
        self.assertNotIn(SECRET_ARGUMENT, rendered)
        self.assertNotIn(SECRET_RESULT, rendered)

    def test_returned_is_not_success(self):
        event = effect_receipts.public_event(
            1, "returned", COMMAND, "partial(failed: no chat)", 1.0)
        self.assertEqual(event["disposition"], "returned")
        self.assertNotIn("success", json.dumps(event))
        self.assertIn("not a success verdict",
                      effect_receipts.public_text(event))

    def test_unknown_tool_or_disposition_is_dropped(self):
        self.assertIsNone(effect_receipts.public_event(
            1, "returned", ["not-a-tool", "x"], "r", 1.0))
        self.assertIsNone(effect_receipts.public_event(
            1, "success", COMMAND, "r", 1.0))
        self.assertIsNone(effect_receipts.public_event(
            1, "returned", "free text, not a command", "r", 1.0))
        self.assertIsNone(effect_receipts.public_event(
            1, "returned", [], "r", 1.0))

    def test_json_encoded_command_and_stable_id(self):
        encoded = json.dumps(COMMAND)
        one = effect_receipts.public_event(2, "returned", encoded, "r", 1.0)
        two = effect_receipts.public_event(2, "returned", encoded, "r", 99.0)
        self.assertEqual(one["tool"], "shell")
        self.assertEqual(one["receipt_id"], two["receipt_id"])
        other = effect_receipts.public_event(2, "returned", encoded, "s", 1.0)
        self.assertNotEqual(one["receipt_id"], other["receipt_id"])

    def test_withheld_and_deferred_stay_withheld_and_deferred(self):
        for disposition in ("withheld", "deferred"):
            event = effect_receipts.public_event(
                1, disposition, ["pin", "x"], "operator stopped cognition", 1.0)
            self.assertEqual(event["disposition"], disposition)

    def test_tool_allowlist_covers_installed_observer_tools(self):
        if not (LIBRARY / "observer.py").exists():
            self.skipTest("godel-worklog-observer not installed")
        sys.path.insert(0, str(LIBRARY))
        try:
            import observer
        finally:
            sys.path.remove(str(LIBRARY))
        self.assertLessEqual(observer.TOOLS, effect_receipts.PUBLIC_TOOLS)


class ProjectorWiringCases(Base, unittest.TestCase):
    def make_sink(self):
        self.sink = FakeSink()
        patch = mock.patch.object(effect_receipts, "_sink",
                                  lambda: self.sink)
        patch.start()
        self.addCleanup(patch.stop)

    def test_one_command_one_event_and_receipt_still_written(self):
        self.assertEqual(self.work(), 1)
        self.assertEqual(len(self.sink.notes), 1)
        self.assertIn("Activity · shell", self.sink.notes[0])
        self.assertIn("Receipt: returned", self.sink.notes[0])
        self.assertEqual(len(self.receipts()), 1)

    def test_allowlist_no_argument_or_result_bytes_in_output(self):
        self.work()
        self.work(4, "exception", ["write-file", "p", SECRET_ARGUMENT],
                  "Traceback " + SECRET_RESULT)
        self.assertEqual(len(self.sink.notes), 2)
        for note in self.sink.notes:
            self.assertNotIn(SECRET_ARGUMENT, note)
            self.assertNotIn(SECRET_RESULT, note)
            self.assertNotIn("echo", note)
        # The private ledger is unchanged and still holds the evidence.
        self.assertIn(SECRET_RESULT, self.path.read_text(encoding="utf-8"))

    def test_off_switch_drops_event(self):
        self.sink.toggle(False)
        self.assertEqual(self.work(), 1)
        self.assertEqual(self.sink.notes, [])

    def test_off_after_capture_is_rejected(self):
        effect_receipts.capture_public_epoch(3)
        self.sink.toggle(False)
        self.assertEqual(
            effect_receipts.record_command(3, "returned", COMMAND, "r"), 1)
        self.assertEqual(self.sink.notes, [])

    def test_off_on_cycle_rejects_event_with_old_epoch(self):
        effect_receipts.capture_public_epoch(3)
        self.sink.toggle(False)
        self.sink.toggle(True)       # enabled again, but a new epoch
        effect_receipts.record_command(3, "returned", COMMAND, "r")
        self.assertEqual(self.sink.notes, [])
        self.work(4)                 # fresh capture after the cycle works
        self.assertEqual(len(self.sink.notes), 1)

    def test_event_without_captured_epoch_is_dropped(self):
        effect_receipts.record_command(3, "returned", COMMAND, "r")
        self.assertEqual(self.sink.notes, [])

    def test_duplicate_replay_gives_one_event(self):
        self.work()
        self.work()
        self.assertEqual(len(self.sink.notes), 1)
        self.assertEqual(len(self.receipts()), 2)   # ledger stays append-only

    def test_rejected_event_may_be_published_by_later_replay(self):
        effect_receipts.capture_public_epoch(3)
        self.sink.toggle(False)
        self.sink.toggle(True)
        effect_receipts.record_command(3, "returned", COMMAND, "r")
        self.assertEqual(self.sink.notes, [])
        effect_receipts.capture_public_epoch(3)
        effect_receipts.record_command(3, "returned", COMMAND, "r")
        self.assertEqual(len(self.sink.notes), 1)

    def test_sink_add_failure_does_not_fail_record_command(self):
        self.sink.fail_add = OSError("display broken")
        self.assertEqual(self.work(), 1)
        self.assertEqual(len(self.receipts()), 1)
        self.sink.fail_add = None
        self.work()                  # not marked published after a failure
        self.assertEqual(len(self.sink.notes), 1)

    def test_sink_status_failure_does_not_fail_record_command(self):
        self.sink.fail_status = RuntimeError("no state")
        self.assertEqual(self.work(), 1)
        self.assertEqual(self.sink.notes, [])
        self.assertEqual(len(self.receipts()), 1)

    def test_sink_construction_failure_does_not_fail_record_command(self):
        with mock.patch.object(effect_receipts, "_sink",
                               side_effect=ImportError("no library")):
            self.assertEqual(self.work(), 1)
        self.assertEqual(len(self.receipts()), 1)

    def test_publication_happens_after_the_receipt_is_durable(self):
        seen = []
        original = self.sink.add

        def add(text, epoch):
            seen.append(len(self.receipts()))
            return original(text, epoch)
        self.sink.add = add
        self.work()
        self.assertEqual(seen, [1])

    def test_no_receipt_path_means_no_publication(self):
        with mock.patch.dict(os.environ, {"METTACLAW_EFFECT_RECEIPT_PATH": ""}):
            self.assertEqual(self.work(), 0)
        self.assertEqual(self.sink.notes, [])

    def test_reporting_does_not_report_itself(self):
        original = self.sink.add

        def add(text, epoch):
            effect_receipts.capture_public_epoch(9)
            effect_receipts.record_command(9, "returned", ["shell", "x"], "r")
            return original(text, epoch)
        self.sink.add = add
        self.work()
        self.assertEqual(len(self.sink.notes), 1)

    def test_stopped_command_is_withheld_never_returned(self):
        self.work(5, "withheld", ["shell", "rm x"], "operator stopped cognition")
        self.assertEqual(len(self.sink.notes), 1)
        self.assertIn("Activity · shell", self.sink.notes[0])
        self.assertIn("Receipt: withheld", self.sink.notes[0])
        self.assertIn("did not run", self.sink.notes[0])
        self.assertNotIn("returned", self.sink.notes[0])

    def test_unknown_tool_is_not_published_but_is_recorded(self):
        self.assertEqual(self.work(6, "returned", ["mystery", "x"], "r"), 1)
        self.assertEqual(self.sink.notes, [])
        self.assertEqual(len(self.receipts()), 1)

    def test_no_sink_configured_is_a_noop(self):
        with mock.patch.object(effect_receipts, "_sink", lambda: None):
            self.assertEqual(self.work(), 1)


class BuiltinWorklogCases(Base, unittest.TestCase):
    """The same rules through the release-owned worklog sharing state."""

    def make_sink(self):
        self.root = pathlib.Path(self.temporary.name) / "worklog"
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_PUBLIC_WORK_DIR": str(self.root),
            "METTACLAW_PUBLIC_WORK_LIB": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.sink = effect_receipts._sink()

    def switch(self, action):
        self.sink._module.transact(self.root, action)

    def notes(self):
        log = self.root / "notes.jsonl"
        if not log.exists():
            return []
        return [json.loads(line)["text"] for line in
                log.read_text(encoding="utf-8").splitlines()]

    def test_default_off_drops_event(self):
        self.assertEqual(self.work(), 1)
        self.assertEqual(self.notes(), [])

    def test_on_publishes_one_metadata_note_and_dedupes(self):
        self.switch("on")
        self.work()
        self.work()
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Activity · shell\n"))
        self.assertIn("Receipt: returned", notes[0])
        self.assertNotIn(SECRET_ARGUMENT, notes[0])
        self.assertNotIn(SECRET_RESULT, notes[0])

    def test_off_after_capture_and_off_on_cycle_are_rejected(self):
        self.switch("on")
        effect_receipts.capture_public_epoch(3)
        self.switch("off")
        effect_receipts.record_command(3, "returned", COMMAND, "r")
        self.assertEqual(self.notes(), [])
        self.switch("on")
        effect_receipts.capture_public_epoch(4)
        self.switch("off")
        self.switch("on")
        effect_receipts.record_command(4, "returned", COMMAND, "r2")
        self.assertEqual(self.notes(), [])
        self.work(5)
        self.assertEqual(len(self.notes()), 1)

    def test_broken_library_does_not_fail_record_command(self):
        self.switch("on")
        with mock.patch.dict(os.environ,
                             {"METTACLAW_PUBLIC_WORK_LIB": "/nonexistent"}):
            self.assertEqual(self.work(), 1)
        self.assertEqual(len(self.receipts()), 1)

    def test_note_structure_survives_but_control_characters_do_not(self):
        self.switch("on")
        enabled, epoch = self.sink.status()
        self.assertTrue(enabled)
        self.assertTrue(self.sink.add("Title\n- item\x1b\u202e", epoch))
        self.assertEqual(self.notes(), ["Title\n- item  "])


if __name__ == "__main__":
    unittest.main(verbosity=2)

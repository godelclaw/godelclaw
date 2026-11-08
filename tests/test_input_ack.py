import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import input_ack


class InputAcknowledgementTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "1",
                                          "METTACLAW_INPUT_ACK_SECONDS": "15"})
        env.start(); self.addCleanup(env.stop)
        state = mock.patch.object(input_ack, "_pending", {})
        state.start(); self.addCleanup(state.stop)
        notices = mock.patch.object(input_ack, "_last_notice", {})
        notices.start(); self.addCleanup(notices.stop)
        self.now = 100.0
        clock = mock.patch.object(input_ack.time, "monotonic", side_effect=lambda: self.now)
        clock.start(); self.addCleanup(clock.stop)

    def test_commit_reads_report_exactly_the_processed_ids(self):
        import tempfile
        import native_ledger
        with tempfile.TemporaryDirectory() as directory:
            native_ledger.start(self, directory)
            input_ack.processed_update_ids(["one"])
            self.assertEqual(input_ack.already_committed("one"), 1)
            self.assertEqual(input_ack.already_committed("two"), 0)

    def test_notice_waits_then_keeps_the_same_identity_across_lost_reply_and_restart(self):
        key = lambda: input_ack.key("input-1", "42.0", "message", "operator")
        self.assertEqual(key(), "")
        self.now += 15
        original = key()
        self.assertTrue(original.startswith("42.0."))
        self.assertEqual(key(), original)
        input_ack.submitted("input-1")
        self.assertEqual(key(), "")
        input_ack._pending = {}
        input_ack._last_notice = {}
        self.assertEqual(key(), "")
        self.now += 15
        self.assertEqual(key(), original)

    def test_other_roles_commands_and_upstream_profiles_get_no_notice(self):
        for kind, role in [("message", "peer"), ("message", "bot"), ("callback", "operator")]:
            self.assertEqual(input_ack.key("t", "42.0", kind, role), "")
        with mock.patch.dict(os.environ, {"METTACLAW_OBSERVED_STRATEGY": "0"}):
            self.assertEqual(input_ack.key("t", "42.0", "message", "operator"), "")
        self.assertEqual(input_ack._pending, {})

    def test_pending_notices_are_retained_and_consumed_tasks_are_removed(self):
        for n in range(300):
            input_ack.key(str(n), "42.0", "message", "operator")
        self.assertEqual(len(input_ack._pending), 300)
        input_ack.begin_scan([["0", "body"]])
        self.assertEqual(list(input_ack._pending), ["0"])

    def test_partial_pages_do_not_forget_unseen_pending_work(self):
        with mock.patch.object(input_ack, "_scan_after", ""), mock.patch.object(input_ack, "_scan_seen", set()):
            for n in range(600):
                input_ack.key(str(n), "42.0", "message", "operator")
            first = [[str(n), "[]"] for n in range(256)] + [["runtime-scan-unavailable", "[]"]]
            input_ack.begin_scan(first, first)
            self.assertEqual(input_ack.scan_after(), "255")
            self.assertEqual(len(input_ack._pending), 600)
            second = [[str(n), "[]"] for n in range(256, 512)] + [["runtime-scan-unavailable", "[]"]]
            input_ack.begin_scan(second, second)
            last = [[str(n), "[]"] for n in range(512, 600)]
            input_ack.begin_scan(last, last)
            self.assertEqual(input_ack.scan_after(), "")
            self.assertEqual(len(input_ack._pending), 600)

    def test_lane_cannot_change_after_capture(self):
        input_ack.key("t", "42.0", "message", "operator")
        self.now += 15
        self.assertEqual(input_ack.key("t", "84.0", "message", "operator"), "")

    def test_disabled_and_invalid_delays(self):
        for value in ["0", "-1", "nan", "inf"]:
            with mock.patch.dict(os.environ, {"METTACLAW_INPUT_ACK_SECONDS": value}):
                self.assertEqual(input_ack.key("t", "42.0", "message", "operator"), "")

    def test_notices_are_limited_per_lane_without_blocking_other_lanes(self):
        for task, lane in [("a", "42.0"), ("b", "42.0"), ("c", "84.0")]:
            input_ack.key(task, lane, "message", "operator")
        self.now += 15
        self.assertTrue(input_ack.key("a", "42.0", "message", "operator"))
        input_ack.submitted("a")
        self.assertEqual(input_ack.key("b", "42.0", "message", "operator"), "")
        self.assertTrue(input_ack.key("c", "84.0", "message", "operator"))
        self.now += 60
        self.assertTrue(input_ack.key("b", "42.0", "message", "operator"))


if __name__ == "__main__":
    unittest.main()

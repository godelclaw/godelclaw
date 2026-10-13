"""New input queues; only the operator stop interrupts emitted calls."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "src"))
sys.path.insert(0, os.fspath(ROOT / "channels"))

import lifecycle  # noqa: E402
import telegram  # noqa: E402

OLD, NEW = "111000111", "222000222"


class ConversationFreshnessTest(unittest.TestCase):
    def setUp(self):
        self.saved = {name: getattr(telegram, name) for name in (
            "_effect_turn", "_effect_activity_epoch", "_pending_messages",
            "_prepared_messages", "_primary_chat_id", "_reply_chat_id",
            "_last_chat_id")}
        seen = [(OLD, "read before the turn", False, "full", 1)]
        telegram._prepared_messages = list(seen)
        telegram._pending_messages = seen + [
            (NEW, "arrived during the turn", False, "full", 2)]
        telegram._effect_turn = "5"
        telegram._effect_activity_epoch = 1
        telegram._primary_chat_id = ""
        telegram._reply_chat_id = OLD
        telegram._last_chat_id = OLD
        self.latch = mock.patch.object(
            lifecycle, "cognition_enabled", return_value=True)
        self.latch.start()

    def tearDown(self):
        self.latch.stop()
        for name, value in self.saved.items():
            setattr(telegram, name, value)

    def unaffected(self, *command):
        return telegram.effect_command_unaffected(5, list(command))

    def test_send_into_another_chat_runs(self):
        self.assertEqual(self.unaffected("send-telegram-chat", OLD, "hi"), 1)
        self.assertEqual(self.unaffected("send", "hi"), 1)

    def test_unread_input_does_not_discard_an_emitted_reply(self):
        self.assertEqual(self.unaffected("send-telegram-chat", NEW, "hi"), 1)
        telegram._reply_chat_id = NEW
        self.assertEqual(self.unaffected("send", "hi"), 1)
        self.assertEqual(self.unaffected("send-file", "/tmp/x", "c"), 1)

    def test_commands_that_do_not_speak_run(self):
        for command in (("pin", "state"), ("query", "q"), ("shell", "ls"),
                        ("remember", "fact")):
            with self.subTest(command=command):
                self.assertEqual(self.unaffected(*command), 1)

    def test_rest_is_not_rejected_by_unread_input(self):
        self.assertEqual(self.unaffected("rest", 7200, "later"), 1)
        telegram._pending_messages = list(telegram._prepared_messages)
        self.assertEqual(self.unaffected("rest", 7200, "later"), 1)

    def test_operator_latch_still_stops_everything(self):
        with mock.patch.object(lifecycle, "cognition_enabled",
                               return_value=False):
            self.assertEqual(self.unaffected("pin", "state"), 0)
            self.assertEqual(
                self.unaffected("send-telegram-chat", OLD, "hi"), 0)

    def test_direct_tool_use_needs_no_registration(self):
        self.assertEqual(
            telegram.effect_command_unaffected(6, ["pin", "state"]), 1)


if __name__ == "__main__":
    unittest.main()

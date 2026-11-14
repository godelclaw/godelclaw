"""What the MeTTa client publishes beside the health file a monitor reads, as
channels/telegram.py takes it up: its own process's values only. The client
writes the health file itself (channels/telegram.metta)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "channels"))
import telegram  # noqa: E402


class PublishedStateTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "telegram-health.json"
        environment = mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_HEALTH_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_the_client_file_sits_beside_the_health_file(self):
        self.assertEqual(telegram._client_path(), str(self.path)[:-5] + ".client.json")
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_HEALTH_PATH": "/run/x/health"}):
            self.assertEqual(telegram._client_path(), "/run/x/health.client.json")

    def test_values_another_process_published_are_not_taken_up(self):
        defaults = dict(telegram._published)
        self.addCleanup(telegram._published.update, defaults)
        client = Path(telegram._client_path())
        client.write_text(json.dumps({"pid": os.getpid() + 1, "pending_count": 4, "reply_chat": "77"}))
        telegram._client_seen = None
        self.assertEqual(telegram.reply_chat_id(), defaults["_reply_chat_id"])
        client.write_text(json.dumps({"pid": os.getpid(), "reply_chat": "77", "batch_update_ids": "5,6",
                                      "context_epoch": 3, "activity_epoch": 4, "context_frontier": 12,
                                      "titles": {"77": "Lab"}}))
        telegram._client_seen = None
        self.assertEqual((telegram.reply_chat_id(), telegram.current_batch_update_ids(),
                          telegram._value("_context_activity_epoch"), telegram._value("_activity_epoch"),
                          telegram._value("_context_frontier_bytes")),
                         ("77", {"5", "6"}, 3, 4, 12))
        # A test's assignment stands for the module's own code too.
        with mock.patch.object(telegram, "_reply_chat_id", "11", create=True):
            self.assertEqual(telegram._reply_target(), "11")
            self.assertEqual(telegram.reply_chat_id(), "11")
        self.assertEqual(telegram._reply_target(), "77")
        telegram._client_seen = None


if __name__ == "__main__":
    unittest.main()

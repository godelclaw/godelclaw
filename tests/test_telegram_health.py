"""The health file a monitor reads: channels/telegram.py merges its facts into
what the MeTTa client wrote, under the file's lock, and loses none."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT / "channels"))
import telegram  # noqa: E402


class HealthMergeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "telegram-health.json"
        environment = mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_HEALTH_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_python_facts_join_the_clients_without_replacing_them(self):
        self.path.write_text(json.dumps({"loop_status": "waiting", "waiting_until": 1794000000.5,
                                         "poll_status": "ok", "last_poll_ok_at": 1793999999.0}))
        telegram._health_update(client="metta")
        state = self.read()
        self.assertEqual((state["loop_status"], state["waiting_until"], state["poll_status"], state["client"]),
                         ("waiting", 1794000000.5, "ok", "metta"))
        self.assertIn("observed_at", state)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        # A later write gives only its own facts: an earlier one is not replayed.
        telegram._health_update(running=True, poll_status="starting")
        self.path.write_text(json.dumps(dict(self.read(), poll_status="ok")))
        telegram._health_update(running=False)
        self.assertEqual((self.read()["poll_status"], self.read()["running"]), ("ok", False))

    def test_unreadable_or_foreign_content_starts_a_fresh_record(self):
        for content in ("{torn", "[1, 2]", ""):
            with self.subTest(content=content):
                self.path.write_text(content)
                telegram._health_update(running=True)
                self.assertEqual(self.read()["running"], True)

    def test_concurrent_writers_lose_no_fact(self):
        threads = [threading.Thread(target=telegram._health_update, kwargs={"field_%d" % i: i})
                   for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        state = self.read()
        self.assertEqual({key: state[key] for key in state if key.startswith("field_")},
                         {"field_%d" % i: i for i in range(24)})
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir() if p.name.startswith(".telegram-health-")), [])

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
        with mock.patch.dict(telegram._chat_titles, {}, clear=True):
            self.assertEqual((telegram.reply_chat_id(), telegram.current_batch_update_ids(),
                              telegram._value("_context_activity_epoch"), telegram._value("_activity_epoch"),
                              telegram._value("_context_frontier_bytes")),
                             ("77", {"5", "6"}, 3, 4, 12))
            self.assertEqual(telegram._chat_titles, {"77": "Lab"})
        # A test's assignment stands for the module's own code too.
        with mock.patch.object(telegram, "_reply_chat_id", "11", create=True):
            self.assertEqual(telegram._reply_target(), "11")
            self.assertEqual(telegram.reply_chat_id(), "11")
        self.assertEqual(telegram._reply_target(), "77")
        telegram._client_seen = None


if __name__ == "__main__":
    unittest.main()

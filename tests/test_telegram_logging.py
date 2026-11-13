#!/usr/bin/env python3
"""What channels/telegram.py still does for the Python code beside the MeTTa
client: extracts and formats updates for the mode hosts, and routes plain
replies. Attachments and uploads are native (tests/unit/telegram_files.metta);
the client's own intake log and its one-send-per-turn rule are tested on the
MeTTa client by tests/telegram_send_and_intake_probe.metta."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channels"))
import telegram  # noqa: E402


class TelegramModuleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = pathlib.Path(directory.name)
        # No client file of another test or process is taken up.
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_TELEGRAM_HEALTH_PATH": str(self.directory / "health.json")})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID", None)
        state = mock.patch.multiple(telegram, _client_seen=None)
        state.start()
        self.addCleanup(state.stop)
        published = mock.patch.dict(telegram._published, dict(telegram._published))
        published.start()
        self.addCleanup(published.stop)


class UpdateTest(TelegramModuleTest):
    def test_channel_posts_are_extracted_with_kind(self):
        update = {"update_id": 200, "channel_post": {"message_id": 20, "text": "channel text",
                                                     "chat": {"id": -100, "type": "channel", "title": "News"}}}
        kind, message = telegram._extract_message(update)
        self.assertEqual(kind, "channel_post")
        self.assertEqual(message["chat"]["id"], -100)
        self.assertIn("chat_title=\"News\"", telegram._format_message(update, kind, message, "channel text"))


class RouteTest(TelegramModuleTest):
    def test_primary_route_is_stable_under_cross_chat_activity(self):
        with mock.patch.dict(os.environ, {"METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": " private-operator "}), \
                mock.patch.multiple(telegram, _reply_chat_id="protobots", create=True):
            self.assertEqual(telegram._reply_target(), "private-operator")
            self.assertEqual(telegram._reply_target("protobots"), "protobots")

    def test_without_a_primary_the_reply_chat_routes(self):
        with mock.patch.multiple(telegram, _reply_chat_id="current-chat", create=True):
            self.assertEqual(telegram._reply_target(), "current-chat")

    def test_the_published_reply_chat_routes_once_it_changes(self):
        client = pathlib.Path(telegram._client_path())
        client.write_text('{"pid": %d, "reply_chat": "from-the-client"}' % os.getpid())
        self.assertEqual(telegram._reply_target(), "from-the-client")


if __name__ == "__main__":
    unittest.main()

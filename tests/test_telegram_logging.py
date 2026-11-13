#!/usr/bin/env python3
"""What channels/telegram.py still does for the MeTTa client: logs the
updates of the attachments it fetches, downloads them atomically under
sanitized names, routes plain replies, keeps sends and uploads to allowed
chats, and reports an upload's receipt. The
client's own intake log and its one-send-per-turn rule are tested on the
MeTTa client by tests/telegram_send_and_intake_probe.metta."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channels"))
import telegram  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, chunks=(), ok=True, status_code=200):
        self.payload = payload
        self.chunks = chunks
        self.ok = ok
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, _size):
        return iter(self.chunks)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class TelegramModuleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = pathlib.Path(directory.name)
        # No client file of another test or process is taken up.
        environment = mock.patch.dict(os.environ, {
            "METTACLAW_TELEGRAM_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_TELEGRAM_ATTACHMENTS_DIR": str(self.directory / "attachments")})
        environment.start()
        self.addCleanup(environment.stop)
        state = mock.patch.multiple(telegram, _token="fake-token", _primary_chat_id="",
                                    _last_chat_id="", _allowed_chat_ids=set(), _client_seen=None,
                                    _log_path=str(self.directory / "telegram_updates.jsonl"))
        state.start()
        self.addCleanup(state.stop)
        published = mock.patch.dict(telegram._published, dict(telegram._published))
        published.start()
        self.addCleanup(published.stop)
        (self.directory / "attachments").mkdir()


class UpdateLogTest(TelegramModuleTest):
    def test_channel_posts_are_logged_with_kind(self):
        update = {"update_id": 200, "channel_post": {"message_id": 20, "text": "channel text",
                                                     "chat": {"id": -100, "type": "channel", "title": "News"}}}
        kind, message = telegram._extract_message(update)
        self.assertEqual(kind, "channel_post")
        self.assertTrue(telegram._append_update_log(update, kind, message, True, True, ""))
        record = json.loads(pathlib.Path(telegram._log_path).read_text(encoding="utf-8"))
        self.assertEqual((record["kind"], record["chat_id"]), ("channel_post", "-100"))
        self.assertEqual(record["raw"]["channel_post"]["text"], "channel text")


class AttachmentTest(TelegramModuleTest):
    def test_download_is_atomic_and_sanitized(self):
        message = {"message_id": 7, "document": {"file_id": "file-1", "file_name": "../../bad name.pdf",
                                                 "mime_type": "application/pdf", "file_size": 6}}

        def fake_get(url, params=None, timeout=None, stream=False):
            if url.endswith("/getFile"):
                self.assertEqual(params, {"file_id": "file-1"})
                return FakeResponse({"ok": True, "result": {"file_path": "docs/server.pdf"}})
            self.assertIs(stream, True)
            return FakeResponse(chunks=(b"abc", b"def"))

        with mock.patch.object(telegram.requests, "get", fake_get):
            note = telegram._download_attachment(message)
        saved = self.directory / "attachments" / "7-bad_name.pdf"
        self.assertEqual(saved.read_bytes(), b"abcdef")
        self.assertEqual(list((self.directory / "attachments").glob("*.part")), [])
        self.assertIn(str(saved), note)
        self.assertNotIn("fake-token", note)

    def test_oversized_download_leaves_no_partial_file(self):
        message = {"message_id": 8, "document": {"file_id": "file-2", "file_name": "large.bin"}}

        def fake_get(url, params=None, timeout=None, stream=False):
            if url.endswith("/getFile"):
                return FakeResponse({"ok": True, "result": {"file_path": "docs/large.bin"}})
            return FakeResponse(chunks=(b"1234", b"56"))

        with mock.patch.object(telegram.requests, "get", fake_get), \
                mock.patch.object(telegram, "_MAX_ATTACHMENT_BYTES", 5):
            note = telegram._download_attachment(message)
        self.assertIn("download failed: ValueError", note)
        self.assertEqual(list((self.directory / "attachments").iterdir()), [])


class RouteTest(TelegramModuleTest):
    def test_primary_route_is_stable_under_cross_chat_activity(self):
        with mock.patch.multiple(telegram, _primary_chat_id="private-operator", _reply_chat_id="protobots",
                                 _last_chat_id="another-chat", create=True):
            self.assertEqual(telegram._reply_target(), "private-operator")
            self.assertEqual(telegram._reply_target("protobots"), "protobots")

    def test_without_a_primary_the_reply_chat_routes(self):
        with mock.patch.multiple(telegram, _reply_chat_id="current-chat", _last_chat_id="last-chat", create=True):
            self.assertEqual(telegram._reply_target(), "current-chat")

    def test_the_published_reply_chat_routes_once_it_changes(self):
        client = pathlib.Path(telegram._client_path())
        client.write_text('{"pid": %d, "reply_chat": "from-the-client"}' % os.getpid())
        self.assertEqual(telegram._reply_target(), "from-the-client")


class OutboundTest(TelegramModuleTest):
    def test_uploads_reach_only_allowed_chats(self):
        artifact = self.directory / "artifact.txt"
        artifact.write_text("test", encoding="utf-8")
        with mock.patch.multiple(telegram, _primary_chat_id="operator-private",
                                 _allowed_chat_ids={"operator-private", "protobots"}):
            self.assertTrue(telegram.send_file(str(artifact), "caption", "unknown-chat").startswith(
                "send failed: target chat is not allowed"))

    def test_an_upload_reports_its_receipt(self):
        artifact = self.directory / "artifact.txt"
        artifact.write_text("test", encoding="utf-8")

        def fake_post(url, data=None, files=None, timeout=None):
            return FakeResponse({"ok": True, "result": {"message_id": 44}})

        with mock.patch.multiple(telegram, _primary_chat_id="operator-private", _allowed_chat_ids={"operator-private"}), \
                mock.patch.object(telegram.requests, "post", fake_post):
            result = telegram.send_file(str(artifact), "caption")
        self.assertTrue(result.startswith("sent "), result)
        self.assertIn("msg 44", result)


if __name__ == "__main__":
    unittest.main()

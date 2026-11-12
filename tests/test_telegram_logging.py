#!/usr/bin/env python3
import importlib
import json
import os
import pathlib
import sys
import tempfile
import threading


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_telegram():
    from channels import telegram

    return importlib.reload(telegram)


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


def test_channel_posts_are_logged_with_kind():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        update = {
            "update_id": 200,
            "channel_post": {
                "message_id": 20,
                "chat": {"id": -100, "type": "channel", "title": "News"},
                "text": "channel text",
            },
        }
        kind, message = tg._extract_message(update)
        assert kind == "channel_post"
        assert tg._append_update_log(update, kind, message, True, True, "")
        record = json.loads(pathlib.Path(tg._log_path).read_text(encoding="utf-8"))
        assert record["kind"] == "channel_post"
        assert record["chat_id"] == "-100"
        assert record["raw"]["channel_post"]["text"] == "channel text"


def test_attachment_download_is_atomic_and_sanitized():
    tg = load_telegram()
    message = {
        "message_id": 7,
        "document": {
            "file_id": "file-1",
            "file_name": "../../bad name.pdf",
            "mime_type": "application/pdf",
            "file_size": 6,
        },
    }

    def fake_get(url, params=None, timeout=None, stream=False):
        if url.endswith("/getFile"):
            assert params == {"file_id": "file-1"}
            return FakeResponse({"ok": True, "result": {"file_path": "docs/server.pdf"}})
        assert stream is True
        return FakeResponse(chunks=(b"abc", b"def"))

    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("METTACLAW_TELEGRAM_ATTACHMENTS_DIR")
        os.environ["METTACLAW_TELEGRAM_ATTACHMENTS_DIR"] = tmp
        try:
            tg.requests.get = fake_get
            tg._token = "fake-token"
            note = tg._download_attachment(message)
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_TELEGRAM_ATTACHMENTS_DIR", None)
            else:
                os.environ["METTACLAW_TELEGRAM_ATTACHMENTS_DIR"] = previous

        saved = pathlib.Path(tmp) / "7-bad_name.pdf"
        assert saved.read_bytes() == b"abcdef"
        assert not list(pathlib.Path(tmp).glob("*.part"))
        assert str(saved) in note
        assert "fake-token" not in note


def test_oversized_attachment_leaves_no_partial_file():
    tg = load_telegram()
    tg._MAX_ATTACHMENT_BYTES = 5
    message = {
        "message_id": 8,
        "document": {"file_id": "file-2", "file_name": "large.bin"},
    }

    def fake_get(url, params=None, timeout=None, stream=False):
        if url.endswith("/getFile"):
            return FakeResponse({"ok": True, "result": {"file_path": "docs/large.bin"}})
        return FakeResponse(chunks=(b"1234", b"56"))

    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("METTACLAW_TELEGRAM_ATTACHMENTS_DIR")
        os.environ["METTACLAW_TELEGRAM_ATTACHMENTS_DIR"] = tmp
        try:
            tg.requests.get = fake_get
            tg._token = "fake-token"
            note = tg._download_attachment(message)
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_TELEGRAM_ATTACHMENTS_DIR", None)
            else:
                os.environ["METTACLAW_TELEGRAM_ATTACHMENTS_DIR"] = previous

        assert "download failed: ValueError" in note
        assert list(pathlib.Path(tmp).iterdir()) == []


def test_primary_route_is_stable_under_cross_chat_activity():
    tg = load_telegram()
    tg._primary_chat_id = "private-operator"
    tg._reply_chat_id = "protobots"
    tg._last_chat_id = "another-chat"
    assert tg._reply_target() == "private-operator"
    assert tg._reply_target("protobots") == "protobots"
    assert "bound to" in tg.route_view()


def test_no_primary_retains_legacy_reply_route():
    tg = load_telegram()
    tg._primary_chat_id = ""
    tg._reply_chat_id = "current-chat"
    tg._last_chat_id = "last-chat"
    assert tg._reply_target() == "current-chat"


def test_explicit_outbound_route_is_limited_to_allowed_chats():
    tg = load_telegram()
    tg._token = "fake-token"
    tg._primary_chat_id = "operator-private"
    tg._allowed_chat_ids = {"operator-private", "protobots"}
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json["chat_id"])
        return FakeResponse({"ok": True, "result": {"message_id": 12}})

    tg.requests.post = fake_post
    assert tg.send_message_to_chat("unknown-chat", "no").startswith(
        "send failed: target chat is not allowed")
    assert posts == []
    assert tg.send_message_to_chat("protobots", "yes").startswith(
        "sent message 12")
    assert posts == ["protobots"]


def test_upload_route_is_limited_to_allowed_chats():
    tg = load_telegram()
    tg._token = "fake-token"
    tg._primary_chat_id = "operator-private"
    tg._allowed_chat_ids = {"operator-private", "protobots"}
    with tempfile.TemporaryDirectory() as tmp:
        artifact = pathlib.Path(tmp) / "artifact.txt"
        artifact.write_text("test", encoding="utf-8")
        assert tg.send_file(
            str(artifact), "caption", "unknown-chat"
        ).startswith("send failed: target chat is not allowed")


def test_upload_success_receipt_matches_broker_prefix():
    tg = load_telegram()
    tg._token = "fake-token"
    tg._primary_chat_id = "operator-private"
    tg._allowed_chat_ids = {"operator-private"}

    def fake_post(url, data=None, files=None, timeout=None):
        return FakeResponse({"ok": True, "result": {"message_id": 44}})

    tg.requests.post = fake_post
    with tempfile.TemporaryDirectory() as tmp:
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        artifact = pathlib.Path(tmp) / "artifact.txt"
        artifact.write_text("test", encoding="utf-8")
        result = tg.send_file(str(artifact), "caption")
        assert result.startswith("sent ")
        assert "msg 44" in result


def test_model_effect_send_is_at_most_once_per_turn():
    tg = load_telegram()
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json, timeout))
        return FakeResponse({
            "ok": True,
            "result": {"message_id": 100 + len(posts)},
        })

    with tempfile.TemporaryDirectory() as tmp:
        tg.requests.post = fake_post
        tg._token = "fake-token"
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")

        tg.begin_effect_turn("turn-1")
        tg.send_effect_message_to_chat("-1", "same message")
        tg.begin_effect_turn("turn-1")
        assert tg.send_effect_message_to_chat(
            "-1", "same message") == "duplicate send suppressed"
        assert len(posts) == 1

        tg.begin_effect_turn("turn-2")
        tg.send_effect_message_to_chat("-1", "same message")
        assert len(posts) == 2

        # Control-plane replies do not share the model-effect receipt set.
        tg.send_message_to_chat("-1", "same message")
        assert len(posts) == 3


if __name__ == "__main__":
    test_channel_posts_are_logged_with_kind()
    test_attachment_download_is_atomic_and_sanitized()
    test_oversized_attachment_leaves_no_partial_file()
    test_model_effect_send_is_at_most_once_per_turn()
    test_primary_route_is_stable_under_cross_chat_activity()
    test_no_primary_retains_legacy_reply_route()
    test_explicit_outbound_route_is_limited_to_allowed_chats()
    test_upload_route_is_limited_to_allowed_chats()
    test_upload_success_receipt_matches_broker_prefix()

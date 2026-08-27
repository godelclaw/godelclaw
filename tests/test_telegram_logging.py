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


def test_poll_logs_before_advancing_offset_and_queues_allowed_message():
    tg = load_telegram()
    updates = [
        {
            "update_id": 100,
            "message": {
                "message_id": 10,
                "chat": {"id": -1, "type": "group", "title": "Allowed Group"},
                "from": {"id": 42, "username": "zar"},
                "text": "hello from allowed",
            },
        },
        {
            "update_id": 101,
            "message": {
                "message_id": 11,
                "chat": {"id": -2, "type": "group", "title": "Other Group"},
                "from": {"id": 43, "username": "stranger"},
                "text": "private to other group",
                "document": {"file_id": "must-not-download"},
            },
        },
    ]

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/getUpdates")
        tg._running = False
        return FakeResponse({"ok": True, "result": updates})

    with tempfile.TemporaryDirectory() as tmp:
        tg.requests.get = fake_get
        tg._token = "fake-token"
        tg._allowed_chat_ids = {"-1"}
        tg._allow_private_chats = False
        tg._offset = None
        tg._offset_path = str(pathlib.Path(tmp) / "offset.txt")
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._pending_messages = []
        tg._reply_chat_id = ""
        tg._last_chat_id = ""
        tg._last_message_is_human = False
        tg._last_from_bot = False
        tg._running = True

        tg._poll_loop()

        records = [
            json.loads(line)
            for line in pathlib.Path(tg._log_path).read_text(encoding="utf-8").splitlines()
        ]
        assert [record["update_id"] for record in records] == [100, 101]
        assert pathlib.Path(tg._offset_path).read_text(encoding="utf-8") == "102"

        allowed = records[0]
        assert allowed["allowed"] is True
        assert allowed["queued"] is True
        assert allowed["chat_id"] == "-1"
        assert allowed["message_id"] == 10
        assert allowed["from_id"] == "42"
        assert allowed["text"] == "hello from allowed"
        assert allowed["raw"]["message"]["text"] == "hello from allowed"

        disallowed = records[1]
        assert disallowed["allowed"] is False
        assert disallowed["queued"] is False
        assert disallowed["note"] == "disallowed_chat"
        assert disallowed["chat_id"] == "-2"
        assert disallowed["text"] is None
        assert disallowed["raw"] is None

        queued = tg.getLastMessage()
        assert 'update_id="100"' in queued
        assert 'chat_id="-1"' in queued
        assert 'message_id="10"' in queued
        assert 'from_id="42"' in queued
        assert "hello from allowed" in queued
        assert "private to other group" not in queued
        assert tg.lastMessageIsHuman() == 1
        assert tg.lastMessageFromBot() is False

        assert tg.getLastMessage() == ""
        assert tg.lastMessageIsHuman() == 0


def test_poll_defaults_use_robust_long_poll_window():
    tg = load_telegram()
    observed = {}

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/getUpdates")
        observed["poll_timeout"] = params["timeout"]
        observed["request_timeout"] = timeout
        tg._running = False
        return FakeResponse({"ok": True, "result": []})

    keys = (
        "METTACLAW_TELEGRAM_POLL_TIMEOUT",
        "METTACLAW_TELEGRAM_REQUEST_TIMEOUT",
        "METTACLAW_TELEGRAM_HEALTH_PATH",
    )
    previous = {key: os.environ.pop(key, None) for key in keys}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = str(
                pathlib.Path(tmp) / "health.json")
            tg.requests.get = fake_get
            tg._offset = None
            tg._running = True
            tg._poll_loop()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert observed == {"poll_timeout": 20, "request_timeout": 30}


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


def test_log_failure_does_not_block_message_delivery_or_offset():
    tg = load_telegram()
    update = {
        "update_id": 300,
        "message": {
            "message_id": 30,
            "chat": {"id": -1, "type": "group", "title": "Allowed Group"},
            "from": {"id": 42, "username": "zar"},
            "text": "deliver even if logging fails",
        },
    }

    def fake_get(url, params=None, timeout=None):
        tg._running = False
        return FakeResponse({"ok": True, "result": [update]})

    with tempfile.TemporaryDirectory() as tmp:
        tg.requests.get = fake_get
        tg._append_update_log = lambda *args, **kwargs: False
        tg._token = "fake-token"
        tg._allowed_chat_ids = {"-1"}
        tg._allow_private_chats = False
        tg._offset = None
        tg._offset_path = str(pathlib.Path(tmp) / "offset.txt")
        tg._pending_messages = []
        tg._reply_chat_id = ""
        tg._last_chat_id = ""
        tg._last_message_is_human = False
        tg._last_from_bot = False
        tg._running = True

        tg._poll_loop()

        assert pathlib.Path(tg._offset_path).read_text(encoding="utf-8") == "301"
        queued = tg.getLastMessage()
        assert 'update_id="300"' in queued
        assert "deliver even if logging fails" in queued
        assert tg.lastMessageIsHuman() == 1


def test_bot_messages_are_delivered_but_do_not_arm_loop():
    tg = load_telegram()
    update = {
        "update_id": 400,
        "message": {
            "message_id": 40,
            "chat": {"id": -1, "type": "group", "title": "Allowed Group"},
            "from": {"id": 99, "username": "SiblingBot", "is_bot": True},
            "text": "bot-to-bot hello",
        },
    }

    def fake_get(url, params=None, timeout=None):
        tg._running = False
        return FakeResponse({"ok": True, "result": [update]})

    with tempfile.TemporaryDirectory() as tmp:
        tg.requests.get = fake_get
        tg._token = "fake-token"
        tg._allowed_chat_ids = {"-1"}
        tg._allow_private_chats = False
        tg._offset = None
        tg._offset_path = str(pathlib.Path(tmp) / "offset.txt")
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._pending_messages = []
        tg._reply_chat_id = ""
        tg._last_chat_id = ""
        tg._last_message_is_human = False
        tg._last_from_bot = False
        tg._running = True

        tg._poll_loop()

        queued = tg.getLastMessage()
        assert 'from_is_bot=true' in queued
        assert "bot-to-bot hello" in queued
        assert tg.lastMessageIsHuman() == 0
        assert tg.lastMessageFromBot() is True


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


def test_callback_log_records_actual_allowlist_decision():
    tg = load_telegram()
    update = {
        "update_id": 250,
        "callback_query": {
            "id": "callback-1",
            "data": "model:modelB",
            "from": {"id": 42, "username": "zar"},
            "message": {
                "message_id": 25,
                "chat": {"id": -1, "type": "group", "title": "Allowed Group"},
            },
        },
    }

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/getUpdates")
        tg._running = False
        return FakeResponse({"ok": True, "result": [update]})

    with tempfile.TemporaryDirectory() as tmp:
        tg.requests.get = fake_get
        tg._handle_callback_query = lambda _callback: "callback_model_switch"
        tg._token = "fake-token"
        tg._allowed_chat_ids = {"-1"}
        tg._allow_private_chats = False
        tg._offset = None
        tg._offset_path = str(pathlib.Path(tmp) / "offset.txt")
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._running = True

        tg._poll_loop()

        record = json.loads(pathlib.Path(tg._log_path).read_text(encoding="utf-8"))
        assert record["kind"] == "callback_query"
        assert record["allowed"] is True
        assert record["queued"] is False
        assert record["note"] == "callback_dispatched"



def test_energy_updates_are_atomic_across_threads():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        path = str(pathlib.Path(tmp) / "energy.json")
        previous = os.environ.get("METTACLAW_ENERGY_PATH")
        previous_light = os.environ.pop(
            "METTACLAW_TELEGRAM_LIGHT_ARM_IDS", None)
        os.environ["METTACLAW_ENERGY_PATH"] = path
        try:
            workers = [threading.Thread(
                target=tg.energy_set, args=(str(1000 + i), "light"))
                for i in range(20)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
            assert len(data["senders"]) == 20
            assert set(data["senders"].values()) == {"light"}
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_ENERGY_PATH", None)
            else:
                os.environ["METTACLAW_ENERGY_PATH"] = previous
            if previous_light is not None:
                os.environ["METTACLAW_TELEGRAM_LIGHT_ARM_IDS"] = previous_light


def test_delete_my_recent_advances_past_tombstoned_sends():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._token = "fake-token"
        tg._log_outbound("-1", "older", 10)
        tg._log_outbound("-1", "newer", 11)
        deleted = []

        def fake_post(url, json=None, timeout=None):
            deleted.append(json["message_id"])
            return FakeResponse({"ok": True})

        tg.requests.post = fake_post
        assert "message 11" in tg.delete_my_recent("-1", 1)
        assert "message 10" in tg.delete_my_recent("-1", 1)
        assert "nothing to delete" in tg.delete_my_recent("-1", 1)
        assert deleted == [11, 10]


def test_exact_model_delete_requires_own_send_receipt():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._token = "fake-token"
        deleted = []

        def fake_post(url, json=None, timeout=None):
            deleted.append((json["chat_id"], json["message_id"]))
            return FakeResponse({"ok": True})

        tg.requests.post = fake_post
        assert tg.delete_recorded_message("-7", 42).startswith(
            "delete refused: no current own-send receipt")
        assert deleted == []
        tg._log_outbound("-7", "mistaken send", 42)
        assert tg.delete_recorded_message("-7", 42).startswith(
            "deleted message 42")
        assert deleted == [("-7", 42)]
        records = [json.loads(line) for line in pathlib.Path(
            tg._log_path).read_text(encoding="utf-8").splitlines()]
        assert records[-1]["note"] == "own_delete"
        assert records[-1]["message_id"] == 42


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


def test_energy_updates_are_atomic_across_threads():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        path = str(pathlib.Path(tmp) / "energy.json")
        previous = os.environ.get("METTACLAW_ENERGY_PATH")
        previous_light = os.environ.pop(
            "METTACLAW_TELEGRAM_LIGHT_ARM_IDS", None)
        os.environ["METTACLAW_ENERGY_PATH"] = path
        try:
            workers = [threading.Thread(
                target=tg.energy_set, args=(str(1000 + i), "light"))
                for i in range(20)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
            assert len(data["senders"]) == 20
            assert set(data["senders"].values()) == {"light"}
        finally:
            if previous is None:
                os.environ.pop("METTACLAW_ENERGY_PATH", None)
            else:
                os.environ["METTACLAW_ENERGY_PATH"] = previous
            if previous_light is not None:
                os.environ["METTACLAW_TELEGRAM_LIGHT_ARM_IDS"] = previous_light


def test_delete_my_recent_advances_past_tombstoned_sends():
    tg = load_telegram()
    with tempfile.TemporaryDirectory() as tmp:
        tg._log_path = str(pathlib.Path(tmp) / "telegram_updates.jsonl")
        tg._token = "fake-token"
        tg._log_outbound("-1", "older", 10)
        tg._log_outbound("-1", "newer", 11)
        deleted = []

        def fake_post(url, json=None, timeout=None):
            deleted.append(json["message_id"])
            return FakeResponse({"ok": True})

        tg.requests.post = fake_post
        assert "message 11" in tg.delete_my_recent("-1", 1)
        assert "message 10" in tg.delete_my_recent("-1", 1)
        assert "nothing to delete" in tg.delete_my_recent("-1", 1)
        assert deleted == [11, 10]


if __name__ == "__main__":
    test_poll_logs_before_advancing_offset_and_queues_allowed_message()
    test_poll_defaults_use_robust_long_poll_window()
    test_channel_posts_are_logged_with_kind()
    test_log_failure_does_not_block_message_delivery_or_offset()
    test_bot_messages_are_delivered_but_do_not_arm_loop()
    test_attachment_download_is_atomic_and_sanitized()
    test_oversized_attachment_leaves_no_partial_file()
    test_callback_log_records_actual_allowlist_decision()
    test_model_effect_send_is_at_most_once_per_turn()
    test_exact_model_delete_requires_own_send_receipt()
    test_primary_route_is_stable_under_cross_chat_activity()
    test_no_primary_retains_legacy_reply_route()
    test_explicit_outbound_route_is_limited_to_allowed_chats()
    test_upload_route_is_limited_to_allowed_chats()
    test_upload_success_receipt_matches_broker_prefix()
    test_energy_updates_are_atomic_across_threads()
    test_delete_my_recent_advances_past_tombstoned_sends()
    test_energy_updates_are_atomic_across_threads()
    test_delete_my_recent_advances_past_tombstoned_sends()

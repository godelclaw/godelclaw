"""The Telegram module's menu registration, health file and attachment recovery."""
import shutil
import os
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "channels"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import telegram  # noqa: E402
import runtime_spool  # noqa: E402
import synthetic_llm  # noqa: E402
import loop_modes  # noqa: E402


_REAL_SET_MODEL = synthetic_llm.set_model
_REAL_CURRENT_MODEL = synthetic_llm.current_model


class SlashCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_mode_path = os.environ.get("METTACLAW_LOOP_MODE_PATH")
        self.previous_health_path = os.environ.get(
            "METTACLAW_TELEGRAM_HEALTH_PATH")
        self.previous_engine_env = {
            name: os.environ.get(name) for name in (
                "METTACLAW_ENGINE_STATE_PATH",
                "METTACLAW_RECYCLE_REQUEST_PATH",
            )
        }
        os.environ["METTACLAW_LOOP_MODE_PATH"] = os.path.join(
            self.tmp.name, "loop_mode.json")
        os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = os.path.join(
            self.tmp.name, "telegram-health.json")
        os.environ["METTACLAW_ENGINE_STATE_PATH"] = os.path.join(
            self.tmp.name, "engine")
        os.environ["METTACLAW_RECYCLE_REQUEST_PATH"] = os.path.join(
            self.tmp.name, "recycle.requested")
        self.previous_fuel_path = os.environ.get("METTACLAW_FUEL_MODE_PATH")
        os.environ["METTACLAW_FUEL_MODE_PATH"] = os.path.join(
            self.tmp.name, "fuel_mode.json")
        telegram._client_seen = None
        telegram._allowed_chat_ids = set()
        telegram._primary_chat_id = ""
        telegram._log_path = os.path.join(
            self.tmp.name, "telegram-updates.jsonl")
        self.p2 = mock.patch.object(
            synthetic_llm, "model_ids", lambda: ["modelA", "modelB"])
        self.p3 = mock.patch.object(
            synthetic_llm, "quota", lambda: "model=x | weekly=ok")
        self.p4 = mock.patch.object(
            synthetic_llm, "set_model",
            lambda name: f"model set to '{name}'")
        self.p5 = mock.patch.object(
            synthetic_llm, "current_model", lambda: "syn:large:text")
        self.p7 = mock.patch.object(
            synthetic_llm, "model_catalog_status", lambda: "catalog fixture")
        self.p8 = mock.patch.object(
            synthetic_llm, "model_in_observed_catalog", lambda name: True)
        self.p9 = mock.patch.object(
            synthetic_llm, "refresh_provider_observations_async",
            lambda *args, **kwargs: None)
        for p in (self.p2, self.p3, self.p4, self.p5,
                  self.p7, self.p8, self.p9):
            p.start()
        self.chat = {"id": -4321}
        self.operator = {"id": 111000111}
        os.environ["METTACLAW_TELEGRAM_OPERATOR_IDS"] = "111000111"

    def tearDown(self):
        for p in (self.p2, self.p3, self.p4, self.p5,
                  self.p7, self.p8, self.p9):
            p.stop()
        if self.previous_mode_path is None:
            os.environ.pop("METTACLAW_LOOP_MODE_PATH", None)
        else:
            os.environ["METTACLAW_LOOP_MODE_PATH"] = self.previous_mode_path
        if self.previous_health_path is None:
            os.environ.pop("METTACLAW_TELEGRAM_HEALTH_PATH", None)
        else:
            os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = \
                self.previous_health_path
        for name, value in self.previous_engine_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.previous_fuel_path is None:
            os.environ.pop("METTACLAW_FUEL_MODE_PATH", None)
        else:
            os.environ["METTACLAW_FUEL_MODE_PATH"] = self.previous_fuel_path
        self.tmp.cleanup()

    def test_health_file_has_liveness_but_no_credentials(self):
        telegram._health_update(poll_status="ok",
                                last_poll_ok_at=123.0)
        with open(os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"],
                  encoding="utf-8") as fh:
            health = fh.read()
        self.assertIn('"poll_status": "ok"', health)
        self.assertNotIn("token", health.lower())
        self.assertNotIn("chat_id", health.lower())


    def test_pending_attachment_is_recovered_from_durable_ledger(self):
        update = {
            "update_id": 90,
            "message": {
                "message_id": 14,
                "chat": {"id": -4321, "type": "group"},
                "from": self.operator,
                "caption": "please inspect",
                "document": {"file_id": "recover-me", "file_size": 10},
            },
        }
        telegram._log_path = os.path.join(self.tmp.name, "updates.jsonl")
        record = {
            "update_id": 90,
            "chat_id": "-4321",
            "message_id": 14,
            "note": "attachment_dispatched",
            "raw": update,
        }
        with open(telegram._log_path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        with mock.patch.object(telegram, "_enqueue_attachment") as enqueue:
            self.assertEqual(telegram._recover_pending_attachments(), 1)
        enqueue.assert_called_once()

    def test_each_attachment_starts_without_waiting_for_another(self):
        entered = []
        both_started = threading.Event()
        both_finished = threading.Event()
        release = threading.Event()
        finished = []

        def blocked_download(message):
            entered.append(message["message_id"])
            if len(entered) == 2:
                both_started.set()
            release.wait(2)
            return "[attachment saved]"

        def record_finished(*_args, **_kwargs):
            finished.append(True)
            if len(finished) == 2:
                both_finished.set()

        def update(number):
            return {
                "update_id": number,
                "message": {
                    "message_id": number,
                    "chat": {"id": -4321, "type": "group"},
                    "from": self.operator,
                },
            }

        with mock.patch.object(telegram, "_download_attachment",
                               side_effect=blocked_download), \
             mock.patch.object(telegram, "_append_update_log",
                               return_value=True), \
             mock.patch.object(telegram, "_set_last",
                               side_effect=record_finished), \
             mock.patch.object(telegram, "_wake_for_operator_message"):
            for number in (101, 102):
                item = update(number)
                telegram._enqueue_attachment(
                    item, "message", item["message"], "inspect")
            self.assertTrue(both_started.wait(1))
            release.set()
            self.assertTrue(both_finished.wait(1))
        self.assertCountEqual(entered, [101, 102])


if __name__ == "__main__":
    unittest.main(verbosity=2)

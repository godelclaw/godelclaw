"""Deterministic controls are answered without entering the agent loop."""
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
import synthetic_llm  # noqa: E402
import loop_modes  # noqa: E402
import engine_modes  # noqa: E402


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
                "METTACLAW_ACTIVE_ENGINE",
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
        os.environ["METTACLAW_ACTIVE_ENGINE"] = "petta"
        self.previous_fuel_path = os.environ.get("METTACLAW_FUEL_MODE_PATH")
        os.environ["METTACLAW_FUEL_MODE_PATH"] = os.path.join(
            self.tmp.name, "fuel_mode.json")
        telegram._health_state.clear()
        telegram._health_last_write = 0.0
        telegram._allowed_chat_ids = set()
        telegram._primary_chat_id = ""
        telegram._log_path = os.path.join(
            self.tmp.name, "telegram-updates.jsonl")
        self.sent = []
        self.p1 = mock.patch.object(
            telegram, "send_message_to_chat",
            lambda chat_id, text: self.sent.append((chat_id, text)))
        self.p1.start()
        self.p2 = mock.patch.object(
            synthetic_llm, "model_ids", lambda: ["modelA", "modelB"])
        self.p3 = mock.patch.object(
            synthetic_llm, "quota", lambda: "model=x | weekly=ok")
        self.p4 = mock.patch.object(
            synthetic_llm, "set_model",
            lambda name: f"model set to '{name}'")
        self.p5 = mock.patch.object(
            synthetic_llm, "current_model", lambda: "syn:large:text")
        self.p6 = mock.patch.object(
            engine_modes, "engine_available", lambda name: True)
        for p in (self.p2, self.p3, self.p4, self.p5, self.p6):
            p.start()
        self.chat = {"id": -4321}
        self.operator = {"id": 111000111}
        os.environ["METTACLAW_TELEGRAM_OPERATOR_IDS"] = "111000111"

    def tearDown(self):
        for p in (self.p1, self.p2, self.p3, self.p4, self.p5, self.p6):
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

    def test_menu_describes_operator_latch_truthfully(self):
        descriptions = dict(telegram._MENU_COMMANDS)
        self.assertEqual(
            descriptions["start"],
            "Enable cognition with the operator latch",
        )
        self.assertEqual(
            descriptions["stop"],
            "Stop cognition with the operator latch",
        )
        self.assertNotIn("watcher", descriptions["start"])
        self.assertNotIn("watcher", descriptions["stop"])

    def test_fuel_shows_and_switches_the_discipline(self):
        import fuel_modes
        self.assertEqual(self.handle("/fuel"), "slash_command:/fuel")
        self.assertIn("active fuel: " + fuel_modes.DEFAULT, self.sent[-1][1])
        self.assertEqual(self.handle("/fuel accumulate"), "slash_command:/fuel")
        self.assertIn("fuel set to 'accumulate'", self.sent[-1][1])
        self.assertEqual(fuel_modes.current_fuel(), "accumulate")
        self.assertEqual(self.handle("/fuel turbo"), "slash_command:/fuel")
        self.assertIn("fuel-set failed", self.sent[-1][1])

    def test_fuels_sends_keyboard(self):
        posts = []
        with mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            self.assertEqual(self.handle("/fuels"), "slash_command:/fuels")
        url, payload = posts[-1]
        self.assertIn("sendMessage", url)
        kb = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in kb],
                         ["fuel:saturate", "fuel:accumulate",
                          "fuel:carry", "fuel:decay", "fuel:unlimited"])

    def test_fuel_callback_is_operator_only(self):
        cq = {"id": "91", "data": "fuel:accumulate", "from": {"id": 222000222},
              "message": {"message_id": 5, "chat": {"id": -4321}}}
        with mock.patch.object(telegram, "_chat_is_allowed",
                               lambda chat: True), \
             mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None: mock.Mock()):
            self.assertEqual(telegram._handle_callback_query(cq),
                             "callback_not_operator")

    def test_fuel_callback_switches_and_redraws(self):
        import fuel_modes
        posts = []
        cq = {"id": "92", "data": "fuel:decay", "from": {"id": 111000111},
              "message": {"message_id": 5, "chat": {"id": -4321}}}
        with mock.patch.object(telegram, "_chat_is_allowed",
                               lambda chat: True), \
             mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            self.assertEqual(telegram._handle_callback_query(cq),
                             "callback_fuel_switch")
        self.assertEqual(fuel_modes.current_fuel(), "decay")
        self.assertTrue(any("editMessageText" in u for u, _ in posts))

    def handle(self, text, sender=None):
        return telegram._handle_slash_command(
            self.chat, self.operator if sender is None else sender, text)

    def test_non_operator_passthrough(self):
        self.assertIsNone(self.handle("/model claude-fable-5",
                                      sender={"id": 42}))
        self.assertEqual(self.sent, [])

    def test_models_sends_keyboard(self):
        posts = []
        with mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            self.assertEqual(self.handle("/models"), "slash_command:/models")
        url, payload = posts[-1]
        self.assertIn("sendMessage", url)
        kb = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in kb],
                         ["model:modelA", "model:modelB"])

    def test_callback_switches_model(self):
        posts = []
        cq = {"id": "77", "data": "model:modelB", "from": {"id": 111000111},
              "message": {"message_id": 5, "chat": {"id": -4321}}}
        with mock.patch.object(telegram, "_chat_is_allowed",
                               lambda chat: True), \
             mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            note = telegram._handle_callback_query(cq)
        self.assertEqual(note, "callback_model_switch")
        self.assertTrue(any("answerCallbackQuery" in u for u, _ in posts))
        self.assertTrue(any("editMessageText" in u for u, _ in posts))

    def test_callback_disallowed_chat_ignored(self):
        cq = {"id": "78", "data": "model:modelB", "from": {"id": 111000111},
              "message": {"message_id": 5, "chat": {"id": 999}}}
        with mock.patch.object(telegram, "_chat_is_allowed",
                               lambda chat: False):
            self.assertEqual(telegram._handle_callback_query(cq),
                             "callback_disallowed_chat")

    def test_quota(self):
        self.assertEqual(self.handle("/quota"), "slash_command:/quota")
        self.assertIn("weekly=ok", self.sent[-1][1])

    def test_health_is_deterministic_and_does_not_call_model(self):
        with mock.patch("runtime_health.report", return_value="runtime ok"), \
             mock.patch.object(synthetic_llm, "current_model") as model:
            self.assertEqual(self.handle("/health"),
                             "slash_command:/health")
        model.assert_not_called()
        self.assertEqual(self.sent[-1][1], "runtime ok")

    def test_activity_is_deterministic_and_does_not_wake_loop(self):
        telegram._wake_event.clear()
        with mock.patch("runtime_health.activity_report",
                        return_value="activity: idle"), \
             mock.patch.object(synthetic_llm, "current_model") as model:
            self.assertEqual(self.handle("/activity"),
                             "slash_command:/activity")
        model.assert_not_called()
        self.assertFalse(telegram._wake_event.is_set())
        self.assertEqual(self.sent[-1][1], "activity: idle")

    def test_every_control_uses_an_independent_lane(self):
        observed = []
        events = {command: threading.Event()
                  for command in telegram._CONTROL_COMMANDS}

        def fake_handle(_chat, _sender, text):
            command = text.split(None, 1)[0]
            observed.append(command)
            events[command].set()

        with mock.patch.object(telegram, "_handle_slash_command",
                               side_effect=fake_handle):
            for command in telegram._CONTROL_COMMANDS:
                self.assertEqual(telegram._dispatch_slash_command(
                    self.chat, self.operator, command), "independent")
            for command in telegram._CONTROL_COMMANDS:
                self.assertTrue(events[command].wait(1), command)
        self.assertCountEqual(observed, telegram._CONTROL_COMMANDS)

    def test_every_control_bypasses_a_blocked_control(self):
        slow_entered = threading.Event()
        release_slow = threading.Event()
        seen = {command: threading.Event()
                for command in telegram._CONTROL_COMMANDS
                if command != "/delete"}

        def fake_handle(_chat, _sender, text):
            if text == "/delete":
                slow_entered.set()
                release_slow.wait(2)
            else:
                seen[text].set()

        with mock.patch.object(telegram, "_handle_slash_command",
                               side_effect=fake_handle):
            self.assertEqual(telegram._dispatch_slash_command(
                self.chat, self.operator, "/delete"), "independent")
            self.assertTrue(slow_entered.wait(1))
            for command in seen:
                self.assertEqual(telegram._dispatch_slash_command(
                    self.chat, self.operator, command), "independent")
            for command, event in seen.items():
                self.assertTrue(event.wait(1), command)
            release_slow.set()

    def test_model_bare_shows_current(self):
        self.assertEqual(self.handle("/model"), "slash_command:/model")
        self.assertIn("syn:large:text", self.sent[-1][1])

    def test_model_switch(self):
        self.assertEqual(self.handle("/model claude-fable-5"),
                         "slash_command:/model")
        self.assertIn("claude-fable-5", self.sent[-1][1])

    def test_mode_bare_shows_current(self):
        self.assertEqual(self.handle("/mode"), "slash_command:/mode")
        self.assertIn("selected mode: godelclaw", self.sent[-1][1])

    def test_mode_view_distinguishes_running_loop_from_pending_selection(self):
        loop_modes.record_active("godelclaw", os.getpid())
        self.handle("/mode iter")
        self.handle("/mode")
        self.assertIn("active mode: godelclaw; requested iter", self.sent[-1][1])
        self.assertEqual(loop_modes.current_mode(), "iter")
        self.assertTrue(os.path.exists(os.environ["METTACLAW_RECYCLE_REQUEST_PATH"]))

    def test_mode_switch_persists_and_wakes_loop(self):
        telegram._wake_event.clear()
        try:
            self.assertEqual(self.handle("/mode coding"),
                             "slash_command:/mode")
            self.assertEqual(loop_modes.current_mode(), "coding")
            self.assertTrue(telegram._wake_event.is_set())
            self.assertIn("persists", self.sent[-1][1])
        finally:
            telegram._wake_event.clear()

    def test_modes_sends_keyboard(self):
        posts = []
        with mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            self.assertEqual(self.handle("/modes"), "slash_command:/modes")
        payload = posts[-1][1]
        callbacks = [row[0]["callback_data"]
                     for row in payload["reply_markup"]["inline_keyboard"]]
        self.assertEqual(callbacks, ["mode:godelclaw", "mode:coding", "mode:iter", "mode:omega"])

    def test_engine_bare_shows_active_engine(self):
        self.assertEqual(self.handle("/engine"), "slash_command:/engine")
        self.assertIn("active engine: petta", self.sent[-1][1])

    def test_engine_switch_persists_and_requests_recycle(self):
        telegram._wake_event.clear()
        try:
            self.assertEqual(self.handle("/engine cetta"),
                             "slash_command:/engine")
            self.assertEqual(engine_modes.selected_engine(), "cetta")
            self.assertTrue(os.path.isfile(
                os.environ["METTACLAW_RECYCLE_REQUEST_PATH"]))
            self.assertTrue(telegram._wake_event.is_set())
            self.assertIn("recycling", self.sent[-1][1])
        finally:
            telegram._wake_event.clear()

    def test_engines_sends_keyboard(self):
        posts = []
        with mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            self.assertEqual(self.handle("/engines"),
                             "slash_command:/engines")
        payload = posts[-1][1]
        callbacks = [row[0]["callback_data"]
                     for row in payload["reply_markup"]["inline_keyboard"]]
        self.assertEqual(callbacks, ["engine:petta", "engine:cetta"])
        self.assertIn("pleatta [disabled]", payload["text"])

    def test_callback_switches_engine(self):
        posts = []
        cq = {"id": "89", "data": "engine:cetta",
              "from": {"id": 111000111},
              "message": {"message_id": 7, "chat": {"id": -4321}}}
        telegram._wake_event.clear()
        try:
            with mock.patch.object(telegram, "_chat_is_allowed",
                                   lambda chat: True), \
                 mock.patch.object(telegram.requests, "post",
                                   lambda url, json=None, timeout=None:
                                   posts.append((url, json)) or mock.Mock()):
                note = telegram._handle_callback_query(cq)
            self.assertEqual(note, "callback_engine_switch")
            self.assertEqual(engine_modes.selected_engine(), "cetta")
            self.assertTrue(telegram._wake_event.is_set())
            self.assertTrue(any("answerCallbackQuery" in u for u, _ in posts))
            self.assertTrue(any("editMessageText" in u for u, _ in posts))
        finally:
            telegram._wake_event.clear()

    def test_command_menu_registers_mode_controls(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "result": True}
        with mock.patch.object(telegram.requests, "post",
                               return_value=response) as post:
            telegram._register_menu_commands()
        payload = post.call_args.kwargs["json"]
        names = [item["command"] for item in payload["commands"]]
        self.assertIn("mode", names)
        self.assertIn("modes", names)
        self.assertIn("start", names)
        self.assertIn("stop", names)
        self.assertIn("engine", names)
        self.assertIn("engines", names)
        self.assertIn("health", names)
        self.assertEqual(len(names), len(set(names)))

    def test_command_menu_failure_does_not_raise(self):
        with mock.patch.object(telegram.requests, "post",
                               side_effect=RuntimeError("offline")):
            telegram._register_menu_commands()

    def test_health_file_has_liveness_but_no_credentials(self):
        telegram._health_update(force=True, poll_status="ok",
                                last_poll_ok_at=123.0)
        with open(os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"],
                  encoding="utf-8") as fh:
            health = fh.read()
        self.assertIn('"poll_status": "ok"', health)
        self.assertNotIn("token", health.lower())
        self.assertNotIn("chat_id", health.lower())

    def test_rest_deadline_is_externally_observable(self):
        with mock.patch.object(telegram, "_health_update") as health, \
             mock.patch.object(telegram._wake_event, "wait",
                               return_value=True):
            telegram.sleep_until_message(30)
        updates = [call.kwargs for call in health.call_args_list]
        self.assertTrue(any(item.get("loop_status") == "waiting"
                            and item.get("waiting_until", 0) > time.time()
                            for item in updates))
        self.assertEqual(updates[-1]["loop_status"], "awake")

    def test_callback_switches_mode(self):
        posts = []
        cq = {"id": "88", "data": "mode:coding",
              "from": {"id": 111000111},
              "message": {"message_id": 6, "chat": {"id": -4321}}}
        with mock.patch.object(telegram, "_chat_is_allowed",
                               lambda chat: True), \
             mock.patch.object(telegram.requests, "post",
                               lambda url, json=None, timeout=None:
                               posts.append((url, json)) or mock.Mock()):
            note = telegram._handle_callback_query(cq)
        self.assertEqual(note, "callback_mode_switch")
        self.assertEqual(loop_modes.current_mode(), "coding")
        self.assertTrue(any("answerCallbackQuery" in u for u, _ in posts))
        self.assertTrue(any("editMessageText" in u for u, _ in posts))

    def test_wake_is_poll_fast_path(self):
        self.assertEqual(
            telegram._peek_slash_command("/wake", self.operator),
            "slash_command:/wake")

    def test_operator_exact_delete_is_fast_and_explicitly_addressed(self):
        with open(telegram._log_path, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "chat_id": "-7", "chat_title": "Research Group",
                "message_id": 42, "kind": "message",
            }) + "\n")
        telegram._allowed_chat_ids = {"-7"}
        with mock.patch.object(
                telegram, "delete_message",
                return_value="deleted message 42 from chat -7") as delete:
            self.assertEqual(
                self.handle("/delete Research Group 42"),
                "slash_command:/delete",
            )
        delete.assert_called_once_with("-7", "42")
        self.assertIn("deleted message 42", self.sent[-1][1])

    def test_operator_delete_refuses_unknown_chat(self):
        self.assertEqual(
            self.handle("/delete Unknown 42"), "slash_command:/delete"
        )
        self.assertIn("delete refused", self.sent[-1][1])

    def test_wake_does_not_depend_on_model_backend(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "synthetic_llm":
                raise ImportError("model backend unavailable")
            return real_import(name, *args, **kwargs)

        telegram._wake_event.clear()
        try:
            with mock.patch.object(telegram, "rest_status",
                                   return_value=(True, 125)), \
                 mock.patch("builtins.__import__",
                            side_effect=guarded_import):
                self.assertEqual(self.handle("/wake"),
                                 "slash_command:/wake")
            self.assertTrue(telegram._wake_event.is_set())
            self.assertEqual(self.sent, [])
        finally:
            telegram._wake_event.clear()

    def test_wake_for_other_bot_is_consumed_without_waking(self):
        telegram._bot_username = "SomeBot"
        telegram._wake_event.clear()
        try:
            self.assertEqual(
                self.handle("/wake@OtherBot"),
                "slash_command_other_bot:otherbot")
            self.assertFalse(telegram._wake_event.is_set())
            self.assertEqual(self.sent, [])
        finally:
            telegram._bot_username = None
            telegram._wake_event.clear()

    def test_poll_dispatches_wake_without_false_queue_notice(self):
        update = {
            "update_id": 77,
            "message": {
                "message_id": 12,
                "chat": {"id": -4321, "type": "group"},
                "from": self.operator,
                "text": "/wake",
            },
        }
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [update]}

        def one_poll(*args, **kwargs):
            telegram._running = False
            return response

        telegram._running = True
        try:
            with mock.patch.object(telegram.requests, "get",
                                   side_effect=one_poll), \
                 mock.patch.object(telegram, "_chat_is_allowed",
                                   return_value=True), \
                 mock.patch.object(telegram, "_download_attachment",
                                   return_value=None), \
                 mock.patch.object(telegram, "_append_update_log",
                                   return_value=True), \
                 mock.patch.object(telegram, "_save_offset"), \
                 mock.patch.object(telegram, "_set_last") as queued, \
                 mock.patch.object(telegram, "_maybe_rest_notice") as notice, \
                 mock.patch.object(telegram, "_dispatch_slash_command") as dispatch:
                telegram._poll_loop()
        finally:
            telegram._running = False

        dispatch.assert_called_once_with(
            update["message"]["chat"], self.operator, "/wake")
        notice.assert_not_called()
        queued.assert_not_called()

    def test_wake_peer_message_wakes_and_queues_with_its_bot_flag(self):
        peer = {"id": 333000333, "is_bot": True, "username": "PeerBot"}
        update = {
            "update_id": 78,
            "message": {
                "message_id": 13,
                "chat": {"id": 333000333, "type": "private"},
                "from": peer,
                "text": "/stop is only words from a peer",
            },
        }
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [update]}

        def one_poll(*args, **kwargs):
            telegram._running = False
            return response

        telegram._running = True
        telegram._wake_event.clear()
        telegram._wake_reason = ""
        try:
            with mock.patch.dict(os.environ, {
                     "METTACLAW_TELEGRAM_WAKE_IDS": "333000333",
                     "METTACLAW_ENERGY_PATH": os.path.join(
                         self.tmp.name, "energy.json")}), \
                 mock.patch.object(telegram.requests, "get",
                                   side_effect=one_poll), \
                 mock.patch.object(telegram, "_chat_is_allowed",
                                   return_value=True), \
                 mock.patch.object(telegram, "_append_update_log",
                                   return_value=True), \
                 mock.patch.object(telegram, "_save_offset"), \
                 mock.patch.object(telegram, "_set_last") as queued, \
                 mock.patch.object(telegram, "_dispatch_slash_command") as dispatch:
                telegram._poll_loop()
            woke = telegram._wake_event.is_set()
            reason = telegram._wake_reason
        finally:
            telegram._running = False
            telegram._wake_event.clear()
            telegram._wake_reason = ""

        dispatch.assert_not_called()
        queued.assert_called_once()
        self.assertTrue(queued.call_args.args[2],
                        "a waking peer keeps its bot flag, so it arms nothing")
        self.assertTrue(woke)
        self.assertEqual(reason, "peer message")

    def test_captioned_fast_control_never_waits_for_attachment(self):
        update = {
            "update_id": 78,
            "message": {
                "message_id": 13,
                "chat": {"id": -4321, "type": "group"},
                "from": self.operator,
                "caption": "/activity",
                "document": {"file_id": "slow-file", "file_size": 10},
            },
        }
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": [update]}

        def one_poll(*_args, **_kwargs):
            telegram._running = False
            return response

        telegram._running = True
        try:
            with mock.patch.object(telegram.requests, "get",
                                   side_effect=one_poll), \
                 mock.patch.object(telegram, "_chat_is_allowed",
                                   return_value=True), \
                 mock.patch.object(telegram, "_dispatch_slash_command") \
                         as dispatch, \
                 mock.patch.object(telegram, "_enqueue_attachment") \
                         as attachment, \
                 mock.patch.object(telegram, "_append_update_log",
                                   return_value=True), \
                 mock.patch.object(telegram, "_save_offset"):
                telegram._poll_loop()
        finally:
            telegram._running = False
        dispatch.assert_called_once_with(
            update["message"]["chat"], self.operator, "/activity")
        attachment.assert_not_called()

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

    def test_botname_suffix_for_self_is_handled(self):
        telegram._bot_username = "SomeBot"
        try:
            self.assertEqual(self.handle("/models@SomeBot"),
                             "slash_command:/models")
        finally:
            telegram._bot_username = None

    def test_poll_fast_path_uses_configured_identity_without_network(self):
        telegram._bot_username = None
        previous = os.environ.get("METTACLAW_TELEGRAM_BOT_USERNAME")
        os.environ["METTACLAW_TELEGRAM_BOT_USERNAME"] = "SomeBot"
        try:
            with mock.patch.object(telegram.requests, "get") as get:
                self.assertEqual(
                    telegram._peek_slash_command(
                        "/mode@SomeBot", self.operator),
                    "slash_command:/mode")
            get.assert_not_called()
        finally:
            telegram._bot_username = None
            if previous is None:
                os.environ.pop("METTACLAW_TELEGRAM_BOT_USERNAME", None)
            else:
                os.environ["METTACLAW_TELEGRAM_BOT_USERNAME"] = previous

    def test_botname_suffix_for_other_bot_is_consumed(self):
        telegram._bot_username = "SomeBot"
        try:
            self.assertEqual(self.handle("/models@OtherBot"),
                             "slash_command_other_bot:otherbot")
        finally:
            telegram._bot_username = None

    def test_unrelated_commands_and_text_pass_through(self):
        self.assertIsNone(self.handle("hello there"))
        self.assertIsNone(self.handle(""))
        self.assertEqual(len(self.sent), 0)

    def test_start_sets_operator_latch_before_waking_cognition(self):
        with mock.patch("lifecycle.start",
                        return_value="started: cognition enabled by operator latch"), \
             mock.patch("lifecycle.cognition_enabled", return_value=1), \
             mock.patch.object(telegram, "_request_wake") as wake:
            self.assertEqual(self.handle("/start"), "slash_command:/start")
        wake.assert_called_once_with("operator start")
        self.assertIn("operator latch", self.sent[-1][1])

    def test_failed_start_does_not_wake_cognition(self):
        with mock.patch("lifecycle.start",
                        return_value="start refused: remains stopped"), \
             mock.patch("lifecycle.cognition_enabled", return_value=0), \
             mock.patch.object(telegram, "_request_wake") as wake:
            self.assertEqual(self.handle("/start"), "slash_command:/start")
        wake.assert_not_called()

    def test_stop_revokes_and_wakes_control_loop(self):
        with mock.patch("lifecycle.stop",
                        return_value="stopped: cognition revoked"), \
             mock.patch.object(telegram, "_request_wake") as wake:
            self.assertEqual(self.handle("/stop"), "slash_command:/stop")
        wake.assert_called_once_with("operator stop")
        self.assertIn("cognition revoked", self.sent[-1][1])

    def test_handler_error_never_raises(self):
        with mock.patch.object(synthetic_llm, "model_ids",
                               side_effect=RuntimeError("boom")):
            note = self.handle("/models")
        self.assertEqual(note, "slash_command_error:/models")



class CrossBotAddressingTests(unittest.TestCase):
    def setUp(self):
        telegram._bot_username = "PrimaryTestBot"

    def tearDown(self):
        telegram._bot_username = None

    def test_command_for_another_bot_is_consumed_not_answered(self):
        note = telegram._handle_slash_command(
            {"id": 1}, {"id": 555000555}, "/model@OtherTestBot")
        self.assertEqual(note, "slash_command_other_bot:othertestbot")

    def test_unknown_identity_with_suffix_is_consumed(self):
        telegram._bot_username = None
        with mock.patch.object(telegram, "requests") as req:
            req.get.side_effect = Exception("net down")
            note = telegram._handle_slash_command(
                {"id": 1}, {"id": 555000555}, "/model@AnyBot")
        self.assertEqual(note, "slash_command_other_bot:anybot")


if __name__ == "__main__":
    unittest.main(verbosity=2)

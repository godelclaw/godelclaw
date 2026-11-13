"""Failures at the independent control/routing/delivery boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels"), str(ROOT / "modes")]
import durable_telegram
import mode_channel
import runtime_host
import synthetic_llm


class ControlBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / "modes/iter"
        (self.runtime / "channels").mkdir(parents=True)
        self.environment = mock.patch.dict(os.environ, {
            "METTACLAW_MODE_STATE_ROOT": str(self.base / "modes"),
            "METTACLAW_MODE_RUNTIME": str(self.runtime),
            "METTACLAW_ENGINE_STATE_PATH": str(self.base / "engine"),
            "METTACLAW_ACTIVE_LOOP_MODE": "iter",
            "METTACLAW_TELEGRAM_PRIMARY_CHAT_ID": "42",
            "METTACLAW_TELEGRAM_SERVICE_SOCKET": str(self.base / "channel.sock"),
            "METTACLAW_MODEL_STATE_PATH": str(self.base / "model.metta"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_model_selection_reaches_already_running_process(self):
        state = self.base / "model.metta"
        state.write_text("(active-model fixture-before)\n")
        code = "import synthetic_llm; print(synthetic_llm.current_model(), flush=True); input(); print(synthetic_llm.current_model(), flush=True)"
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        child = subprocess.Popen([sys.executable, "-c", code], env=environment,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "fixture-before")
            state.write_text("(active-model fixture-after)\n")
            output, _ = child.communicate("next\n", timeout=5)
            self.assertEqual(output.strip(), "fixture-after")
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_group_bot_reply_and_operator_have_separate_destinations(self):
        update = {"update_id": 11, "message": {"message_id": 81,
            "chat": {"id": -84, "type": "supergroup", "title": "Lab"},
            "from": {"id": 9, "first_name": "Sibling", "is_bot": True},
            "message_thread_id": 5, "is_topic_message": True,
            "reply_to_message": {"message_id": 80, "from": {"id": 7, "first_name": "Other"}},
            "text": "verdict"}}
        client = mock.Mock()
        client.pending.return_value = [("input-11", json.dumps(["input", "-84.5", "message", "bot", update])),
                                       ("control-12", json.dumps(["command", "42.0", "/mode", "omega", "operator"]))]
        with mock.patch.object(runtime_host, "gate"), mock.patch.object(runtime_host, "channel", return_value=client):
            observed = runtime_host.receive()
            self.assertNotIn("/mode", observed)
            self.assertIn('chat_type="supergroup"', observed)
            self.assertIn("from_is_bot=true", observed)
            self.assertIn('thread_id="5"', observed)
            self.assertIn('reply_to_message_id="80"', observed)
            self.assertIn("reply_channel=telegram_m84_5", observed)
            runtime_host.commit_inputs()
        client.acknowledge.assert_called_once_with("input-11")
        self.assertTrue((self.runtime / "channels/telegram_m84_5.py").exists())
        self.assertIn("telegram_m84_5", mode_channel.context())
        mode_channel.enqueue("private status")
        mode_channel.enqueue("sibling reply", "telegram_m84_5")
        intents = [json.loads(p.read_text()) for p in (self.base / "modes/channel-outbox").glob("*.json")]
        lanes = {i["commands"][0][1]: durable_telegram.lane_of(i["key"]) for i in intents}
        self.assertEqual(lanes, {"private status": "42.0", "sibling reply": "-84.5"})

    def test_sender_text_cannot_create_a_destination(self):
        for destination in ("../operator", "telegram_m84_5", "other", "telegram_0_0"):
            with self.assertRaises(ValueError):
                mode_channel.enqueue("text", destination)
        self.assertFalse((self.base / "modes/channel-outbox").exists())

    def test_handover_input_survives_until_a_cognitive_checkpoint(self):
        update = {"update_id": 19, "message": {"chat": {"id": 42, "type": "private"},
                  "from": {"id": 7, "is_bot": False}, "text": "pending during handover"}}
        mode_channel.store(self.base / "modes/channel-handover/19.json", update)
        client = mock.Mock()
        client.pending.return_value = []
        with mock.patch.object(runtime_host, "gate"), mock.patch.object(runtime_host, "channel", return_value=client):
            first = runtime_host.receive()
            self.assertIn("pending during handover", first)
            self.assertEqual(runtime_host.receive(), first)
            self.assertEqual(len(mode_channel.handover_inputs()), 1)
            runtime_host.commit_inputs()
            self.assertEqual(runtime_host.receive(), "")
        client.acknowledge.assert_not_called()
        self.assertTrue((self.base / "modes/channel-handover/19.done").exists())

    def test_normal_metta_client_preserves_the_same_handover_receipt(self):
        import telegram
        update = {"update_id": 21, "message": {"chat": {"id": -84, "type": "group"},
                  "from": {"id": 9, "is_bot": True}, "text": "pending sibling input"}}
        mode_channel.store(self.base / "modes/channel-handover/21.json", update)
        # The native client offers it (src/runtime_events_cetta.metta,
        # tests/unit/runtime_events.metta); its turn's acknowledgement is the
        # same receipt.
        self.assertEqual(len(mode_channel.handover_inputs()), 1)
        telegram.handover_ack("21")
        self.assertFalse(mode_channel.handover_inputs())

    def test_control_heartbeat_records_unavailable_channel_without_cognition(self):
        with mock.patch.object(mode_channel, "_last_health_at", 0), mock.patch.object(time, "monotonic", return_value=100):
            with mock.patch.object(durable_telegram.Channel, "rpc", return_value=(durable_telegram.IDLE, "", "")) as rpc:
                mode_channel.heartbeat()
                mode_channel.heartbeat()
                rpc.assert_called_once()
                receipt = json.loads((self.base / "modes/control-status.json").read_text())
                self.assertTrue(receipt["channel_ready"])
            with mock.patch.object(time, "monotonic", return_value=106), mock.patch.object(durable_telegram.Channel, "rpc", side_effect=TimeoutError):
                mode_channel.heartbeat()
                receipt = json.loads((self.base / "modes/control-status.json").read_text())
                self.assertFalse(receipt["channel_ready"])

    def test_slow_or_missing_transport_never_blocks_tool_completion(self):
        with mock.patch.object(durable_telegram.Channel, "rpc", side_effect=AssertionError("tool must not do network I/O")):
            result = mode_channel.enqueue("slow delivery")
        self.assertTrue(result.startswith("durably queued:"))
        with mock.patch.object(durable_telegram.Channel, "rpc", side_effect=TimeoutError):
            self.assertEqual(mode_channel.pump(), 0)
        self.assertEqual(len(list((self.base / "modes/channel-outbox").glob("*.json"))), 1)

    def test_lost_acceptance_retries_same_key_and_bytes_after_restart(self):
        mode_channel.enqueue("exact message")
        calls = []
        def rpc(client, code, key, body):
            calls.append((code, key, body))
            if len(calls) == 1:
                raise TimeoutError("accepted but reply lost")
            return durable_telegram.STORED, "", ""
        with mock.patch.object(durable_telegram.Channel, "rpc", rpc):
            self.assertEqual(mode_channel.pump(), 0)
            self.assertEqual(mode_channel.pump(), 1)
            self.assertEqual(mode_channel.pump(), 0)
        self.assertEqual(calls[0], calls[1])

    def test_terminal_refusal_is_retained_without_rerouting(self):
        mode_channel.enqueue("refused message")
        with mock.patch.object(durable_telegram.Channel, "rpc", return_value=(durable_telegram.INVALID, "", "")) as rpc:
            self.assertEqual(mode_channel.pump(), 0)
            self.assertEqual(mode_channel.pump(), 0)
        rpc.assert_called_once()
        folder = self.base / "modes/channel-outbox"
        self.assertEqual(len(list(folder.glob("*.refused"))), 1)
        self.assertFalse(list(folder.glob("*.json")))

    def test_independent_senders_do_not_reuse_stale_sequence(self):
        sequence = str(self.base / "sequence")
        first = durable_telegram.Channel("", sequence)
        second = durable_telegram.Channel("", sequence)
        with mock.patch.object(time, "time_ns", return_value=1):
            key1 = first.key(42)
            key2 = second.key(42)
        self.assertNotEqual(key1, key2)
        self.assertGreater(key2, key1)


if __name__ == "__main__":
    unittest.main()

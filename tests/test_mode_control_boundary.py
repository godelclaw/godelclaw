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

    def test_sender_text_cannot_create_a_destination(self):
        for destination in ("../operator", "telegram_m84_5", "other", "telegram_0_0"):
            with self.assertRaises(ValueError):
                mode_channel.enqueue("text", destination)
        self.assertFalse((self.base / "modes/channel-outbox").exists())

    def test_normal_metta_client_preserves_the_same_handover_receipt(self):
        update = {"update_id": 21, "message": {"chat": {"id": -84, "type": "group"},
                  "from": {"id": 9, "is_bot": True}, "text": "pending sibling input"}}
        mode_channel.store(self.base / "modes/channel-handover/21.json", update)
        # The native client offers it (src/runtime_events_cetta.metta,
        # tests/unit/runtime_events.metta); its turn's acknowledgement is the
        # same receipt.
        self.assertEqual(len(mode_channel.handover_inputs()), 1)
        self.assertTrue(mode_channel.acknowledge_handover(["21"]))
        self.assertFalse(mode_channel.handover_inputs())

    # The control responder submits the outbox and publishes readiness natively
    # (channels/telegram_control_io.metta, tests/unit/control_io.metta).
    def test_slow_or_missing_transport_never_blocks_tool_completion(self):
        with mock.patch.object(durable_telegram.Channel, "rpc", side_effect=AssertionError("tool must not do network I/O")):
            result = mode_channel.enqueue("slow delivery")
        self.assertTrue(result.startswith("durably queued:"))
        self.assertEqual(len(list((self.base / "modes/channel-outbox").glob("*.json"))), 1)

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

"""Crash points around the atomic successor/input journal and upstream ack."""
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from observed_strategy import Ledger
import helper
import input_ack


class TurnCheckpointTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MODE_TEST_CETTA_BIN"), "set MODE_TEST_CETTA_BIN")
    def test_native_ack_gate_and_recovery_preserve_later_arrival(self):
        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ, METTACLAW_TELEGRAM_SERVICE_SOCKET="",
            METTACLAW_ENGINE_STATE_PATH=str(self.root / "engine"),
            METTACLAW_TELEGRAM_LOG_PATH=str(self.root / "updates.jsonl"),
            PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "channels")]))
        result = subprocess.run([os.environ["MODE_TEST_CETTA_BIN"], "--lang", "petta",
            "--import-mode", "ancestor-walk", str(root / "tests/turn_checkpoint_probe.metta")],
            cwd=root, env=environment, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2500:])
        self.assertIn("TURN_CHECKPOINT_OK\n", result.stdout)
        self.assertNotIn("(Error ", result.stdout)
        self.assertNotIn("(assert ", result.stdout)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "state.sqlite3"
        env = mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY="1",
            METTACLAW_STRATEGY_PATH=str(self.path), METTACLAW_WORKING_SET_PATH=str(self.root / "working.json"))
        env.start(); self.addCleanup(env.stop)
        helper.turn_commit_reset()
        self.addCleanup(helper.turn_commit_reset)
        self.store = Ledger(self.path)
        self.store.accept("input:one", "message", kind="message")

    def test_fault_rolls_back_successor_and_input_settlement_together(self):
        event = self.store.event
        def fault(db, kind, **fields):
            if kind == "turn-checkpoint":
                raise RuntimeError("commit fault")
            event(db, kind, **fields)
        with mock.patch.object(self.store, "event", side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, "commit fault"):
                self.store.checkpoint({"saved_at": 1, "loops": 9}, ["101"], "input:one")
        self.assertIsNone(self.store.working_checkpoint())
        self.assertEqual(input_ack.already_committed("101"), 0)
        self.assertEqual(self.store.request_record("input:one")["processed"], 0)

    def test_crash_after_checkpoint_keeps_input_settled_without_ack(self):
        self.assertEqual(input_ack.committed(["101"]), 0)
        self.store.checkpoint({"saved_at": 1, "loops": 9, "lastresults": "saved"}, ["101"], "input:one")
        recovered = Ledger(self.path)
        recovered.restore()
        self.assertEqual(recovered.working_checkpoint()["lastresults"], "saved")
        self.assertEqual(input_ack.already_committed("101"), 1)
        self.assertEqual(input_ack.already_committed("102"), 0)
        self.assertEqual(recovered.request_record("input:one")["processed"], 1)
        self.assertFalse(input_ack.committed(["101", "102"]))

    def test_projection_failure_cannot_undo_committed_state(self):
        self.store.checkpoint({"saved_at": 1, "loops": 9})
        with mock.patch.object(helper.os, "replace", side_effect=OSError("projection failed")):
            self.assertEqual(helper.working_set_save(8, 3, "new feedback", 12), 1)
        self.assertEqual(helper._working_read()["loops"], 8)

    def test_unmanaged_input_is_never_mistaken_for_settled_input(self):
        with mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY="0"):
            self.assertEqual(input_ack.committed(["101"]), 1)
            self.assertEqual(input_ack.already_committed("101"), 0)


if __name__ == "__main__":
    unittest.main()

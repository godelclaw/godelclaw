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
import native_ledger


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
        native_ledger.start(self, self.root)
        self.path = self.root / "strategy"
        env = mock.patch.dict(os.environ, METTACLAW_OBSERVED_STRATEGY="1",
            METTACLAW_STRATEGY_PATH=str(self.path), METTACLAW_WORKING_SET_PATH=str(self.root / "working.json"))
        env.start(); self.addCleanup(env.stop)
        helper.turn_commit_reset()
        self.addCleanup(helper.turn_commit_reset)
        self.store = Ledger(self.path)
        self.store.accept("input:one", "message", kind="message")

    def test_fault_rolls_back_successor_and_input_settlement_together(self):
        call = input_ack._ledger_call
        def fault(verb, *arguments, **options):
            if verb == "checkpoint":
                raise RuntimeError("commit fault")
            return call(verb, *arguments, **options)
        with mock.patch.object(input_ack, "_ledger_call", side_effect=fault):
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

    def test_autonomous_defer_settles_notification_without_new_activity(self):
        from types import SimpleNamespace
        import lifecycle
        import strategy_host
        import task_context

        key = "input:completion"
        self.store.accept(key, "work completion", kind="notification")
        task_context.begin(self.store, key, "channel-event", chat="room")
        self.assertTrue(self.store.disposition(key, "defer", "inspect the completed work later"))
        self.store.accept("input:newer", "new unfinished input", kind="notification")
        self.store.accept("input:later", "genuine later work", kind="message")
        self.assertTrue(self.store.processed("input:later"))
        self.assertTrue(self.store.disposition("input:later", "defer", "not due yet"))
        self.store.accept("autonomous", "ongoing autonomous work", kind="autonomous")
        task_context.begin(self.store, "autonomous", "autonomous-trigger", key)

        def open_requests():
            # This is the native query behind task:conversation-projection's
            # limited open-request/open-work view. Focus the distinct active
            # input so an addressed completion is not retained as the focus.
            answer = input_ack._ledger_call("conversation-rows", ("input:newer", 0, 40000))
            return {row[0]: row[1] for row in answer[1]}

        self.assertEqual(open_requests(), {"input:one": "active", "input:completion": "deferred",
                                           "input:newer": "active", "input:later": "deferred",
                                           "autonomous": "active"})
        channel = SimpleNamespace(_current_batch_update_ids=set(), _reply_chat_id="room")
        with mock.patch.multiple(strategy_host, _ledger=self.store, _booted=True,
                                 _focus=None, _message_focus=None, _input_turn=False), \
                mock.patch.object(strategy_host, "guard", return_value="test-control"), \
                mock.patch.object(lifecycle, "cognition_enabled", return_value=1), \
                mock.patch.dict(sys.modules, {"telegram": channel}):
            # No _focus injection: begin recovers the native context focus.
            strategy_host.begin(2, "autonomous-trigger", "")
            self.assertEqual(strategy_host.request_disposition(key, "defer", "still later"),
                             "REQUEST_DISPOSITION_RECORDED")
            helper.turn_commit_stage(1, 1, 0)
            self.assertEqual(helper.turn_commit_ready(), 1)
            self.assertEqual(helper.working_set_save(9, 1, "resumed result", 12), 1)
            record = self.store.request_record(key)
            self.assertEqual((strategy_host._focus, record["processed"], record["status"]),
                             ("autonomous", 1, "deferred"))
            self.assertEqual(open_requests(), {"input:one": "active", "input:completion": "deferred",
                                               "input:newer": "active", "input:later": "deferred",
                                               "autonomous": "active"})
            self.assertEqual(strategy_host.request_disposition(key, "addressed", "reviewed"),
                             "REQUEST_DISPOSITION_RECORDED")
        self.assertEqual(self.store.request_record("input:newer")["processed"], 0)
        self.assertEqual(open_requests(), {"input:one": "active", "input:newer": "active",
                                           "input:later": "deferred", "autonomous": "active"})
        self.assertEqual(self.store.request_record("input:later")["status"], "deferred")

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

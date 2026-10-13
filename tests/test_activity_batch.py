"""Focused tests for transactional NEW-ACTIVITY delivery.

Covers stable observation under PeTTa re-entry, history-gated exact
acknowledgement, late arrivals, and restart recovery from the update ledger.
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, os.path.join(ROOT, "channels"))
import telegram


def _reset():
    with telegram._msg_lock:
        del telegram._pending_messages[:]
        del telegram._prepared_messages[:]
        telegram._activity_epoch = 0
        telegram._context_activity_epoch = 0
        telegram._reply_chat_id = ""
        telegram._last_message_is_human = False
        telegram._last_from_bot = False
        telegram._last_arm_tier = "full"
    with telegram._effect_lock:
        telegram._effect_turn = None
        telegram._effect_activity_epoch = None


class ActivityBatchTests(unittest.TestCase):
    def setUp(self):
        _reset()
        self.lifecycle = mock.patch("lifecycle.cognition_enabled",
                                    return_value=1)
        self.lifecycle.start()
        self.addCleanup(self.lifecycle.stop)

    def test_batch_prepares_all_chronologically_without_consuming(self):
        telegram._set_last("1", "first", from_bot=False, arm_tier="full")
        telegram._set_last("1", "second", from_bot=True)
        telegram._set_last("1", "third", from_bot=False, arm_tier="mid")
        batch = telegram.getActivityBatch()
        self.assertEqual(batch.splitlines(), ["first", "second", "third"])
        with telegram._msg_lock:
            self.assertEqual(len(telegram._pending_messages), 3)
            self.assertEqual(
                telegram._prepared_messages, telegram._pending_messages)

    def test_mixed_batch_routes_and_arms_from_newest_human(self):
        telegram._set_last("7", "human-early", from_bot=False,
                           arm_tier="mid")
        telegram._set_last("9", "bot-later", from_bot=True)
        telegram.getActivityBatch()
        self.assertEqual(telegram._reply_chat_id, "7")
        self.assertEqual(telegram.lastMessageIsHuman(), 1)
        self.assertEqual(telegram.lastMessageArmLoops(),
                         telegram._tier_loops("mid"))

    def test_bot_only_batch_does_not_arm_as_human(self):
        telegram._set_last("5", "bot-a", from_bot=True)
        telegram._set_last("5", "bot-b", from_bot=True)
        batch = telegram.getActivityBatch()
        self.assertIn("bot-a", batch)
        self.assertIn("bot-b", batch)
        self.assertEqual(telegram._reply_chat_id, "5")
        self.assertEqual(telegram.lastMessageIsHuman(), 0)

    def test_repeated_observation_is_stable_until_acknowledged(self):
        telegram._set_last("1", "only", from_bot=False)
        self.assertEqual(telegram.pendingActivityCount(), 1)
        self.assertEqual(telegram.preparedActivityCount(), 0)
        self.assertEqual(telegram.getActivityBatch(), "only")
        self.assertEqual(telegram.getActivityBatch(), "only")
        self.assertEqual(telegram.pendingActivityCount(), 1)
        self.assertEqual(telegram.preparedActivityCount(), 1)
        self.assertEqual(telegram.ackActivityBatch(), 1)
        self.assertEqual(telegram.pendingActivityCount(), 0)
        self.assertEqual(telegram.preparedActivityCount(), 0)
        self.assertEqual(telegram.getActivityBatch(), "")
        self.assertEqual(telegram.lastMessageIsHuman(), 0)

    def test_arrival_after_observation_survives_exact_prefix_ack(self):
        telegram._set_last("1", "prepared", update_id=10)
        self.assertEqual(telegram.getActivityBatch(), "prepared")
        telegram._set_last("1", "later", update_id=11)
        with mock.patch.object(telegram, "_write_activity_receipt",
                               return_value=True):
            self.assertEqual(telegram.ackActivityBatch(), 1)
        self.assertEqual(telegram.getActivityBatch(), "later")

    def test_receipt_failure_keeps_the_batch_pending(self):
        telegram._set_last("1", "retry me", update_id=20)
        self.assertEqual(telegram.getActivityBatch(), "retry me")
        with mock.patch.object(telegram, "_write_activity_receipt",
                               return_value=False):
            self.assertEqual(telegram.ackActivityBatch(), 0)
        self.assertEqual(telegram.getActivityBatch(), "retry me")

    def test_restart_recovers_only_logged_unacknowledged_inputs(self):
        old_log = telegram._log_path
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = pathlib.Path(directory) / "updates.jsonl"
                telegram._log_path = str(log)
                def inbound(update_id, text):
                    message = {
                        "message_id": update_id,
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 2, "username": "operator"},
                        "text": text,
                    }
                    update = {"update_id": update_id, "message": message}
                    self.assertTrue(telegram._append_update_log(
                        update, "message", message, True, True))
                inbound(30, "already handled")
                inbound(31, "survives restart")
                self.assertEqual(
                    telegram.initialize_activity_receipts([31]), 1)
                _reset()
                self.assertEqual(telegram._recover_pending_activity(), 1)
                self.assertIn("survives restart",
                              telegram.getActivityBatch())
                self.assertNotIn("already handled",
                                 telegram.getActivityBatch())
        finally:
            telegram._log_path = old_log

    def test_ack_receipt_prevents_restart_replay(self):
        old_log = telegram._log_path
        try:
            with tempfile.TemporaryDirectory() as directory:
                log = pathlib.Path(directory) / "updates.jsonl"
                telegram._log_path = str(log)
                message = {
                    "message_id": 40,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 2, "username": "operator"},
                    "text": "settled",
                }
                update = {"update_id": 40, "message": message}
                self.assertTrue(telegram._append_update_log(
                    update, "message", message, True, True))
                self.assertEqual(
                    telegram.initialize_activity_receipts([40]), 1)
                telegram._set_last("1", "settled", update_id=40)
                telegram.getActivityBatch()
                self.assertEqual(telegram.ackActivityBatch(), 1)
                _reset()
                self.assertEqual(telegram._recover_pending_activity(), 0)
                self.assertEqual(telegram.getActivityBatch(), "")
                kinds = [json.loads(line)["kind"]
                         for line in log.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(kinds[-1], "activity_ack")
        finally:
            telegram._log_path = old_log

    def test_new_stimulus_does_not_cancel_emitted_commands(self):
        telegram._set_last("1", "in prompt", from_bot=False)
        self.assertEqual(telegram.getActivityBatch(), "in prompt")
        telegram.begin_effect_turn("turn-1")
        self.assertEqual(telegram.effect_turn_stimulus_free("turn-1"), 1)
        telegram._set_last("1", "arrived during work", from_bot=False)
        self.assertEqual(telegram.effect_turn_stimulus_free("turn-1"), 1)

    def test_new_stimulus_stays_queued_across_reentry(self):
        telegram.getActivityBatch()
        telegram.begin_effect_turn("turn-1")
        telegram._set_last("1", "new", from_bot=False)
        telegram.begin_effect_turn("turn-1")
        self.assertEqual(telegram.effect_turn_stimulus_free("turn-1"), 1)

    def test_operator_revocation_interrupts_the_unexecuted_suffix(self):
        telegram.getActivityBatch()
        telegram.begin_effect_turn("turn-stop")
        with mock.patch("lifecycle.cognition_enabled", return_value=1):
            self.assertEqual(
                telegram.effect_turn_stimulus_free("turn-stop"), 1)
        with mock.patch("lifecycle.cognition_enabled", return_value=0):
            self.assertEqual(
                telegram.effect_turn_stimulus_free("turn-stop"), 0)

    def test_loop_derives_newness_from_nonempty_batch(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        self.assertIn('($message (eval (receive)))', loop)
        self.assertIn(
            '($hasActivity\n            (== (py-call (helper.text_nonempty $message)) 1))',
            loop)
        self.assertNotIn(
            "($msgnew (prog1 (!= $msg (get-state &prevmsg))", loop)

    def test_iter_renews_only_after_an_idle_wait(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        policy = (ROOT / "src" / "loop_policy.metta").read_text(
            encoding="utf-8")
        self.assertIn("(processLoop (initLoop))", loop)
        self.assertIn("(loop-autonomous-ready $periphery)", loop)
        self.assertIn("(policy-wait-seconds", policy)
        self.assertIn("(policy-next-autonomous-ready", policy)
        self.assertIn("AUTONOMOUS_POLICY_BURST", loop)
        self.assertIn("cognitive_health.expect_turn", loop)
        self.assertNotIn("telegram.getMode", loop)

    def test_lifecycle_gate_is_outside_the_enabled_turn(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        enabled_definition = loop.index("(= (enabledTurnCandidate $periphery)")
        receive = loop.index("($message (eval (receive)))", enabled_definition)
        wrapper = loop.index("(= (turnCandidate $periphery)")
        gate = loop.index("(lifecycle.cognition_enabled)", wrapper)
        enabled_call = loop.index("(enabledTurnCandidate $periphery)", gate)
        self.assertLess(enabled_definition, receive)
        self.assertLess(wrapper, gate)
        self.assertLess(gate, enabled_call)
        self.assertIn("(if $enabled\n                (applyTimedContinuation",
                      loop)

    def test_attention_graph_brackets_the_model_action(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        begin = loop.index("(attention-begin")
        model = loop.index("(addition-model-call")
        complete = loop.index("(attention-complete")
        self.assertLess(begin, model)
        self.assertLess(model, complete)

    def test_model_call_is_committed_before_response_parsing(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        model = loop.index("(addition-model-call")
        commitment = loop.index("($_ (cut))", model)
        parse = loop.index("(addition-read-response", model)
        self.assertLess(model, commitment)
        self.assertLess(commitment, parse)
        pipeline = (ROOT / "src" / "command_pipeline.metta").read_text(
            encoding="utf-8"
        )
        self.assertIn("(sread", pipeline)
        self.assertIn("((Error $a $b) ())", pipeline)

    def test_input_settlement_follows_effects_and_durable_history(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        model = loop.index("(addition-model-call")
        effects = loop.index("(addition-effect-broker", model)
        history = loop.index("(addToHistory", effects)
        acknowledgement = loop.index("(telegram.ackActivityBatch", history)
        settlement = loop.index("(cognitive_health.turn_settled",
                                acknowledgement)
        self.assertLess(model, effects)
        self.assertLess(effects, history)
        self.assertLess(history, acknowledgement)
        self.assertLess(acknowledgement, settlement)
        self.assertIn("(== $providerSucceeded 1)",
                      loop[effects:acknowledgement])

    def test_commands_cross_one_committed_effect_boundary(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        response = loop.index("(RESPONSE: $sexpr)")
        commitment = loop.index("($_ (cut))", response)
        dispatcher = loop.index("(addition-effect-broker", response)
        results = loop.index("($results (RESULTS:", response)
        self.assertLess(response, commitment)
        self.assertLess(commitment, dispatcher)
        self.assertLess(dispatcher, results)
        self.assertIn(
            "(policy-command-batch-limit (loop-active-policy $periphery))",
            loop[dispatcher:results],
        )
        self.assertNotIn("run-command-batch-once $iteration $sexpr", loop)
        self.assertNotIn("(collapse (let $s (superpose $sexpr)", loop)
        frontier = loop.index("(addition-effect-turn-begin $iteration)")
        self.assertLess(frontier, response)
        self.assertNotIn("(telegram.begin_effect_turn", loop)


if __name__ == "__main__":
    unittest.main()

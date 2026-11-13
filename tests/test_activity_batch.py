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
        telegram._activity_epoch = 0
        telegram._context_activity_epoch = 0
        telegram._reply_chat_id = ""
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

    def test_loop_derives_newness_from_nonempty_batch(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        self.assertIn('($message (eval (receive)))', loop)
        self.assertIn(
            '($hasActivity\n            (and (loop-host:nonempty? $message)',
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
        self.assertIn("(health:expect (loop-active-policy $ready)", loop)
        for retired in ("expect_turn", "turn_succeeded", "turn_settled"):
            self.assertNotIn("cognitive_health." + retired, loop)
        self.assertNotIn("telegram.getMode", loop)

    def test_lifecycle_gate_is_outside_the_enabled_turn(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        enabled_definition = loop.index("(= (enabledTurnCandidate $periphery)")
        receive = loop.index("($message (eval (receive)))", enabled_definition)
        wrapper = loop.index("(= (turnCandidate $periphery)")
        wrapper_end = loop.index("(= (protectedOutcome", wrapper)
        gate = loop.index("(case (lifecycle:enabled)", wrapper)
        enabled_call = loop.index("(enabledTurnCandidate $periphery)", gate)
        self.assertLess(enabled_definition, receive)
        self.assertLess(wrapper, gate)
        self.assertLess(gate, enabled_call)
        self.assertLess(enabled_call, wrapper_end)
        gated = loop[wrapper:wrapper_end]
        self.assertIn("(if (== $enabled 1)", gated)
        for effect in ("($message (eval (receive)))", "(addition-model-call",
                       "(settleActivityBatch)", "(tg:ack-activity-batch)"):
            self.assertNotIn(effect, gated)

        wait = loop.index("(= (waitCandidate $periphery)")
        wait_gate = loop.index("(case (lifecycle:enabled)", wait)
        authorized = loop.index(
            "(waitAuthorizedCandidate $periphery $policy (== $enabled 1))",
            wait_gate)
        continuation_guard = loop.index(
            "(if (and $enabled (not $recycling))", authorized)
        continuation = loop.index(
            "(applyTimedContinuation $periphery $timedOut)",
            continuation_guard)
        self.assertLess(wait, wait_gate)
        self.assertLess(wait_gate, authorized)
        self.assertLess(authorized, continuation_guard)
        self.assertLess(continuation_guard, continuation)

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
        acknowledgement = loop.index("(tg:ack-activity-batch)", history)
        settlement = loop.index("(health:settle)", acknowledgement)
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

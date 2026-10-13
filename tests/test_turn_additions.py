import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TurnAdditionShapeTests(unittest.TestCase):
    def test_weak_core_does_not_name_runtime_additions(self):
        core = (ROOT / "src" / "weak_process_core.metta").read_text(
            encoding="utf-8"
        )
        self.assertEqual(core.count("(= (process-step"), 2)
        self.assertNotIn("addition-", core)
        self.assertNotIn("context_sources", core)
        self.assertNotIn("synthetic_llm", core)
        self.assertNotIn("run-command-batch-once", core)

    def test_live_turn_delegates_to_named_outer_additions(self):
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        for name in (
            "addition-context-sources",
            "addition-effect-turn-begin",
            "addition-prepare-request",
            "addition-model-call",
            "addition-balance-response",
            "addition-envelope-response",
            "addition-read-response",
            "addition-command-sequence",
            "addition-effect-broker",
        ):
            self.assertIn(name, loop)
        self.assertNotIn("(py-call (context_sources.bundle))", loop)
        self.assertNotIn("(py-call (telegram.begin_effect_turn", loop)
        self.assertNotIn("(py-call (synthetic_llm.chat", loop)
        self.assertNotIn("(run-command-batch-once\n", loop)

    def test_shadow_and_live_share_the_same_parser_and_broker_adapters(self):
        shadow = (ROOT / "src" / "godel_shadow.py").read_text(
            encoding="utf-8"
        )
        for name in (
            "addition-balance-response",
            "addition-envelope-response",
            "addition-read-response",
            "addition-command-sequence",
            "addition-effect-broker",
        ):
            self.assertIn(name, shadow)

    def test_non_command_envelope_cannot_reach_the_effect_broker(self):
        pipeline = (ROOT / "src" / "command_pipeline.metta").read_text(
            encoding="utf-8"
        )
        self.assertIn("((no_commands_parsed) ())", pipeline)

    def test_protocol_failure_paces_without_spending_energy(self):
        policy = (ROOT / "src" / "loop_policy.metta").read_text(
            encoding="utf-8"
        )
        self.assertIn("(applyProtocolPacing", policy)
        self.assertIn("(max 1 $retry)", policy)
        loop = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        self.assertIn("(applyProtocolPacing (sleepInterval) 0", loop)


if __name__ == "__main__":
    unittest.main(verbosity=2)

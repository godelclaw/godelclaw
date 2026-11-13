"""The event ledger, not pins or a mutable source skill, carries continuity."""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContextContinuityTest(unittest.TestCase):
    # The window itself is covered natively by tests/telegram_window_probe.metta.

    def test_internal_history_append_does_not_use_public_mutation_skill(self):
        # The journal itself is covered by tests/unit/loop_host.metta.
        source = (ROOT / "src" / "memory.metta").read_text(encoding="utf-8")
        self.assertIn("(loop-host:history-append", source)
        self.assertNotIn("(append-file", source)

    def test_prompt_has_one_event_window_and_explicit_affect_gestalt(self):
        source = (ROOT / "src" / "loop.metta").read_text(encoding="utf-8")
        context = source[source.index("(= (getContext"):source.index(
            "(= (HandleError")]
        self.assertIn("addition-context-sources", context)
        self.assertEqual(context.count("addition-context-sources"), 1)
        additions = (ROOT / "src" / "turn_additions.metta").read_text(
            encoding="utf-8"
        )
        start = additions.index("(= (addition-context-sources)")
        definition = additions[start:additions.index("\n\n", start)]
        # One bundle per turn, built with the conversation the MeTTa Telegram
        # client renders; nothing else in the additions builds one.
        self.assertIn("(context-bundle:with-views $views $conversation)", definition)
        self.assertEqual(definition.count("context-bundle:with-views"), 1)
        self.assertEqual(additions.count("context-bundle:with-views"), 1)
        self.assertNotIn("tg:client-metta?", additions)
        self.assertNotIn("context_sources.bundle", additions)
        self.assertNotIn("(getHistory)", context)
        self.assertNotIn("telegram.recent_activity", context)
        self.assertNotIn("telegram.conversation_window", context)


if __name__ == "__main__":
    unittest.main(verbosity=2)

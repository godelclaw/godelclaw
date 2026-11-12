"""The event ledger, not pins or a mutable source skill, carries continuity."""

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "channels"))

import helper  # noqa: E402
import telegram  # noqa: E402


class ContextContinuityTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log = pathlib.Path(self.temporary.name) / "updates.jsonl"
        self.history = pathlib.Path(self.temporary.name) / "history.metta"
        self.old_log = telegram._log_path
        telegram._log_path = str(self.log)
        published = mock.patch.dict(telegram._published, dict(telegram._published))
        published.start()
        self.addCleanup(published.stop)
        self.old_history = os.environ.get("METTACLAW_HISTORY_PATH")
        os.environ["METTACLAW_HISTORY_PATH"] = str(self.history)
        self.old_health = os.environ.get("METTACLAW_TELEGRAM_HEALTH_PATH")
        os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = str(pathlib.Path(self.temporary.name) / "health.json")
        telegram._client_seen = None

    def tearDown(self):
        telegram._log_path = self.old_log
        if self.old_history is None:
            os.environ.pop("METTACLAW_HISTORY_PATH", None)
        else:
            os.environ["METTACLAW_HISTORY_PATH"] = self.old_history
        if self.old_health is None:
            os.environ.pop("METTACLAW_TELEGRAM_HEALTH_PATH", None)
        else:
            os.environ["METTACLAW_TELEGRAM_HEALTH_PATH"] = self.old_health
        telegram._client_seen = None

    def append(self, **fields):
        record = {
            "received_at": "2026-08-15T12:00:00+0200",
            "kind": "message",
            "allowed": True,
            "queued": True,
            "note": "",
            "chat_id": "7",
            "chat_title": "Room",
            "from": "@zar",
            "from_is_bot": False,
            "message_id": 1,
            "text": "text",
        }
        record.update(fields)
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def prepare(self, update_ids):
        """What the MeTTa client of this process publishes when it prepares a
        batch: the ledger's end as the context frontier, and its update ids."""
        path = pathlib.Path(telegram._client_path())
        fresh = path.with_name(path.name + ".tmp")
        fresh.write_text(json.dumps({"pid": os.getpid(), "context_frontier": self.log.stat().st_size,
                                     "batch_update_ids": ",".join(update_ids)}), encoding="utf-8")
        os.replace(fresh, path)

    def test_window_is_causal_and_current_batch_appears_once(self):
        self.append(update_id=10, message_id=10, text="prior human")
        self.append(kind="outbound", queued=False, note="own_send",
                    update_id=None, message_id=11, from_="me",
                    text="prior answer")
        self.append(update_id=12, message_id=12, text="current correction")
        self.prepare(["12"])
        # This arrives after the frontier and must wait for the next turn.
        self.append(update_id=13, message_id=13, text="future input")

        window = telegram.conversation_window()
        self.assertIn("prior human", window)
        self.assertIn("prior answer", window)
        self.assertNotIn("current correction", window)
        self.assertNotIn("future input", window)

    def test_control_chatter_is_not_conversation(self):
        self.append(update_id=20, note="slash_command:/activity",
                    text="/activity")
        self.append(kind="outbound", queued=False, note="own_send",
                    update_id=None, text="activity: idle | steps=0")
        self.append(update_id=21, text="real conversation")
        self.prepare([])
        window = telegram.conversation_window()
        self.assertNotIn("/activity", window)
        self.assertNotIn("steps=0", window)
        self.assertIn("real conversation", window)

    def test_message_edits_are_one_semantic_event_in_context(self):
        self.append(update_id=30, message_id=8, text="draft answer")
        self.append(update_id=31, kind="edited_message", message_id=8,
                    text="final answer")
        window = telegram.conversation_window()
        self.assertNotIn("draft answer", window)
        self.assertEqual(window.count("final answer"), 1)
        self.assertEqual(len(self.log.read_text(encoding="utf-8").splitlines()),
                         2)

    def test_message_identity_includes_chat(self):
        self.append(update_id=40, chat_id="7", chat_title="Room A",
                    message_id=9, text="from A")
        self.append(update_id=41, chat_id="8", chat_title="Room B",
                    message_id=9, text="from B")
        window = telegram.conversation_window()
        self.assertIn("from A", window)
        self.assertIn("from B", window)

    def test_future_edit_cannot_cross_the_context_frontier(self):
        self.append(update_id=50, message_id=10, text="visible now")
        frontier = mock.patch.object(telegram, "_context_frontier_bytes", self.log.stat().st_size, create=True)
        frontier.start()
        self.addCleanup(frontier.stop)
        self.append(update_id=51, kind="edited_message", message_id=10,
                    text="future revision")
        window = telegram.conversation_window()
        self.assertIn("visible now", window)
        self.assertNotIn("future revision", window)

    def test_current_edit_does_not_erase_prior_revision_early(self):
        self.append(update_id=60, message_id=11, text="prior revision")
        self.append(update_id=61, kind="edited_message", message_id=11,
                    text="current revision")
        self.prepare(["61"])
        window = telegram.conversation_window()
        self.assertIn("prior revision", window)
        self.assertNotIn("current revision", window)

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

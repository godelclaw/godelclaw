"""Topic notes survive restart without a worker/adoption protocol."""
import os, tempfile, unittest, json
from pathlib import Path
from unittest.mock import patch
import topic_jobs

class TopicNotesTest(unittest.TestCase):
    def test_notes_and_selection_survive_reopen(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"METTACLAW_STRATEGY_PATH":td+"/state.db"}):
            topic_jobs.select("research")
            topic_jobs.note("finding from source")
            self.assertIn("finding from source",topic_jobs.context())
            topic_jobs.select("other")
            self.assertEqual(topic_jobs.context(), "")
            topic_jobs.select("research")
            self.assertIn("finding from source",topic_jobs.context())
            self.assertEqual(json.loads(topic_jobs.topics())["active"], "research")
            self.assertEqual(topic_jobs.notify(),0)

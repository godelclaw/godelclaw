"""Separating tests for task/authority boundaries in retained context."""
from pathlib import Path
import sys
import unittest
import tempfile
import threading
import types
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import context_sources


class ContextScopeTests(unittest.TestCase):
    def setUp(self):
        self.projector = context_sources.Projector()
        self.spec = context_sources.SourceSpec("active-query-declaration", 6000)

    def project(self, epoch, value=None):
        def load():
            if value is None:
                raise OSError("source temporarily unavailable")
            return value
        return self.projector.project(((self.spec, load),), epoch)[0]

    def test_failed_loader_cannot_move_old_constraint_to_new_task(self):
        self.project("task-A", "Do not write pane 3 in task A")
        result = self.project("task-B")
        self.assertEqual((result.status, result.text), ("unavailable", ""))
        self.assertEqual(result.epoch, "task-B")

    def test_same_epoch_can_retain_known_evidence(self):
        self.project("task-A", "known observation")
        self.assertEqual(self.project("task-A").status, "stale-known")

    def test_instruction_revision_change_does_not_restore_old_rule(self):
        self.project("task-A/instructions-1", "old restriction")
        self.assertEqual(self.project("task-A/instructions-2").text, "")

    def test_returning_to_an_old_task_does_not_revive_a_cleared_projection(self):
        self.project("task-A", "old restriction")
        self.project("task-B", "new evidence")
        self.assertEqual(self.project("task-A").status, "unavailable")

    def test_empty_observation_is_still_explicit_absence(self):
        self.project("task-A", "old")
        self.assertEqual(self.project("task-A", "").status, "absent")
        self.assertEqual(self.project("task-A").status, "unavailable")

    def test_note_provenance_is_preserved_instead_of_claiming_operator_authority(self):
        result = context_sources.Observation("pins", "known", "No real terminal", "r", "e")
        text = self.projector.render_observations([result])
        self.assertIn("evidence=assistant-memory", text)
        self.assertIn("context-epoch=e", text)

    def test_all_rendered_sources_retain_their_captured_epoch(self):
        result = self.project("task-A", "scoped")
        self.project("task-B", "other")
        self.assertIn("context-epoch=task-A", self.projector.render_observations([result]))

    def test_generated_epoch_transition_cases(self):
        for before in range(8):
            for after in range(8):
                p = context_sources.Projector()
                p.project(((self.spec, lambda: "old"),), str(before))
                def fail(): raise OSError("missing")
                result = p.project(((self.spec, fail),), str(after))[0]
                self.assertEqual(result.status, "stale-known" if before == after else "unavailable")

    def test_reentrant_other_scope_is_not_relabelled_as_the_outer_scope(self):
        def nested():
            self.projector.project(((self.spec, lambda: "inner task"),), "inner")
            raise OSError("outer loader failed")
        result = self.projector.project(((self.spec, nested),), "outer")[0]
        self.assertEqual((result.status, result.text, result.epoch), ("unavailable", "", "outer"))

    def test_real_epoch_binds_task_workspace_and_instruction_bytes(self):
        host = types.SimpleNamespace(enabled=lambda: True, _lock=threading.RLock(), _focus="task-A")
        with tempfile.TemporaryDirectory() as directory:
            soul = Path(directory) / "soul.txt"
            soul.write_text("current instructions")
            with mock.patch.dict(sys.modules, {"strategy_host": host}), mock.patch.dict(
                    context_sources.os.environ, {"METTACLAW_PROMPT_PATH": str(soul), "METTACLAW_PROJECT_ROOT": directory}):
                first = context_sources.context_epoch()
                self.assertEqual(first, context_sources.context_epoch())
                host._focus = "task-B"
                self.assertNotEqual(first, context_sources.context_epoch())
                host._focus = "task-A"
                with mock.patch.dict(context_sources.os.environ, {"METTACLAW_PROJECT_ROOT": directory + "/other"}):
                    self.assertNotEqual(first, context_sources.context_epoch())
                soul.write_text("corrected instructions")
                self.assertNotEqual(first, context_sources.context_epoch())

    def test_unavailable_instruction_source_cannot_reuse_a_prior_epoch(self):
        host = types.SimpleNamespace(enabled=lambda: True, _lock=threading.RLock(), _focus="task-A")
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules, {"strategy_host": host}), mock.patch.dict(
                context_sources.os.environ, {"METTACLAW_PROMPT_PATH": directory + "/absent", "METTACLAW_PROJECT_ROOT": directory}):
            self.assertNotEqual(context_sources.context_epoch(), context_sources.context_epoch())


if __name__ == "__main__":
    unittest.main()

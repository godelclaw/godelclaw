"""Recover task evidence after interruption, compaction and a fresh process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
from observed_strategy import Ledger, encoded
import task_context
import admission_frontier


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite3"
        self.store = Ledger(self.path)
        self.store.accept("guide", "Explain the API conditions; distinguish source inspection from compilation.")
        task_context.begin(self.store, "guide", "operator-event", chat="research")

    def call(self, commands, outputs, request="guide"):
        router = dict(advertised=[c[0] for c in commands], executable=[c[0] for c in commands],
                      permitted=[c[0] for c in commands])
        snap = self.store.capture(request, "context", router, 0)
        task_context.proposal(self.store, snap["serial"], request, commands, "not run; reconsider")
        ticket = self.store.decide(snap["serial"], dict(kind="command", request=request,
            owner=snap["owner"], tool=commands[0][0], command=dict(commands=commands, limit=len(commands))), 0)
        with self.store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts(ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            for i, result in enumerate(outputs):
                db.execute("INSERT INTO command_attempts VALUES (?,?,?,?)", (ticket, i, encoded(commands[i]),
                           None if result is None else encoded(dict(disposition="returned", result=result))))
        if all(result is not None for result in outputs):
            self.store.report(ticket, encoded(outputs))
            receipt = self.store.protected(request)["results"][-1]
            self.store.observe([ticket], {ticket: [receipt]})
        return ticket

    def test_early_evidence_survives_where_the_old_projection_loses_it(self):
        self.call([["read-file", "contract.lean"]], ["EARLY_FACT: requires finite evidence"])
        for i in range(6):
            self.call([["read-file", "file-%s" % i]], ["unrelated observation %s" % i])
        self.assertNotIn("EARLY_FACT", encoded(self.store.protected("guide")))
        view = task_context.projection(self.store, "guide")
        self.assertIn("EARLY_FACT", encoded(view))
        self.assertEqual(len(view["transcript"]), 7)

    def test_answering_an_interruption_resumes_the_original_task_after_restart(self):
        self.call([["read-file", "contract.lean"]], ["retain this finding"])
        self.store.accept("question", "Quick question about the current task", kind="message")
        task_context.begin(self.store, "question", "operator-event", "guide", "research")
        self.call([["help", "read-file"]], ["help text"], "question")
        self.store.processed("question")
        self.assertTrue(self.store.disposition("question", "addressed", "answered"))
        fresh = Ledger(self.path)
        self.assertEqual(task_context.resume(fresh, task_context.current(fresh)), "guide")
        view = task_context.projection(fresh, "guide")
        self.assertEqual([r["id"] for r in view["requests"]], ["guide", "question"])
        self.assertIn("retain this finding", encoded(view))

    def test_denied_and_unexecuted_commands_have_individual_results(self):
        self.call([["read-file", "missing"], ["send", "premature claim"]], ["read failed: missing"])
        view = task_context.projection(self.store, "guide")
        self.assertEqual([p["disposition"] for p in view["transcript"]], ["returned", "not_run"])
        self.assertEqual(view["pending"][0]["command"], ["send", "premature claim"])
        identity = view["pending"][0]["id"]
        task_context.resolve(self.store, identity, "obsolete claim abandoned after failed read")
        self.assertFalse(task_context.projection(self.store, "guide")["pending"])
        self.assertEqual(len(self.store.projection()["actions"]), 1)

    def test_compaction_preserves_notes_pending_and_exact_source_recovery(self):
        text = "ž🙂 source observation " * 500
        self.call([["read-file", "large"]], [text])
        self.call([["read-file", "small"], ["send", "not executed"]], ["newest result"])
        original = task_context.projection(self.store, "guide")
        identity = original["transcript"][0]["id"]
        task_context.note(self.store, "guide", "Earlier source supports a conditional result, not unconditional success.", encoded([identity]))
        view = task_context.compact(task_context.projection(self.store, "guide"), 4500)
        self.assertLessEqual(len(encoded(view).encode()), 4500)
        self.assertTrue(view["transcript"][0]["compacted"])
        self.assertEqual(view["transcript"][-1]["disposition"], "not_run")
        self.assertTrue(view["notes"] and view["pending"])
        pieces, offset = [], 0
        while offset is not None:
            page = task_context.read(Ledger(self.path), identity, offset, 173)
            pieces.append(page["text"]); offset = page["next"]
        self.assertEqual(json.loads("".join(pieces))["result"], text)

    def test_archive_index_recovers_every_evicted_pair(self):
        for i in range(25):
            self.call([["read-file", str(i)]], ["result %s" % i])
        original = task_context.projection(self.store, "guide")
        view = task_context.compact(original, 1300)
        self.assertLessEqual(len(encoded(view).encode()), 1300)
        self.assertIsNotNone(view["archive"])
        identities, offset = [], 0
        while offset is not None:
            page = task_context.history(self.store, "guide", offset, 7)
            identities.extend(p["id"] for p in page["entries"]); offset = page["next"]
        self.assertEqual(identities, [p["id"] for p in original["transcript"]])

    def test_fresh_interpreter_recovers_original_request_and_uncertainty(self):
        self.call([["write-file", "artifact", "text"]], [None])
        code = "import task_context; from observed_strategy import Ledger,encoded; s=Ledger(%r); s.restore(); print(encoded(task_context.projection(s,'guide')))" % str(self.path)
        result = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, PYTHONPATH=str(ROOT/"src")),
                                capture_output=True, text=True, check=True)
        view = json.loads(result.stdout)
        self.assertEqual(view["transcript"][0]["disposition"], "uncertain")
        self.assertEqual(view["requests"][0]["content"], self.store.request_record("guide")["content"])

    def test_task_roots_do_not_share_tool_history(self):
        self.call([["read-file", "private-a"]], ["task A observation"])
        self.store.accept("other", "unrelated task")
        task_context.begin(self.store, "other", "operator-event")
        self.assertNotIn("task A observation", encoded(task_context.projection(self.store, "other")))

    def test_large_request_uses_a_recoverable_frame_without_overflow(self):
        self.store.accept("large", "original constraint " * 10000)
        task_context.begin(self.store,"large","operator-event")
        original = task_context.projection(self.store,"large")
        view = task_context.compact(original,1800)
        self.assertLessEqual(len(encoded(view).encode()),1800)
        self.assertTrue(view["frame"]["compacted"])
        parts,offset=[],0
        while offset is not None:
            page=task_context.read(self.store,"task-frame:large",offset)
            parts.append(page["text"]);offset=page["next"]
        self.assertEqual(json.loads("".join(parts))["requests"],original["requests"])

    def test_hard_link_targets_are_not_independent_writes(self):
        first=Path(self.tmp.name)/"first";second=Path(self.tmp.name)/"second"
        first.write_text("old");second.hardlink_to(first)
        self.assertTrue(admission_frontier.needs_observation(["write-file",str(second),"new"],[["write-file",str(first),"other"]]))

    def test_dependency_boundary_is_not_an_action_count(self):
        before = [["read-file", str(i)] for i in range(30)]
        self.assertFalse(admission_frontier.needs_observation(["read-file", "last"], before))
        self.assertTrue(admission_frontier.needs_observation(["send", "I checked everything"], before))
        writes = [["write-file", str(Path(self.tmp.name)/str(i)), "fixed text"] for i in range(30)]
        for i, command in enumerate(writes):
            self.assertFalse(admission_frontier.needs_observation(command, writes[:i]))
        self.assertTrue(admission_frontier.needs_observation(writes[0], writes[1:]+writes[:1]))


if __name__ == "__main__":
    unittest.main()

"""Continuous history and durable handoffs over real SQLite/native HTTP.

The provider is a local scripted fixture. No credentials or external models.
"""
import json
import math
import os
from pathlib import Path
import sqlite3
import unittest

import test_native_llm_boundary as boundary
import test_task_context as reference

BASE_PROMPT = "soul_cache_boundary_tools_cache_boundary_conversation_cache_boundary_now"


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class ContextCompactionTests(unittest.TestCase):
    run_guest = boundary.NativeBoundaryTests.run_guest
    tearDown = boundary.NativeBoundaryTests.tearDown

    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.peer.delay = 0
        self.fixture = reference.TaskContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tmp.cleanup)
        self.env.update(METTACLAW_STRATEGY_PATH=str(self.fixture.path),
                        METTACLAW_LLM_COMPLETION_PATH=str(self.directory / "completion.json"))
        self.health = self.directory / "health.json"
        self.completion = self.directory / "completion.json"
        self.health.write_text('{"fixture":"parent turn must remain unchanged"}\n')
        self.completion.write_text('{"fixture":"parent completion must remain unchanged"}\n')
        self.original_health = self.health.read_bytes()
        self.original_completion = self.completion.read_bytes()

    def populate(self, count=60, request="guide"):
        # This is a running continuity installation. Legacy first-start
        # migration is tested separately with no saved checkpoint.
        with self.fixture.store.transaction() as db:
            root = "conversation:" + self.fixture.store.get(db, "owner")
            db.execute("INSERT OR IGNORE INTO meta VALUES(?,?)", ("task_window:" + root,
                json.dumps({"version": 1, "start": 0, "summary": ""})))
        for n in range(count):
            self.fixture.call([["read-file", str(n)]], [("FINDING_%d λ " % n) * 200], request)

    def render(self, name="view", capacity=20000, focus="guide"):
        out = self.directory / (name + ".txt")
        q = json.dumps
        result = self.run_guest('!(import! &self "' + str(boundary.ROOT / 'src/context_compaction.metta') + '")\n'
            '!(let $result (context:prepare '
            + q(BASE_PROMPT) + ' '
            + q(focus) + ' "continuation-tick" ' + q(str(capacity)) + ' "fixture-model" 256 "default") '
            '(case $result (((ContextPreparationFailed $why) '
            '(fs:update-text ' + q(str(out)) + ' "replace" (str:concat "WITHHELD: " $why) "" "")) '
            '($text (fs:update-text ' + q(str(out)) + ' "replace" $text "" "")))))\n'
            '!(println! COMPACTION_OK)\n')
        self.assertIn("COMPACTION_OK", result)
        self.assertTrue(out.is_file(), result + self.last_stderr)
        return out.read_text()

    def seed_recent(self, capacity=20000, focus="guide"):
        # Match preparation's base and render reserve; no topic notes here.
        available = capacity - math.ceil(len((BASE_PROMPT + "\n").encode()) / 2.5) - 128
        return self.run_guest('!(import! &self ' + json.dumps(str(boundary.ROOT / 'src/context_compaction.metta')) + ')\n'
            '!(let $view (task:conversation-view ' + json.dumps(focus) + ' "continuation-tick") '
            '(println! (SEED (context:seed-recent $view ' + str(available) + ' 2.5))))\n')

    def checkpoint(self):
        with self.fixture.store.transaction() as db:
            owner = self.fixture.store.get(db, "owner")
            row = db.execute("SELECT value FROM meta WHERE key=?", ("task_window:conversation:" + owner,)).fetchone()
            return json.loads(row[0]) if row else None

    def assert_parent_unchanged(self):
        self.assertEqual(self.health.read_bytes(), self.original_health)
        self.assertEqual(self.completion.read_bytes(), self.original_completion)

    def test_bootstrap_batches_and_restart_carry_handoff_and_original_ids(self):
        self.populate()
        original = reference.task_context.conversation_projection(self.fixture.store, "guide")
        self.peer.reply["choices"][0]["message"]["content"] = "HANDOFF_ONE: preserve findings and unfinished guide task."
        rendered = self.render()
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("HANDOFF_ONE", rendered)
        first = self.checkpoint()
        self.assertGreater(first["start"], 0)
        self.assertIn(first["first_source"], {p["id"] for p in original["transcript"]})
        self.assertIn(original["transcript"][0]["id"], self.peer.requests[0][2]["messages"][-1]["content"])
        self.assertIn(first["last_source"], {p["id"] for p in original["transcript"]})
        self.assertTrue(first["call_id"])
        self.assertEqual(len(self.peer.requests), 1)
        self.assertIn("UNFOLDED_HISTORY", rendered)
        for _, _, request in self.peer.requests:
            prompt = "\n".join(m["content"] for m in request["messages"])
            self.assertLessEqual(len(prompt.encode()) / 2.5, 0.9 * 20000)
        self.assertNotIn("HANDOFF_ONE", self.peer.requests[0][2]["messages"][-1]["content"])
        with sqlite3.connect(self.directory / "ledger.db") as db:
            self.assertTrue(all(r[0] == "completed" for r in db.execute("SELECT state FROM native_calls")))
        self.assert_parent_unchanged()

        # New unrelated input and a fresh evaluator retain the earlier handoff.
        self.fixture.store.accept("other", "OTHER_TASK_CONSTRAINT: do not abandon the guide task")
        reference.task_context.begin(self.fixture.store, "other", "channel-event", previous="guide")
        self.populate(40, "other")
        before = len(self.peer.requests)
        self.peer.reply["choices"][0]["message"]["content"] = "HANDOFF_TWO: includes HANDOFF_ONE and the new task."
        rendered = self.render("second", focus="other")
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("HANDOFF_TWO", rendered)
        self.assertIn("HANDOFF_ONE", self.peer.requests[before][2]["messages"][-1]["content"])
        self.assertIn("OTHER_TASK_CONSTRAINT", rendered)
        self.assertGreater(self.checkpoint()["start"], first["start"])
        self.assertEqual(reference.task_context.history(self.fixture.store, "guide")["total"], 60)
        identity = original["transcript"][1]["id"]
        self.assertIn("FINDING_0", json.loads(reference.task_context.read(self.fixture.store, identity)["text"])["result"])
        self.assert_parent_unchanged()

    def test_incomplete_or_empty_handoff_never_advances_the_frontier(self):
        self.populate(25)
        for finish, text in (("length", "partial handoff"), ("stop", "  ")):
            with self.subTest(finish=finish):
                with self.fixture.store.transaction() as db:
                    db.execute("DELETE FROM meta WHERE key LIKE 'context_failure:%'")
                self.peer.reply["choices"][0].update(finish_reason=finish,
                    message={"role": "assistant", "content": text})
                result = self.render(finish, capacity=10000)
                self.assertNotIn("WITHHELD:", result)
                self.assertIn("UNFOLDED_HISTORY", result)
                self.assertEqual(self.checkpoint()["start"], 0)
                self.assertEqual(self.checkpoint()["summary"], "")
                self.assertEqual(reference.task_context.history(self.fixture.store, "guide")["total"], 25)
                self.assert_parent_unchanged()

    def test_transport_failure_is_not_a_completed_parent_turn(self):
        self.populate(25)
        self.peer.status = 503
        result = self.render(capacity=10000)
        self.assertNotIn("WITHHELD:", result)
        self.assertIn("UNFOLDED_HISTORY", result)
        self.assertEqual(self.checkpoint()["start"], 0)
        self.assert_parent_unchanged()

    def test_received_transient_failure_waits_then_recovers(self):
        import time
        self.populate(25)
        self.peer.status, self.peer.retry_after = 503, 120
        began = time.time()
        first = self.render(capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", first)
        self.assertEqual(len(self.peer.requests), 1)
        with self.fixture.store.transaction() as db:
            key, raw = db.execute("SELECT key,value FROM meta WHERE key LIKE 'context_failure:%'").fetchone()
            failure = json.loads(raw)
            self.assertGreaterEqual(failure["retry_at"], began + 120)
        second = self.render("still-waiting", capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", second)
        self.assertEqual(len(self.peer.requests), 1)
        with self.fixture.store.transaction() as db:
            failure["retry_at"] = time.time() - 1
            db.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(failure), key))
        self.peer.status = 200
        self.peer.reply["choices"][0]["message"]["content"] = "RECOVERED_HANDOFF with original findings."
        third = self.render("recovered", capacity=10000)
        self.assertIn("RECOVERED_HANDOFF", third)
        self.assertEqual(len(self.peer.requests), 2)
        self.assertGreater(self.checkpoint()["start"], 0)
        self.assert_parent_unchanged()

    def test_internal_handoff_is_not_a_public_commentary_turn(self):
        import public_worklog
        worklog = self.directory / "public"
        public_worklog.transact(worklog, "on")
        self.env.update(METTACLAW_MODEL_COMMENTARY="1", METTACLAW_PUBLIC_WORK_DIR=str(worklog),
                        METTACLAW_PUBLIC_WORK_LIB="")
        self.populate(25)
        self.peer.reply["choices"][0]["message"]["content"] = "PRIVATE_HANDOFF: retain the task evidence."
        result = self.render(capacity=10000)
        self.assertNotIn("WITHHELD:", result)
        self.assertIn("PRIVATE_HANDOFF", result)
        self.assertEqual(public_worklog.transact(worklog, "read")[1], [])
        self.assert_parent_unchanged()

    def test_topic_notes_are_budgeted_once_before_compaction(self):
        self.populate(12)
        topic_note = "TOPIC_NOTE_START:" + "t" * 25000 + ":TOPIC_NOTE_END"
        with self.fixture.store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS topic_contexts(name TEXT PRIMARY KEY,revision INTEGER,notes TEXT)")
            db.execute("INSERT INTO topic_contexts VALUES('topic',1,?)", (topic_note,))
            db.execute("INSERT OR REPLACE INTO meta VALUES('active_topic',?)", (json.dumps("topic"),))
        self.peer.reply["choices"][0]["message"]["content"] = "HANDOFF_WITH_TOPIC: preserve findings."
        result = self.render(capacity=20000)
        self.assertNotIn("WITHHELD:", result)
        self.assertTrue("HANDOFF_WITH_TOPIC" in result, result[:300])
        self.assertEqual(result.count("TOPIC_NOTE_START:"), 1)
        self.assertGreater(len(self.peer.requests), 0)
        self.assertLessEqual(len(result.encode()) / 2.5, 20000)
        self.assert_parent_unchanged()

    def test_escape_heavy_input_never_produces_an_oversized_success(self):
        self.fixture.store.accept("escaped", "\\" * 35000)
        reference.task_context.begin(self.fixture.store, "escaped", "operator-event")
        result = self.render(capacity=20000, focus="escaped")
        if not result.startswith("WITHHELD:"):
            self.assertLessEqual(len(result.encode()) / 2.5, 20000)
            self.assertIn("task-input:escaped", result)

    def test_handoff_source_reuses_identical_results_without_losing_occurrences(self):
        body = "IDENTICAL_SOURCE_BODY " * 100
        self.fixture.call([["read-file", "a"]], [body])
        self.fixture.call([["read-file", "a"]], [body])
        pairs = reference.task_context.conversation_projection(self.fixture.store, "guide")["transcript"]
        out = self.directory / "handoff-source.txt"
        self.run_guest('!(import! &self ' + json.dumps(str(boundary.ROOT / 'src/context_compaction.metta')) + ')\n'
            '!(let* (($view (task:conversation-view "guide" "continuation-tick")) '
            '($prompt (context:summary-prompt "" (task:items $view "transcript") ()))) '
            '(fs:update-text ' + json.dumps(str(out)) + ' "replace" $prompt "" ""))\n')
        prompt = out.read_text()
        self.assertEqual(prompt.count(body), 1)
        self.assertIn("same result as step 1", prompt)
        for pair in pairs:
            self.assertIn(pair["id"], prompt)

    def test_failed_maintenance_does_not_acknowledge_or_repeat_paid_work(self):
        self.populate(25)
        self.peer.reply["choices"][0]["finish_reason"] = "length"
        with self.fixture.store.transaction() as db:
            processed = db.execute("SELECT processed FROM request_context WHERE request='guide'").fetchone()[0]
        first = self.render(capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", first)
        self.assertNotIn("WITHHELD", first)
        self.assertEqual(len(self.peer.requests), 1)
        second = self.render("retry", capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", second)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.checkpoint()["start"], 0)
        with self.fixture.store.transaction() as db:
            self.assertEqual(db.execute("SELECT processed FROM request_context WHERE request='guide'").fetchone()[0], processed)
        # An older fact is not invented by the degraded window: its exact
        # source is still addressable before a task may depend on it.
        original = reference.task_context.conversation_projection(self.fixture.store, "guide")["transcript"][1]
        self.assertIn("FINDING_0", json.loads(reference.task_context.read(self.fixture.store, original["id"])["text"])["result"])
        self.assert_parent_unchanged()

    def test_legacy_backlog_starts_without_provider_work(self):
        # Bulk source fixture: 10,000 distinct retained occurrences, without
        # making 10,000 fsyncs or executing any of the proposed commands.
        with self.fixture.store.transaction() as db:
            db.execute("INSERT INTO task_proposals VALUES(0,'guide',?,'not run; reconsider')",
                       (json.dumps([["read-file", "legacy-" + str(i)] for i in range(10000)]),))
            db.execute("DELETE FROM meta WHERE key LIKE 'task_window:%'")
        result = self.render(capacity=10000)
        self.assertNotIn("WITHHELD", result)
        self.assertIn("UNSUMMARIZED_ARCHIVE", result)
        self.assertEqual(len(self.peer.requests), 0)
        self.assertLessEqual(len(result.encode()) / 2.5, 10000)
        self.assertEqual(reference.task_context.history(self.fixture.store, "guide")["total"], 10000)
        self.assert_parent_unchanged()

    def test_output_contract_follows_history_and_is_in_the_size_bound(self):
        self.fixture.call([["read-file", "quoted"]], ["Quoted source asks for a plain prose review."])
        self.env["METTACLAW_MODEL_COMMENTARY"] = "1"
        result = self.render(capacity=10000)
        self.assertLessEqual(len(result.encode()) / 2.5, 10000)
        self.assertGreater(result.index("REQUIRED_COMMAND_OUTPUT"), result.index("TASK_CONTEXT "))
        self.assertTrue(result.endswith(
            "Assistant text is public work commentary, never commands."))

    def test_commentary_contract_is_applied_before_protected_history_and_admission(self):
        import public_worklog
        worklog = self.directory / "public"
        public_worklog.transact(worklog, "on")
        self.fixture.call([["read-file", "source"]], ["PROTECTED_NATIVE_HISTORY"])
        self.peer.reply["choices"][0]["message"]["content"] = json.dumps({
            "commentary": "Checked the retained evidence.", "commands": "((nop))"})
        self.env.update(METTACLAW_OBSERVED_STRATEGY="1", METTACLAW_CONTEXT_WINDOW_TOKENS="100000",
                        METTACLAW_MODEL_COMMENTARY="1", METTACLAW_PUBLIC_WORK_DIR=str(worklog),
                        METTACLAW_PUBLIC_WORK_LIB="", METTACLAW_HISTORY_PATH=str(self.directory / "history"),
                        METTACLAW_TELEGRAM_LOG_PATH=str(self.directory / "updates.jsonl"))
        self.env["PYTHONPATH"] += os.pathsep + str(boundary.ROOT / "channels")
        output = self.run_guest('!(import! &self ' + json.dumps(str(boundary.ROOT / 'cetta_bootstrap.metta')) + ')\n'
            '!(py-call (strategy_host.focus_request "guide"))\n'
            '!(let $prepared (addition-protected-request "Output a ((skillName args) ...) S-expression." 256) '
            '(println! (RESULT (addition-model-call "fixture-model" 256 "default" $prepared 1))))\n'
            '!(println! (SUCCESS (py-call (cognitive_health.turn_succeeded))))\n')
        self.assertIn("(RESULT ((nop)))", output)
        self.assertIn("(SUCCESS 1)", output)
        self.assertEqual(len(self.peer.requests), 1)
        wire = json.dumps(self.peer.requests[0][2])
        self.assertIn("PROTECTED_NATIVE_HISTORY", wire)
        self.assertIn("advertised metta tool exactly once", wire)
        self.assertEqual(self.peer.requests[0][2]["tools"][0]
                         ["function"]["name"], "metta")
        self.assertEqual([n["text"] for n in public_worklog.transact(worklog, "read")[1]],
                         ["Checked the retained evidence."])

    def test_stale_publication_cannot_separate_summary_and_cursor(self):
        root = reference.task_context.conversation_projection(self.fixture.store, "guide")["root"]
        output = self.run_guest('!(import! &self "' + str(boundary.ROOT / 'src/context_compaction.metta') + '")\n'
            '!(println! (context:publish ' + json.dumps(root) + ' sqlite:null 10 "GOOD" "c1" "first" "last" "digest"))\n'
            '!(println! (context:publish ' + json.dumps(root) + ' sqlite:null 20 "STALE" "c2" "x" "y" "z"))\n')
        self.assertIn("ContextPublished", output)
        self.assertIn("context-checkpoint-changed", output)
        checkpoint = self.checkpoint()
        self.assertEqual((checkpoint["start"], checkpoint["summary"], checkpoint["call_id"]), (10, "GOOD", "c1"))
        self.assertEqual(len(self.peer.requests), 0)

    def test_explicit_recent_migration_keeps_paid_prefix_and_rejects_stale_publish(self):
        self.populate()
        original = reference.task_context.conversation_projection(self.fixture.store, "guide")
        root = original["root"]
        paid = {"version": 1, "start": 3, "summary": "PAID_PREFIX: retain earlier findings.",
                "call_id": "paid-call", "first_source": original["transcript"][0]["id"],
                "last_source": original["transcript"][2]["id"], "source_digest": "paid-digest"}
        old_raw = json.dumps(paid)
        with self.fixture.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("task_window:" + root, old_raw))
        output = self.seed_recent()
        self.assertIn("ContextPublished", output)
        checkpoint = self.checkpoint()
        self.assertGreater(checkpoint["start"], paid["start"])
        self.assertEqual(checkpoint["archive_gap"], {
            "from": paid["start"], "until": checkpoint["start"], "status": "not-summarized"})
        for key in ("summary", "call_id", "first_source", "last_source", "source_digest"):
            self.assertEqual(checkpoint[key], paid[key])
        stale = self.run_guest('!(import! &self ' + json.dumps(str(boundary.ROOT / 'src/context_compaction.metta')) + ')\n'
            '!(println! (context:publish ' + json.dumps(root) + ' ' + json.dumps(old_raw)
            + ' 4 "OBSOLETE" "stale-call" "x" "y" "z"))\n')
        self.assertIn("context-checkpoint-changed", stale)
        self.assertEqual(self.checkpoint(), checkpoint)
        self.assertIn("context-migration-already-seeded", self.seed_recent())
        self.assertEqual(self.checkpoint(), checkpoint)
        rendered = self.render()
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("PAID_PREFIX", rendered)
        self.assertIn("UNSUMMARIZED_ARCHIVE (operator migration; not a model-authored summary)", rendered)
        self.assertIn('"task-history","' + root + '",3', rendered)
        self.assertLessEqual(len(rendered.encode()) / 2.5, 20000)
        self.assertEqual(len(self.peer.requests), 0)
        self.assertEqual(reference.task_context.history(self.fixture.store, "guide")["total"], 60)
        self.assert_parent_unchanged()

    def test_empty_seed_has_no_model_authored_handoff_and_gap_survives_later_summary(self):
        self.populate()
        self.assertIn("ContextPublished", self.seed_recent())
        seeded = self.checkpoint()
        self.assertEqual(seeded["summary"], "")
        gap = seeded["archive_gap"]
        self.assertEqual(gap["from"], 0)
        self.assertEqual(gap["until"], seeded["start"])
        rendered = self.render()
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("UNSUMMARIZED_ARCHIVE", rendered)
        self.assertNotIn("CONTEXT_HANDOFF (model-authored;", rendered)
        self.assertLessEqual(len(rendered.encode()) / 2.5, 20000)
        self.assertEqual(len(self.peer.requests), 0)

        self.populate(40)
        self.peer.reply["choices"][0]["message"]["content"] = "RECENT_HANDOFF: summarizes only the subsequent source records."
        rendered = self.render("after-migration")
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("RECENT_HANDOFF", rendered)
        self.assertIn("UNSUMMARIZED_ARCHIVE", rendered)
        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["archive_gap"], gap)
        self.assertGreater(checkpoint["start"], gap["until"])
        self.assertGreater(len(self.peer.requests), 0)
        for _, _, request in self.peer.requests:
            prior = "\n".join(message["content"] for message in request["messages"])
            self.assertIn("UNSUMMARIZED_ARCHIVE", prior)
            self.assertIn("were not summarized", prior)
        self.assertTrue(checkpoint["call_id"])
        self.assertEqual(reference.task_context.history(self.fixture.store, "guide")["total"], 100)
        self.assert_parent_unchanged()

    def test_invalid_gap_cannot_hide_history_behind_an_empty_summary(self):
        self.fixture.call([["read-file", "first"]], ["UNSUMMARIZED_SOURCE_MUST_REMAIN_VISIBLE"])
        root = reference.task_context.conversation_projection(self.fixture.store, "guide")["root"]
        for gap in (None, {"from": 0, "until": 1, "status": "summarized"},
                    {"from": 1, "until": 1, "status": "not-summarized"},
                    {"from": 0, "until": 43, "status": "not-summarized"},
                    {"from": 1, "until": 42, "status": "not-summarized"},
                    {"from": 0, "until": 41, "status": "not-summarized"}):
            with self.subTest(gap=gap):
                checkpoint = {"start": 42, "summary": "", "archive_gap": gap}
                with self.fixture.store.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("task_window:" + root, json.dumps(checkpoint)))
                rendered = self.render(capacity=100000)
                self.assertNotIn("WITHHELD:", rendered)
                self.assertIn("UNSUMMARIZED_SOURCE_MUST_REMAIN_VISIBLE", rendered)
                self.assertNotIn("UNSUMMARIZED_ARCHIVE", rendered)
        self.assertEqual(len(self.peer.requests), 0)
        self.assert_parent_unchanged()

    def test_recent_migration_rejects_a_newest_record_that_cannot_fit(self):
        self.fixture.store.accept("large", "λ" * 35000)
        reference.task_context.begin(self.fixture.store, "large", "operator-event")
        output = self.seed_recent(focus="large")
        self.assertIn("context-migration-window-exceeds-capacity", output)
        self.assertIsNone(self.checkpoint())
        self.assertEqual(len(self.peer.requests), 0)
        self.assert_parent_unchanged()

    def test_old_numeric_trim_cursor_does_not_hide_unsummarized_evidence(self):
        self.fixture.call([["read-file", "first"]], ["ORIGINAL_UNSUMMARIZED_FINDING"])
        root = reference.task_context.conversation_projection(self.fixture.store, "guide")["root"]
        with self.fixture.store.transaction() as db:
            db.execute("INSERT INTO meta VALUES(?,?)", ("task_window:" + root, "42"))
        rendered = self.render(capacity=100000)
        self.assertIn("ORIGINAL_UNSUMMARIZED_FINDING", rendered)
        self.assertIn("Explain the API conditions", rendered)
        self.assertEqual(len(self.peer.requests), 0)
        self.assert_parent_unchanged()


if __name__ == "__main__":
    unittest.main()

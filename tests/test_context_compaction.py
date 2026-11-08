"""Continuous history and durable handoffs over the native ledger and native HTTP.

The provider is a local scripted fixture. No credentials or external models.
"""
import json
import math
import os
import re
from pathlib import Path
import unittest
import uuid

import test_native_llm_boundary as boundary
import test_task_context as reference
import input_ack  # on the path once the boundary suite is imported

BASE_PROMPT = "soul_cache_boundary_tools_cache_boundary_conversation_cache_boundary_now"


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class ContextCompactionTests(unittest.TestCase):
    run_guest = boundary.NativeBoundaryTests.run_guest
    tearDown = boundary.NativeBoundaryTests.tearDown
    records = boundary.NativeBoundaryTests.records
    column = boundary.NativeBoundaryTests.column

    def setUp(self):
        boundary.NativeBoundaryTests.setUp(self)
        self.peer.delay = 0
        self.fixture = reference.TaskContextTests()
        self.fixture.socket = self.socket  # the ledger the boundary's setUp started
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env.update(METTACLAW_STRATEGY_PATH=str(self.fixture.path),
                        METTACLAW_LLM_COMPLETION_PATH=str(self.directory / "completion.json"))
        self.health = self.directory / "health.json"
        self.completion = self.directory / "completion.json"
        self.health.write_text('{"fixture":"parent turn must remain unchanged"}\n')
        self.completion.write_text('{"fixture":"parent completion must remain unchanged"}\n')
        self.original_health = self.health.read_bytes()
        self.original_completion = self.completion.read_bytes()

    @staticmethod
    def meta(key):
        """The JSON text of one meta key of the native ledger, or None."""
        return dict(input_ack.meta_prefix(key)).get(key)

    @staticmethod
    def meta_put(key, text):
        input_ack._ledger_call("meta-put", (key, text, uuid.uuid4().hex))

    def populate(self, count=60, request="guide"):
        # This is a running continuity installation. Legacy first-start
        # migration is tested separately with no saved checkpoint.
        root = "conversation:" + self.fixture.store._native_meta()["owner"]
        if self.meta("task_window:" + root) is None:
            self.meta_put("task_window:" + root, json.dumps({"version": 1, "start": 0, "summary": ""}))
        for n in range(count):
            self.fixture.call([["read-file", str(n)]], [("FINDING_%d λ " % n) * 200], request)

    def test_provider_size_rejection_repairs_only_while_checkpoint_advances(self):
        self.populate(8)
        self.env['METTACLAW_OBSERVED_STRATEGY'] = '1'
        forms = f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
        def send(text):
            call = '!(println! (RESULT (llm:command "fixture-model" 256 "default" ' + json.dumps(text, ensure_ascii=False) + ')))\n'
            return self.run_guest(forms + call)
        initial = self.render('initial', capacity=200000)
        def source_count():
            out = self.run_guest(f'!(import! &self "{boundary.ROOT}/src/context_compaction.metta")\n'
                '!(println! (SOURCE_COUNT (length (task:items (task:conversation-view "guide" "continuation-tick") "transcript"))))\n')
            return int(re.search(r'\(SOURCE_COUNT (\d+)\)', out).group(1))
        error = {'error': {'code': 'context_length_exceeded', 'message': 'Context too long'}}
        rendered, count, bodies = initial, source_count(), []
        initial_count = count
        for turn in range(initial_count):
            self.peer.status, self.peer.reply = 400, error
            send(rendered)
            bodies.append(json.dumps(self.peer.requests[-1][2], sort_keys=True))
            self.assertEqual(json.loads(self.health.read_text())['provider_rejection']['repair'], 'requested')
            before, calls = self.checkpoint()['start'], len(self.peer.requests)
            self.peer.status = 200
            self.peer.reply = {'choices': [{'message': {'role': 'assistant', 'content': 'HANDOFF retained findings'},
                                           'finish_reason': 'stop'}]}
            next_view = self.render('repair-%d' % turn, capacity=200000)
            if count <= 1:
                self.assertIn('no-older-history-to-fold', next_view)
                send(rendered)
                self.assertEqual(len(self.peer.requests), calls)
                break
            self.assertGreater(self.checkpoint()['start'], before)
            self.assertEqual(len(self.peer.requests), calls + 1)
            remaining = source_count()
            self.assertLess(remaining, count)
            count, rendered = remaining, next_view
            # Preparing again without another rejection never repeats its repair.
            self.render('same-checkpoint', capacity=200000)
            self.assertEqual(len(self.peer.requests), calls + 1)
        else:
            self.fail('repair did not terminate over the frozen source backlog')
        self.assertGreater(len(bodies), 2)
        self.assertEqual(len(set(bodies)), len(bodies))

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
        raw = self.meta("task_window:conversation:" + self.fixture.store._native_meta()["owner"])
        return json.loads(raw) if raw else None

    def assert_parent_unchanged(self):
        self.assertEqual(self.health.read_bytes(), self.original_health)
        self.assertEqual(self.completion.read_bytes(), self.original_completion)

    def test_complete_input_middle_survives_preparation_and_restart_when_it_fits(self):
        marker = "CENTRAL_CONSTRAINT: preserve the signed source occurrences"
        body = "before ž" * 9000 + marker + "after ž" * 9000
        self.fixture.store.accept("input:long", body, kind="message")
        self.fixture.store.link_inputs("input:long", ["original-occurrence"])
        reference.task_context.begin(self.fixture.store, "input:long", "operator-event", chat="private")
        for name in ("first-input", "after-restart"):
            rendered = self.render(name, capacity=200000, focus="input:long")
            self.assertNotIn("WITHHELD:", rendered)
            self.assertIn(marker, rendered)
            self.assertNotIn("input excerpt", rendered)
        self.assertEqual(len(self.peer.requests), 0)
        self.assert_parent_unchanged()

    def test_summary_source_contains_the_middle_of_a_long_input(self):
        marker = "MIDDLE_DIRECTIVE: independently unfinished work survives"
        body = "left λ" * 8000 + marker + "right λ" * 8000
        self.fixture.store.accept("input:source", body, kind="message")
        reference.task_context.begin(self.fixture.store, "input:source", "operator-event", chat="private")
        output = self.directory / "complete-summary-source.txt"
        self.run_guest('!(import! &self ' + json.dumps(str(boundary.ROOT / "src/context_compaction.metta")) + ')\n'
            '!(let $view (task:conversation-view "input:source" "continuation-tick") '
            '(fs:update-text ' + json.dumps(str(output)) + ' "replace" '
            '(context:summary-prompt "" (task:items $view "transcript") ()) "" ""))\n')
        self.assertIn(marker, output.read_text())
        self.assert_parent_unchanged()

    def test_request_index_overhead_triggers_a_handoff_before_rendering(self):
        self.populate(20)
        for n in range(24):
            self.fixture.store.accept("request-%d" % n, "Some constraint %d" % n,
                                      kind="message")
        original = reference.task_context.conversation_projection(self.fixture.store, "guide")
        self.peer.reply["choices"][0]["message"]["content"] = "HANDOFF: original source and unfinished task retained."
        out = self.directory / "index-boundary.txt"
        # A window just above the old planner's threshold: the separate
        # request index still needs framing bytes. The old driver skipped
        # maintenance, then rendering withheld forever on unchanged inputs.
        forms = '!(import! &self ' + json.dumps(str(boundary.ROOT / "src/context_compaction.metta")) + ')\n'
        forms += '''!(let* (($base ''' + json.dumps(BASE_PROMPT) + ''')
          ($view (task:conversation-view "guide" "continuation-tick"))
          ($root (task:string $view "root"))
          ($frame (task:working-frame $view 100000 2.5))
          ($summary (task:handoff-text $root))
          ($old-space (+ 10 (+ (+ (context:measure $frame 2.5)
            (math:ceil (/ (str:byte-length $summary) 2.5)))
            (math:ceil (/ (task:sum-tokens (task:items $view "transcript") 2.5 0) 0.9)))))
          ($capacity (+ (+ $old-space (math:ceil (/ (str:byte-length (str:concat $base "\\n")) 2.5))) 128))
          ($rendered (context:prepare $base "guide" "continuation-tick" (repr $capacity) "fixture-model" 256 "default")))
          (case $rendered
            (((ContextPreparationFailed $why)
                (fs:update-text ''' + json.dumps(str(out)) + ''' "replace" (str:concat "WITHHELD: " $why) "" ""))
             ($text (fs:update-text ''' + json.dumps(str(out)) + ''' "replace" $text "" "")))))
!(println! COMPACTION_OK)
'''
        self.run_guest(forms)
        rendered = out.read_text()
        self.assertNotIn("WITHHELD:", rendered)
        self.assertIn("HANDOFF: original source", rendered)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertGreater(self.checkpoint()["start"], 0)
        self.assertEqual(reference.task_context.conversation_projection(self.fixture.store, "guide")["requests"],
                         original["requests"])
        for pair in original["transcript"]:
            self.assertEqual(reference.task_context.read(self.fixture.store, pair["id"])["id"], pair["id"])
        self.assert_parent_unchanged()

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
        self.assertTrue(all(state == "completed" for state, in self.column("calls", "state")))
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
                # The previous subtest's failure record must not stand for this batch: a record of another digest is none.
                for key, _ in input_ack.meta_prefix("context_failure:"):
                    self.meta_put(key, json.dumps({"digest": "", "reason": "cleared", "streak": 0, "retry_at": 0}))
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
        [(key, raw)] = input_ack.meta_prefix("context_failure:")
        failure = json.loads(raw)
        self.assertGreaterEqual(failure["retry_at"], began + 120)
        second = self.render("still-waiting", capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", second)
        self.assertEqual(len(self.peer.requests), 1)
        failure["retry_at"] = time.time() - 1
        self.meta_put(key, json.dumps(failure))
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
        import topic_jobs
        topic_jobs.select("topic")
        topic_jobs.note(topic_note)
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
        processed = input_ack.request_get("guide")["processed"]
        first = self.render(capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", first)
        self.assertNotIn("WITHHELD", first)
        self.assertEqual(len(self.peer.requests), 1)
        second = self.render("retry", capacity=10000)
        self.assertIn("UNFOLDED_HISTORY", second)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertEqual(self.checkpoint()["start"], 0)
        self.assertEqual(input_ack.request_get("guide")["processed"], processed)
        # An older fact is not invented by the degraded window: its exact
        # source is still addressable before a task may depend on it.
        original = reference.task_context.conversation_projection(self.fixture.store, "guide")["transcript"][1]
        self.assertIn("FINDING_0", json.loads(reference.task_context.read(self.fixture.store, original["id"])["text"])["result"])
        self.assert_parent_unchanged()

    def test_legacy_backlog_starts_without_provider_work(self):
        # Bulk source fixture: 10,000 distinct retained occurrences, without
        # making 10,000 fsyncs or executing any of the proposed commands.
        reference.task_context.proposal(self.fixture.store, 0, "guide",
            [["read-file", "legacy-" + str(i)] for i in range(10000)], "not run; reconsider")
        self.assertEqual(input_ack.meta_prefix("task_window:"), [])  # a backlog with no saved checkpoint
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
            '!(println! (context:publish ' + json.dumps(root) + ' none 10 "GOOD" "c1" "first" "last" "digest"))\n'
            '!(println! (context:publish ' + json.dumps(root) + ' none 20 "STALE" "c2" "x" "y" "z"))\n')
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
        self.meta_put("task_window:" + root, old_raw)
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
                self.meta_put("task_window:" + root, json.dumps(checkpoint))
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
        self.meta_put("task_window:" + root, "42")
        rendered = self.render(capacity=100000)
        self.assertIn("ORIGINAL_UNSUMMARIZED_FINDING", rendered)
        self.assertIn("Explain the API conditions", rendered)
        self.assertEqual(len(self.peer.requests), 0)
        self.assert_parent_unchanged()


if __name__ == "__main__":
    unittest.main()

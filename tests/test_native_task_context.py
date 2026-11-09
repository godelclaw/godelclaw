"""Compare native projections with the Python projection of the same native ledger."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch
import native_ledger
import test_task_context as reference
import provider_protocol
ROOT = dispatch.ROOT

class NativeTaskContext(unittest.TestCase):
    def test_covered_batches_match_reference_without_shifting_source_slots(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for name, size in (("input:a", 128), ("input:b", 146), ("input:c", 147)):
            fixture.store.accept(name, name + " full body", kind="message")
            fixture.store.link_inputs(name, range(size))
            reference.task_context.begin(fixture.store, name, "operator-event", chat="private")
            reference.task_context.note(fixture.store, name, "Retain progress for " + name)
        # The native ledger takes its owner at the first captured turn; the conversation's root names it.
        fixture.call([["read-file", "source"]], ["a captured turn gives the ledger its owner"], "input:c")
        expected = reference.task_context.conversation_projection(fixture.store, "input:c")
        expected_family = reference.task_context.projection(fixture.store, "input:c")
        output = fixture.path.parent / "covered.json"
        family = fixture.path.parent / "covered-family.json"
        self.guest(fixture.path,
            '!(fs:update-text ' + json.dumps(str(output)) +
            ' "replace" (state:encoded (task:projection "' + expected["root"] + '" "continuation-tick")) "" "")\n' +
            '!(fs:update-text ' + json.dumps(str(family)) +
            ' "replace" (state:encoded (task:projection "input:c" "continuation-tick")) "" "")\n' +
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertEqual(json.loads(output.read_text()), expected)
        self.assertEqual(json.loads(family.read_text()), expected_family)

    def test_model_calibration_accepts_the_live_python_text_carrier(self):
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.path.parent / "context-token-calibration.json").write_text(
            json.dumps({"fixture-model": {"bytes_per_token": 4.0}}))
        output = self.guest(fixture.path, '''!(import! &self ./src/task_context_views)
!(println! (RATIO_LITERAL (context:ratio "fixture-model")))
!(println! (RATIO_FOREIGN (context:ratio (py-call (builtins.str "fixture-model")))))
!(println! TASK_CONTEXT_OK)
''')
        self.assertRegex(output, r"\(RATIO_LITERAL 4(?:\.0)?\)")
        self.assertRegex(output, r"\(RATIO_FOREIGN 4(?:\.0)?\)")

    def guest(self, database, code):
        harness = dispatch.CommandDispatchEngineTest(); binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN to the native build')
        env = harness._cetta_env()
        env.pop('METTACLAW_LLM_LEDGER_PATH', None)
        env.update(METTACLAW_STRATEGY_PATH=str(database), METTACLAW_ENGINE_STATE_PATH=str(database.parent / 'engine'),
                   METTACLAW_INPUT_LEDGER_SOCKET=os.environ[native_ledger.VARIABLE])
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as source:
            source.write('!(import! &self (library lib_import))\n!(import! &self ./src/task_context_cetta)\n'+code)
            source.flush()
            result = subprocess.run([str(binary), '--lang', 'petta', '--import-mode', 'ancestor-walk', source.name],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Assertion failed', result.stderr)
        self.assertIn('TASK_CONTEXT_OK', result.stdout, result.stdout + result.stderr)
        return result.stdout

    def test_projection_retains_commands_results_corrections_and_null_outcomes(self):
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.call([['read-file','source']], ['EARLY λ; not a compilation check'])
        fixture.store.accept('correction', 'Retain the original qualification λ', kind='message')
        reference.task_context.begin(fixture.store, 'correction', 'operator-event', parent='guide')
        fixture.call([['help','read-file']], ['correction retained'], request='correction')
        fixture.call([['read-file','missing'], ['read-file','later']], [None])
        expected = reference.encoded(reference.task_context.projection(fixture.store, 'correction', 'peer-reply'))
        output = fixture.path.parent / 'native.json'
        self.guest(fixture.path, '!(let $view (task:projection "correction" "peer-reply")\n'
                   '   (fs:update-text '+json.dumps(str(output))+' "replace" (state:encoded $view) "" ""))\n'
                   '!(println! TASK_CONTEXT_OK)\n')
        self.assertTrue(output.is_file())
        self.assertEqual(output.read_text(), expected)

    def test_foreign_string_focus_renders_full_context_with_its_identity(self):
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.call([["read-file", "source"]], ["inspected evidence λ"])
        expected = fixture.path.parent / "text-focus.txt"
        actual = fixture.path.parent / "foreign-focus.txt"
        # py-call returns this text through the PeTTa symbol carrier. Exercise
        # the full prompt, where it becomes the payload of JsonString.
        self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n' +
            '(= (fixture:context $focus) (task:full-context '
            '"soul_cache_boundary_tools_cache_boundary_history_cache_boundary_now" '
            '$focus "tool-completion" "32768" 4))\n' +
            '!(fs:update-text ' + json.dumps(str(expected)) +
            ' "replace" (fixture:context "guide") "" "")\n' +
            '!(let $focus (py-call (builtins.str "guide")) (fs:update-text ' +
            json.dumps(str(actual)) + ' "replace" (fixture:context $focus) "" ""))\n' +
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertTrue(actual.is_file())
        actual_prefix, actual_json = actual.read_text().rsplit("TASK_CONTEXT ", 1)
        expected_prefix, expected_json = expected.read_text().rsplit("TASK_CONTEXT ", 1)
        self.assertEqual(actual_prefix, expected_prefix)
        actual_view, expected_view = json.loads(actual_json), json.loads(expected_json)
        for key in ("focus", "root", "window", "archive"):
            self.assertEqual(actual_view[key], expected_view[key])
        self.assertEqual(actual_view["focus"], "guide")

    def test_unicode_topic_names_and_notes_are_kept(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.guest(fixture.path, '''!(println! (topic-select "Café / PLN λ"))
!(println! (topic-note "inspect the original source; correction preserved"))
!(println! (topics))
!(println! TASK_CONTEXT_OK)
''')
        import topic_jobs
        name, notes = 'Café / PLN λ', 'inspect the original source; correction preserved'
        listed = json.loads(topic_jobs.topics())
        self.assertEqual(listed['active'], name)
        self.assertEqual([row for row in listed['topics'] if row['name'] == name], [{'name': name, 'revision': 1}])
        self.assertEqual(topic_jobs.context(), 'TOPIC_NOTES ' + reference.encoded({'name': name, 'notes': notes}))


    def test_compaction_and_fresh_process_recover_exact_unicode_source(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        text = "ž🙂 inspected source " * 120
        fixture.call([["read-file", "large"]], [text])
        fixture.call([["read-file", "later"], ["send", "unexecuted"]], ["newest observation"])
        original = reference.task_context.projection(fixture.store, "guide")
        identity = original["transcript"][0]["id"]
        out = fixture.path.parent / "recovered.json"
        view = fixture.path.parent / "compact.json"
        def q(value): return json.dumps(str(value), ensure_ascii=False)
        self.guest(fixture.path, "!(import! &self ./src/task_context_views)\n" +
            "!(println! (task-note \"Keep the source-inspection qualification\" "+q(json.dumps([identity]))+"))\n"+
            "!(fs:update-text "+q(view)+" \"replace\" (state:encoded (task:compact (task:projection \"guide\" \"peer-reply\") 1800 1)) \"\" \"\")\n"+
            "!(println! TASK_CONTEXT_OK)\n")
        compact = json.loads(view.read_text())
        self.assertLessEqual(len(view.read_bytes()), 1800)
        self.assertTrue(compact['archive'])
        self.assertEqual(compact['transcript'][-1]['disposition'], 'not_run')
        self.assertIn('qualification', compact['notes'][0]['text'])
        self.guest(fixture.path, "!(import! &self ./src/task_context_views)\n" +
            '(= (recover $offset) (let $page (task:read '+q(identity)+' $offset 173) '+
            '(let* (($text (task:string $page "text")) ($next (json-object-get $page "next"))) '+
            '(case $next ((JsonNull ($text)) ((JsonNumber $n) (cons-atom $text (recover (parse $n)))))))))\n'+
            '!(fs:update-text '+q(out)+' "replace" (str:join "" (recover 0)) "" "")\n'+
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertEqual(json.loads(out.read_text()), original['transcript'][0])
        self.assertEqual(json.loads(out.read_text())['result'], text)

    def test_long_history_fit_check_and_encoding_match_the_full_measure(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for i in range(40): fixture.call([["read-file", str(i)]], [("λ result %d " % i) * 3000])
        expected = reference.encoded(reference.task_context.projection(fixture.store, 'guide'))
        out = fixture.path.parent / 'long.json'
        stdout = self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(let* (($view (task:projection "guide" "continuation-tick"))\n'
            '        ($small (task:fits $view 4000 1)) ($small-full (<= (context:measure $view 1) 4000))\n'
            '        ($large (task:fits $view 100000000 1)) ($large-full (<= (context:measure $view 1) 100000000)))\n'
            '   (println! (fit-check $small $small-full $large $large-full)))\n'
            '!(fs:update-text '+json.dumps(str(out))+' "replace" (state:encoded (task:projection "guide" "continuation-tick")) "" "")\n'
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertIn('(fit-check false false true true)', stdout)
        self.assertEqual(out.read_text(), expected)

    def test_emitted_archive_commands_recover_distinct_occurrences_and_exact_budget(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        text = "same evidence λ🙂 " * 300
        for name in ('first', 'second'):
            fixture.call([["read-file", name]], [text])
        fixture.call([["read-file", "correction"]], ["corrected interpretation; originals retained"])
        original = reference.task_context.projection(fixture.store, 'guide')
        view_file = fixture.path.parent / 'emitted-view.json'
        history_file = fixture.path.parent / 'emitted-history.json'
        q = lambda value: json.dumps(str(value), ensure_ascii=False)
        code = '!(import! &self ./src/task_context_views)\n' + f'''
!(let* (($view (task:projection "guide" "continuation-tick"))
         ($encoded (state:encoded $view))
         ($cost (context:measure $view 2.5)))
   (progn
     (println! (BOUNDARY (task:fits $view $cost 2.5) (task:fits $view (- $cost 1) 2.5)))
     (println! (COST $cost (request:bytes $encoded)))
     (fs:update-text {q(view_file)} "replace" (state:encoded (task:compact $view 800 2.5)) "" "")))
!(println! TASK_CONTEXT_OK)
'''
        out = self.guest(fixture.path, code)
        self.assertIn('(BOUNDARY true false)', out)
        source_bytes = len(reference.encoded(original).encode())
        self.assertIn(f'(COST {math.ceil(source_bytes / 2.5)} {source_bytes})', out)
        self.assertLessEqual(math.ceil(len(view_file.read_bytes()) / 2.5), 800)
        compact = json.loads(view_file.read_text())
        descriptor = compact['archive']['retrieve']
        self.assertEqual(descriptor[0], 'task-history')
        command = '(' + descriptor[0] + ' ' + ' '.join(q(v) for v in descriptor[1:]) + ' "0")'
        self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n' +
                   f'!(fs:update-text {q(history_file)} "replace" {command} "" "")\n!(println! TASK_CONTEXT_OK)\n')
        entries = json.loads(history_file.read_text())['entries']
        self.assertEqual([e['id'] for e in entries], [p['id'] for p in original['transcript']])
        self.assertNotEqual(entries[0]['id'], entries[1]['id'])
        for index, entry in enumerate(entries):
            descriptor = entry['retrieve']
            self.assertEqual(descriptor[0], 'task-read')
            output = fixture.path.parent / f'emitted-read-{index}.json'
            command = '(' + descriptor[0] + ' ' + ' '.join(q(v) for v in descriptor[1:]) + ' "65536")'
            self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n' +
                       f'!(fs:update-text {q(output)} "replace" {command} "" "")\n!(println! TASK_CONTEXT_OK)\n')
            page = json.loads(output.read_text())
            self.assertIsNone(page['next'])
            self.assertEqual(json.loads(page['text']), original['transcript'][index])
        self.assertEqual(reference.task_context.projection(fixture.store, 'guide'), original)

    def test_idle_view_collapses_only_identical_confirmed_rest_receipts(self):
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = "(RESTING budget-banked sleep-interval-seconds 60)"
        pairs = [
            dict(id="a", request="guide", command=["rest", 60],
                 disposition="returned", result=result),
            dict(id="b", request="guide", command=["rest", 60],
                 disposition="returned", result=result),
            dict(id="c", request="other", command=["rest", 60],
                 disposition="returned", result=result),
            dict(id="d", request="other", command=["rest", 60],
                 disposition="not_run", result="not run"),
        ]
        output = fixture.path.parent / "idle-view.json"
        atoms = " ".join("(json-parse %s)" % json.dumps(
            json.dumps(pair, separators=(",", ":"))) for pair in pairs)
        self.guest(fixture.path,
            "!(import! &self ./src/task_context_views)\n" +
            "!(fs:update-text " + json.dumps(str(output)) +
            " \"replace\" (state:encoded (JsonArray (task:idle-pairs (" +
            atoms + ")))) \"\" \"\")\n" +
            "!(println! TASK_CONTEXT_OK)\n")
        rendered = json.loads(output.read_text())
        self.assertEqual(len(rendered), 3)
        self.assertEqual(rendered[0]["count"], 2)
        self.assertEqual(rendered[0]["id"], "a")
        self.assertEqual(rendered[0]["ids"], ["a", "b"])
        self.assertEqual(rendered[1]["request"], "other")
        self.assertEqual(rendered[2]["disposition"], "not_run")

    def full_view(self, fixture, capacity, out, focus="guide", facts="STABLE"):
        prompt = "P_cache_boundary_" + facts + "_cache_boundary_CONV_cache_boundary_TURN"
        stdout = self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(fs:update-text '+json.dumps(str(out))+' "replace" (task:append-context '+json.dumps(prompt)+
            ' '+json.dumps(focus)+' "continuation-tick" '+json.dumps(str(capacity))+' "model") "" "")\n!(println! TASK_CONTEXT_OK)\n')
        self.assertTrue(out.is_file(), stdout)
        text = out.read_text()
        return text, json.loads(text[text.rindex("TASK_CONTEXT ") + len("TASK_CONTEXT "):])

    def test_new_frame_keeps_prior_evidence_in_the_working_window(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for i in range(9): fixture.call([["read-file", str(i)]], ["A_FINDING_%d" % i])
        before, first = self.full_view(fixture, 400000, fixture.path.parent / "before.txt")
        fixture.store.accept("group-post", "B_UNRELATED_INPUT", kind="notification")
        reference.task_context.begin(fixture.store, "group-post", "channel-event", previous="guide", chat="group")
        fixture.call([["help", "work"]], ["B_RESULT"], "group-post")
        after, second = self.full_view(fixture, 400000, fixture.path.parent / "after.txt", "group-post")
        self.assertIn("A_FINDING_0", after)
        self.assertIn("A_FINDING_8", after)
        self.assertIn("B_RESULT", after)
        self.assertIn("B_UNRELATED_INPUT", after)
        self.assertEqual(second["root"], first["root"])
        self.assertEqual(second["task_root"], "group-post")
        self.assertEqual(second["focus"], "group-post")
        self.assertEqual(second["window"]["transcript_steps"], 12)
        self.assertEqual(after.split("_cache_boundary_")[1], before.split("_cache_boundary_")[1])
        self.assertEqual(reference.task_context.history(fixture.store, "guide")["total"], 9)
        self.assertEqual(reference.task_context.history(fixture.store, "group-post")["total"], 1)

    def test_working_metadata_does_not_grow_with_closed_requests(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for name, status in (("wait", "waiting-user"), ("later", "deferred"), ("handed-over", "processed")):
            fixture.store.accept(name, "OPEN_" + name, kind="message")
            if status == "processed":
                self.assertTrue(fixture.store.processed(name))
            else:
                self.assertTrue(fixture.store.disposition(name, "waiting-user" if status == "waiting-user" else "defer", "set aside"))
        def working(name, offset):
            out = fixture.path.parent / (name + ".json")
            self.guest(fixture.path, '!(fs:update-text ' + json.dumps(str(out)) +
                ' "replace" (state:encoded (task:conversation-projection "guide" "tick" ' + str(offset) + ' 40000)) "" "")\n!(println! TASK_CONTEXT_OK)\n')
            return json.loads(out.read_text())
        before = working("before", 4)
        for i in range(2000):
            fixture.store.accept("closed-" + str(i), "CLOSED_BODY" * 100, kind="message")
            self.assertTrue(fixture.store.processed("closed-" + str(i)))
            self.assertTrue(fixture.store.disposition("closed-" + str(i), "addressed", "answered"))
        after = working("after", 2004)
        self.assertEqual(before["requests"], after["requests"])
        self.assertEqual({r["id"] for r in after["requests"]}, {"guide", "wait", "later", "handed-over"})
        self.assertNotIn("CLOSED_BODY", json.dumps(after["requests"]))
        self.assertTrue(all("content" not in r and "retrieve" not in r for r in after["requests"]))

    def test_processed_operator_notices_can_be_addressed_and_leave_open_work(self):
        from unittest import mock
        import input_ack
        import strategy_host

        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        finished = (("input:finished-message", "message"),
                    ("input:finished-notification", "notification"))
        for name, kind in finished + (("input:later", "message"),
                                     ("input:follow-up", "message"),
                                     ("input:deferred", "message"),
                                     ("input:direct", "message")):
            fixture.store.accept(name, name, kind=kind)
        for name, _ in finished:
            self.assertTrue(fixture.store.processed(name))
        self.assertTrue(fixture.store.processed("input:follow-up"))
        self.assertTrue(fixture.store.disposition("input:deferred", "defer", "genuine later work"))
        # Answering directly remains legal without settling the delivery first.
        self.assertTrue(input_ack.request_answered("input:direct"))
        self.assertEqual(fixture.store.request_record("input:direct")["processed"], 0)

        def open_requests(name):
            output = fixture.path.parent / (name + ".json")
            self.guest(fixture.path, '!(fs:update-text ' + json.dumps(str(output)) +
                ' "replace" (state:encoded (task:conversation-projection "guide" "continuation-tick" 0 40000)) "" "")\n'
                '!(println! TASK_CONTEXT_OK)\n')
            return json.loads(output.read_text())["requests"]

        before = open_requests("notice-before")
        self.assertTrue({name for name, _ in finished}.issubset({row["id"] for row in before}))
        with mock.patch.object(strategy_host, "enabled", return_value=True), \
                mock.patch.object(strategy_host, "ledger", return_value=fixture.store):
            receipts = [strategy_host.request_disposition(name, "addressed", "reviewed")
                        for name, _ in finished]
        self.assertEqual(receipts, ["REQUEST_DISPOSITION_RECORDED"] * len(finished))
        self.assertEqual([fixture.store.request_record(name)["status"] for name, _ in finished],
                         ["addressed", "addressed"])

        after = open_requests("notice-after")
        rows = {row["id"]: row for row in after}
        self.assertFalse({name for name, _ in finished} & rows.keys())
        self.assertNotIn("input:direct", rows)
        self.assertEqual(rows["input:later"]["status"], "active")
        self.assertEqual(rows["input:follow-up"]["status"], "processed")
        self.assertEqual(rows["input:deferred"]["status"], "deferred")

    def test_native_conversation_projection_matches_python_and_preserves_occurrences(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.call([["read-file", "a"]], ["same observation"])
        fixture.store.accept("other", "Other input")
        reference.task_context.begin(fixture.store, "other", "channel-event", previous="guide")
        fixture.call([["read-file", "b"]], ["same observation"], "other")
        expected = reference.encoded(reference.task_context.conversation_projection(fixture.store, "other", "channel-event"))
        out = fixture.path.parent / "conversation.json"
        self.guest(fixture.path, '!(fs:update-text '+json.dumps(str(out))+
                   ' "replace" (state:encoded (task:conversation-projection "other" "channel-event" 0 0)) "" "")\n!(println! TASK_CONTEXT_OK)\n')
        self.assertEqual(out.read_text(), expected)
        self.assertEqual(len(json.loads(out.read_text())["transcript"]), 4)

    def test_whole_window_view_is_append_only_ahead_of_the_turn(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.call([["read-file", "big"]], ["H" * 30000 + "MIDDLE" + "T" * 30000])
        fixture.call([["read-file", "marker"]], ["a _cache_boundary_ and a _cache_chunk_ in content"])
        for i in range(20): fixture.call([["read-file", str(i)]], [("step %d λ " % i) * 40])
        first, view = self.full_view(fixture, 400000, fixture.path.parent / "first.txt")
        self.assertEqual(first.count("_cache_boundary_"), 3)
        transcript, facts, turn = first.split("_cache_boundary_")[1:]
        self.assertTrue(facts.startswith("TASK_REQUESTS "))
        self.assertTrue(facts.split("\n_cache_chunk_\n", 1)[1].startswith("STABLE"))
        self.assertIn("TASK_TRANSCRIPT steps 0-7", transcript)
        self.assertIn("CONV", turn); self.assertNotIn("CONV", transcript)
        chunks = [c.strip() for c in transcript.split("\n_cache_chunk_\n")]
        self.assertEqual(len(chunks), 2)
        self.assertEqual(view["window"]["transcript_steps"], 23)
        self.assertEqual(view["transcript"], [])
        recent = first[first.index("TASK_RECENT newest steps"):first.rindex("TASK_CONTEXT ")]
        self.assertEqual(recent.count("\n## step "), 7)
        self.assertIn("\n## step 17 · [\"read-file\",\"14\"] · returned · id ", recent)
        self.assertIn("step 14 λ step 14 λ", recent)
        self.assertNotIn("MIDDLE", first)
        self.assertIn("tokens truncated; task-read shows the whole result", first)
        self.assertIn("_cache-boundary_", transcript)
        self.assertIn("_cache-chunk_", transcript)
        for i in range(20, 31): fixture.call([["read-file", str(i)]], [("later %d " % i) * 40])
        second, later = self.full_view(fixture, 400000, fixture.path.parent / "second.txt")
        later_chunks = [c.strip() for c in second.split("_cache_boundary_")[1].split("\n_cache_chunk_\n")]
        self.assertEqual(later_chunks[:2], chunks)
        self.assertEqual(len(later_chunks), 4)
        self.assertEqual(later["window"]["first_step_here"], 32)

    def test_project_revision_preserves_the_completed_history_cache_prefix(self):
        import provider_protocol
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for i in range(20):
            fixture.call([["read-file", str(i)]], ["inspected source " + str(i)])
        first, _ = self.full_view(fixture, 400000, fixture.path.parent / "before-revision.txt")
        second, _ = self.full_view(fixture, 400000, fixture.path.parent / "after-revision.txt", facts="NEW_REVISION")
        stable, _ = provider_protocol.split_prompt(first)
        history_end = provider_protocol.cache_boundaries(stable)[1]
        self.assertGreater(history_end, 0)
        bodies = [provider_protocol.command_body("anthropic", "fixture", 100, "high", prompt)[1]
                  for prompt in (first, second)]
        a, b = map(provider_protocol.anthropic_cache_observation, bodies)
        self.assertEqual(a["blocks"][:history_end + 1], b["blocks"][:history_end + 1])
        self.assertNotEqual(a["blocks"][-1]["prefix_digest"], b["blocks"][-1]["prefix_digest"])
        self.assertIn("cache_control", bodies[0]["system"][history_end])
        for text in (first, second):
            self.assertIn("inspected source 0", text)
            self.assertIn("inspected source 19", text)
            self.assertIn("CONV", text)

    def test_pending_index_precedes_volatile_state_without_losing_request_values(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for i in range(25):
            fixture.store.accept('pending-' + str(i), 'λ _cache_boundary_ _cache_chunk_ _cache-boundary_ _cache-chunk_ \\_cache_chunk_ ' + str(i), kind='message')
        for i in range(25):
            self.assertTrue(fixture.store.processed('pending-' + str(i)))
        import input_ack
        expected = [dict(id=row['id'], status=row['status'], kind=row['kind'], note=row['note'], excerpt=row['content'][:160])
                    for row in input_ack.request_rows()]
        before, view = self.full_view(fixture, 400000, fixture.path.parent / 'indexed-before.txt')
        after, _ = self.full_view(fixture, 400000, fixture.path.parent / 'indexed-after.txt', facts='CHANGED_STATE')
        def blocks(prompt):
            return provider_protocol.command_body('responses', 'fixture', 100, 'high', prompt)[1]['input']
        first, second = blocks(before), blocks(after)
        indexed = [item['content'] for item in first if item['content'].startswith('TASK_REQUESTS')]
        decoded = [row for part in indexed for row in json.loads(part[part.index('\n')+1:])]
        self.assertEqual(decoded, expected)
        self.assertEqual(view['requests'], {'source': 'TASK_REQUESTS', 'count': len(expected)})
        self.assertEqual(before.count('_cache_boundary_'), 3)
        last_index = max(i for i, item in enumerate(first) if item['content'].startswith('TASK_REQUESTS'))
        self.assertEqual(first[:last_index+1], second[:last_index+1])
        self.assertIn('STABLE', first[last_index+1]['content'])
        self.assertIn('CHANGED_STATE', second[last_index+1]['content'])
        fixture.store.accept('new-pending', 'A new request must stay visible', kind='message')
        grown, _ = self.full_view(fixture, 400000, fixture.path.parent / 'indexed-grown.txt')
        newer = [item['content'] for item in blocks(grown) if item['content'].startswith('TASK_REQUESTS')]
        self.assertEqual(indexed[:-1], newer[:-1])
        self.assertIn('new-pending', newer[-1])

    def test_index_rearrangement_keeps_inline_metadata_when_layout_is_unstructured(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        target = fixture.path.parent / 'plain-view.txt'
        self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n' +
            '!(fs:update-text ' + json.dumps(str(target)) +
            ' "replace" (task:full-context "PLAIN_BASE" "guide" "tick" "400000" 4) "" "")\n!(println! TASK_CONTEXT_OK)\n')
        rendered = target.read_text()
        view = json.loads(rendered[rendered.rindex('TASK_CONTEXT ')+13:])
        self.assertIsInstance(view['requests'], list)
        self.assertTrue(any(row['id']=='guide' for row in view['requests']))

    def test_whole_window_points_to_an_identical_earlier_result(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        same = "the same long file body " * 100
        fixture.call([["read-file", "a"]], [same])
        fixture.call([["read-file", "b"]], ["different"])
        fixture.call([["read-file", "a"]], [same])
        text, _ = self.full_view(fixture, 400000, fixture.path.parent / "dedupe.txt")
        recent = text[text.index("TASK_RECENT newest steps"):text.rindex("TASK_CONTEXT ")]
        self.assertEqual(recent.count(same), 1)
        self.assertIn("(same result as step 1)", recent)

    def test_overflow_cannot_advance_without_a_durable_handoff(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        for i in range(60): fixture.call([["read-file", str(i)]], [("step %d " % i) * 200])
        # Pure rendering is not permission to discard history. The live driver
        # must first generate and atomically publish a successful handoff.
        output = self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(println! (task:append-context "prompt" "guide" "continuation-tick" "20000" "model"))\n'
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertIn('ContextPreparationFailed "context-compaction-required"', output)
        import input_ack
        self.assertEqual(input_ack.meta_prefix('task_window:'), [])
        self.assertEqual(reference.task_context.history(fixture.store, "guide")["total"], 60)

    def test_legacy_history_keeps_numeric_order_and_original_identities(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        # Older journals lack some proposals: those turns were decided without recording one.
        for i in range(13): fixture.call([["read-file", str(i)]], ["result " + str(i)], propose=i not in (0, 2, 10))
        expected = reference.encoded(reference.task_context.projection(fixture.store, 'guide'))
        out = fixture.path.parent / 'legacy.json'
        self.guest(fixture.path, '!(fs:update-text '+json.dumps(str(out))+
                   ' "replace" (state:encoded (task:projection "guide" "continuation-tick")) "" "")\n!(println! TASK_CONTEXT_OK)\n')
        self.assertEqual(out.read_text(), expected)


    def test_full_driver_uses_native_context_without_the_python_projection(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.call([["read-file", "source"]], ["ORIGINAL_FINDING_RETAINED"])
        harness = dispatch.CommandDispatchEngineTest(); env = harness._cetta_env()
        env.pop("METTACLAW_LLM_LEDGER_PATH", None)
        env.update(METTACLAW_OBSERVED_STRATEGY="1", METTACLAW_STRATEGY_PATH=str(fixture.path),
                   METTACLAW_INPUT_LEDGER_SOCKET=fixture.socket,
                   METTACLAW_ENGINE_STATE_PATH=str(fixture.path.parent / "engine"),
                   METTACLAW_CONTEXT_WINDOW_TOKENS="100000", METTACLAW_HISTORY_PATH=str(fixture.path.parent / "history"),
                   METTACLAW_MODEL_COMMENTARY="1",
                   METTACLAW_TELEGRAM_LOG_PATH=str(fixture.path.parent / "updates.jsonl"))
        module = fixture.path.parent / 'projection_probe.py'
        module.write_text('import task_context, model_commentary\ndef disable():\n'
                          '    def fail(*args, **kwargs): raise AssertionError("Python projection used")\n'
                          '    task_context.projection = fail\n    task_context.compact = fail\n'
                          '    original = model_commentary.prompt\n'
                          '    def format_base(text):\n'
                          '        assert "ORIGINAL_FINDING_RETAINED" not in text, "protected history crossed the Python formatter"\n'
                          '        return original(text)\n'
                          '    model_commentary.prompt = format_base\n    return 1\n')
        with tempfile.NamedTemporaryFile('w', dir=ROOT, suffix='.metta') as source:
            source.write('!(import! &self '+json.dumps(str(module))+')\n'
                         '!(py-call (projection_probe.disable))\n'
                         '!(py-call (strategy_host.focus_request "guide"))\n'
                         '!(println! (addition-protected-request "native prompt" 32768))\n'
                         '!(println! (task-history "guide" "0" "0"))\n')
            source.flush()
            result = subprocess.run([str(harness._cetta_binary()), '--lang', 'petta', '--import-mode', 'ancestor-walk',
                                     str(ROOT/'cetta_bootstrap.metta'), source.name], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ORIGINAL_FINDING_RETAINED', result.stdout)
        self.assertIn('TASK_CONTEXT', result.stdout)
        self.assertIn('advertised `metta` tool exactly once', result.stdout)
        self.assertNotIn('Python projection used', result.stdout + result.stderr)
        self.assertNotIn('protected history crossed', result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()

"""Compare native projections with the existing checkpoint representation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_command_dispatch_engine as dispatch
import test_task_context as reference
ROOT = dispatch.ROOT

class NativeTaskContext(unittest.TestCase):
    def guest(self, database, code):
        harness = dispatch.CommandDispatchEngineTest(); binary = harness._cetta_binary()
        if not binary.is_file(): self.skipTest('set CETTA_BIN to shared-SQLite build')
        env = harness._cetta_env()
        env.update(METTACLAW_STRATEGY_PATH=str(database), METTACLAW_ENGINE_STATE_PATH=str(database.parent / 'engine'))
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
        self.addCleanup(fixture.tmp.cleanup)
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
        self.addCleanup(fixture.tmp.cleanup)
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

    def test_unicode_topic_names_and_notes_use_existing_schema(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        self.guest(fixture.path, '''!(println! (topic-select "Café / PLN λ"))
!(println! (topic-note "inspect the original source; correction preserved"))
!(println! (topics))
!(println! TASK_CONTEXT_OK)
''')
        with fixture.store.transaction() as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='active_topic'").fetchone()[0]), 'Café / PLN λ')
            self.assertEqual(tuple(db.execute("SELECT name,revision,notes FROM topic_contexts WHERE name=?", ('Café / PLN λ',)).fetchone()),
                             ('Café / PLN λ', 1, 'inspect the original source; correction preserved'))


    def test_compaction_and_fresh_process_recover_exact_unicode_source(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
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
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
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

    def test_idle_view_collapses_only_identical_confirmed_rest_receipts(self):
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
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

    def full_view(self, fixture, capacity, out, focus="guide"):
        prompt = "P_cache_boundary_STABLE_cache_boundary_CONV_cache_boundary_TURN"
        stdout = self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(fs:update-text '+json.dumps(str(out))+' "replace" (task:append-context '+json.dumps(prompt)+
            ' '+json.dumps(focus)+' "continuation-tick" '+json.dumps(str(capacity))+' "model") "" "")\n!(println! TASK_CONTEXT_OK)\n')
        self.assertTrue(out.is_file(), stdout)
        text = out.read_text()
        return text, json.loads(text[text.rindex("TASK_CONTEXT ") + len("TASK_CONTEXT "):])

    def test_new_frame_keeps_prior_evidence_in_the_working_window(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
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
        self.assertEqual(after.split("_cache_boundary_")[2], before.split("_cache_boundary_")[2])
        self.assertEqual(reference.task_context.history(fixture.store, "guide")["total"], 9)
        self.assertEqual(reference.task_context.history(fixture.store, "group-post")["total"], 1)

    def test_native_conversation_projection_matches_python_and_preserves_occurrences(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
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
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        fixture.call([["read-file", "big"]], ["H" * 30000 + "MIDDLE" + "T" * 30000])
        fixture.call([["read-file", "marker"]], ["a _cache_boundary_ and a _cache_chunk_ in content"])
        for i in range(20): fixture.call([["read-file", str(i)]], [("step %d λ " % i) * 40])
        first, view = self.full_view(fixture, 400000, fixture.path.parent / "first.txt")
        self.assertEqual(first.count("_cache_boundary_"), 3)
        head, transcript, turn = first.split("_cache_boundary_")[1:]
        self.assertTrue(head.startswith("STABLE"))
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
        later_chunks = [c.strip() for c in second.split("_cache_boundary_")[2].split("\n_cache_chunk_\n")]
        self.assertEqual(later_chunks[:2], chunks)
        self.assertEqual(len(later_chunks), 4)
        self.assertEqual(later["window"]["first_step_here"], 32)

    def test_whole_window_points_to_an_identical_earlier_result(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        same = "the same long file body " * 100
        fixture.call([["read-file", "a"]], [same])
        fixture.call([["read-file", "b"]], ["different"])
        fixture.call([["read-file", "a"]], [same])
        text, _ = self.full_view(fixture, 400000, fixture.path.parent / "dedupe.txt")
        recent = text[text.index("TASK_RECENT newest steps"):text.rindex("TASK_CONTEXT ")]
        self.assertEqual(recent.count(same), 1)
        self.assertIn("(same result as step 1)", recent)

    def test_overflow_cannot_advance_without_a_durable_handoff(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        for i in range(60): fixture.call([["read-file", str(i)]], [("step %d " % i) * 200])
        # Pure rendering is not permission to discard history. The live driver
        # must first generate and atomically publish a successful handoff.
        output = self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(println! (task:append-context "prompt" "guide" "continuation-tick" "20000" "model"))\n'
            '!(println! TASK_CONTEXT_OK)\n')
        self.assertIn('ContextPreparationFailed "context-compaction-required"', output)
        with fixture.store.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM meta WHERE key LIKE 'task_window:%'").fetchone()[0], 0)
        self.assertEqual(reference.task_context.history(fixture.store, "guide")["total"], 60)

    def test_legacy_history_keeps_numeric_order_and_original_identities(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        for i in range(13): fixture.call([["read-file", str(i)]], ["result " + str(i)])
        with fixture.store.transaction() as db:
            db.execute("DELETE FROM task_proposals WHERE serial IN (0,2,10)")
        expected = reference.encoded(reference.task_context.projection(fixture.store, 'guide'))
        out = fixture.path.parent / 'legacy.json'
        self.guest(fixture.path, '!(fs:update-text '+json.dumps(str(out))+
                   ' "replace" (state:encoded (task:projection "guide" "continuation-tick")) "" "")\n!(println! TASK_CONTEXT_OK)\n')
        self.assertEqual(out.read_text(), expected)


    def test_full_driver_uses_native_context_without_the_python_projection(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        fixture.call([["read-file", "source"]], ["ORIGINAL_FINDING_RETAINED"])
        harness = dispatch.CommandDispatchEngineTest(); env = harness._cetta_env()
        env.update(METTACLAW_OBSERVED_STRATEGY="1", METTACLAW_STRATEGY_PATH=str(fixture.path),
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
        self.assertIn('return one JSON object', result.stdout)
        self.assertNotIn('Python projection used', result.stdout + result.stderr)
        self.assertNotIn('protected history crossed', result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()

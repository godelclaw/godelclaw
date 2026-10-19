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

    def full_view(self, fixture, capacity, out):
        prompt = "P_cache_boundary_STABLE_cache_boundary_CONV_cache_boundary_TURN"
        self.guest(fixture.path, '!(import! &self ./src/task_context_views)\n'
            '!(fs:update-text '+json.dumps(str(out))+' "replace" (task:append-context '+json.dumps(prompt)+
            ' "guide" "continuation-tick" '+json.dumps(str(capacity))+' "model") "" "")\n!(println! TASK_CONTEXT_OK)\n')
        text = out.read_text()
        return text, json.loads(text[text.rindex("TASK_CONTEXT ") + len("TASK_CONTEXT "):])

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
        self.assertEqual(view["window"]["transcript_steps"], 22)
        self.assertEqual(len(view["transcript"]), 6)
        self.assertNotIn("MIDDLE", first)
        self.assertIn("tokens truncated; task-read shows the whole result", first)
        self.assertIn("_cache\\u005fboundary_", transcript)
        self.assertIn("_cache\\u005fchunk_", transcript)
        for i in range(20, 31): fixture.call([["read-file", str(i)]], [("later %d " % i) * 40])
        second, later = self.full_view(fixture, 400000, fixture.path.parent / "second.txt")
        later_chunks = [c.strip() for c in second.split("_cache_boundary_")[2].split("\n_cache_chunk_\n")]
        self.assertEqual(later_chunks[:2], chunks)
        self.assertEqual(len(later_chunks), 4)
        self.assertEqual(later["window"]["first_step_here"], 32)

    def test_whole_window_compacts_past_ninety_percent_to_the_newest_fifth(self):
        fixture = reference.TaskContextTests(); fixture.setUp(); self.addCleanup(fixture.tmp.cleanup)
        for i in range(60): fixture.call([["read-file", str(i)]], [("step %d " % i) * 200])
        _, view = self.full_view(fixture, 20000, fixture.path.parent / "view.txt")
        with fixture.store.transaction() as db:
            start = int(json.loads(db.execute("SELECT value FROM meta WHERE key='task_window:guide'").fetchone()[0]))
        self.assertGreater(start, 0)
        self.assertEqual(view["archive"]["count"], str(start) if isinstance(view["archive"]["count"], str) else start)
        self.assertLessEqual(view["window"]["transcript_steps"], 60 - start)

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
                   METTACLAW_TELEGRAM_LOG_PATH=str(fixture.path.parent / "updates.jsonl"))
        module = fixture.path.parent / 'projection_probe.py'
        module.write_text('import task_context\ndef disable():\n'
                          '    def fail(*args, **kwargs): raise AssertionError("Python projection used")\n'
                          '    task_context.projection = fail\n    task_context.compact = fail\n    return 1\n')
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
        self.assertNotIn('Python projection used', result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()

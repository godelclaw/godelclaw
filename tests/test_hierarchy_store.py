#!/usr/bin/env python3
"""Native hierarchy conformance. Python starts fixtures; MeTTa owns policy."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARGS = argparse.Namespace(engine=os.environ.get('CETTA_BIN'), server=os.environ.get('CETTA_SPACE_SERVICE'))



class HierarchyTests(unittest.TestCase):
    def setUp(self):
        if not ARGS.engine or not ARGS.server:
            self.skipTest('set CETTA_BIN and CETTA_SPACE_SERVICE for native qualification')
        self.temp = tempfile.TemporaryDirectory(prefix='cetta-hierarchy-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.socket = self.home / 'store.sock'
        self.server = None
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        self.server = subprocess.Popen(
            [ARGS.server, '--socket', str(self.socket), '--data', str(self.home / 'data')],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        until = time.monotonic() + 10
        while not self.socket.exists():
            if self.server.poll() is not None:
                self.fail(self.server.stderr.read().decode())
            if time.monotonic() > until:
                self.fail('native store did not start')
            time.sleep(.01)

    def stop(self):
        if self.server is not None:
            self.server.terminate()
            self.server.communicate(timeout=10)
            self.server = None

    def run_metta(self, body, dialect='petta', checks=None):
        source = self.home / ('probe-' + dialect + '-' + str(time.monotonic_ns()) + '.metta')
        module = json.dumps(str(ROOT / 'src/hierarchy_store.metta'))
        source.write_text('!(import! &self ' + module + ')\n'
                          '(= (sock) ' + json.dumps(str(self.socket)) + ')\n'
                          '(= (store) "hierarchy")\n'
                          '(= (expect $label $actual $wanted) '
                          '(if (== $actual $wanted) (println! (OK $label)) '
                          '(println! (FAIL $label $actual $wanted))))\n' + body)
        env = dict(os.environ)
        # The core engine must not fall through to a Python/SWI module search.
        env.pop('PYTHONPATH', None)
        run = subprocess.run([ARGS.engine, '--lang', dialect, str(source)], cwd=ROOT,
                             env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertNotIn('(FAIL ', run.stdout, run.stdout)
        self.assertNotIn('(Error ', run.stdout, run.stdout)
        if checks is not None:
            self.assertEqual(run.stdout.count('(OK '), checks, run.stdout)
        return run.stdout

    def seed(self):
        return '''
!(hm:put (sock) (store) "e" episode "Evidence" "original needle Ω" () ((External "file-v1")))
!(hm:put (sock) (store) "t" task "Task" "Original objective" ("e") ())
!(hm:put (sock) (store) "p" project "Project" "Overview" ("t") ())
'''

    def test_expand_and_raw_search_survive_summary_omission(self):
        output = self.run_metta(self.seed() + '''
!(hm:summarize (sock) (store) "t" "summary omits the detail")
!(println! (EXPANDED (hm:expand (sock) (store) "p" 2)))
!(expect raw-search (hm:search (sock) (store) "needle" "p")
   ((HHit "e" 1 episode "Evidence" source (HOverview absent "" ()))))
''', checks=1)
        for identity in ('p', 't', 'e'):
            self.assertIn('(HMemory "' + identity + '"', output)
        self.assertIn('"original needle Ω"', output)
        self.assertIn('HOverview fresh "summary omits the detail"', output)

    def test_correction_retains_original_and_invalidates_ancestors(self):
        self.run_metta(self.seed() + '''
!(hm:summarize (sock) (store) "t" "First task interpretation")
!(hm:summarize (sock) (store) "p" "First project interpretation")
!(hm:put (sock) (store) "e" episode "Evidence" "corrected value" () ((External "file-v2")))
!(case (hm:expand (sock) (store) "p" 0)
   (((HMemory $id $r $k $t $body (HOverview stale $text $stamp) $ss $cs) (println! (OK stale)))
    ($other (println! (FAIL stale $other)))))
!(expect original (hm:history (sock) (store) "e" 1)
   (HOriginal "e" 1 episode "Evidence" "original needle Ω" () ((External "file-v1"))))
!(expect corrected (hm:history (sock) (store) "e" 2)
   (HOriginal "e" 2 episode "Evidence" "corrected value" () ((External "file-v2"))))
''', checks=3)

    def test_membership_and_child_summary_changes_are_dependencies(self):
        self.run_metta(self.seed() + '''
!(hm:summarize (sock) (store) "t" "First task summary")
!(hm:summarize (sock) (store) "p" "Project summary")
!(hm:summarize (sock) (store) "t" "Revised task interpretation")
!(case (hm:expand (sock) (store) "p" 0)
   (((HMemory $id $r $k $t $b (HOverview stale $text $refs) $ss $cs) (println! (OK child-summary)))
    ($other (println! (FAIL child-summary $other)))))
!(case (hm:expand (sock) (store) "t" 0)
   (((HMemory $id $r $k $t $b (HOverview fresh $text $refs) $ss $cs) (println! (OK own-summary)))
    ($other (println! (FAIL own-summary $other)))))
!(hm:summarize (sock) (store) "p" "Updated project summary")
!(hm:put (sock) (store) "extra" episode "Extra" "More evidence" () ())
!(hm:link (sock) (store) "t" "extra")
!(case (hm:expand (sock) (store) "p" 0)
   (((HMemory $id $r $k $t $b (HOverview stale $text $refs) $ss $cs) (println! (OK membership)))
    ($other (println! (FAIL membership $other)))))
''', checks=3)

    def test_duplicate_delivery_is_idempotent_and_cycles_are_explicit(self):
        self.run_metta(self.seed() + '''
!(expect duplicate (hm:put (sock) (store) "e" episode "Evidence" "original needle Ω" () ((External "file-v1")))
   (HStored "e" 1 unchanged))
!(expect cycle (hm:link (sock) (store) "e" "p") (HFailure cycle "e"))
!(expect dangling (hm:link (sock) (store) "t" "missing") (HFailure missing-node "missing"))
!(expect preserved (hm:history (sock) (store) "e" 1)
   (HOriginal "e" 1 episode "Evidence" "original needle Ω" () ((External "file-v1"))))
''', checks=4)

    def test_restart_restores_versions_and_hierarchy(self):
        self.run_metta(self.seed() + '!(hm:summarize (sock) (store) "p" "Remember this project")\n')
        self.stop()
        self.start()
        self.run_metta('''
!(expect original (hm:history (sock) (store) "e" 1)
   (HOriginal "e" 1 episode "Evidence" "original needle Ω" () ((External "file-v1"))))
!(case (hm:expand (sock) (store) "p" 2)
   (((HMemory $id $r $k $t $body (HOverview fresh "Remember this project" $refs) $ss $cs) (println! (OK restart)))
    ($other (println! (FAIL restart $other)))))
''', checks=2)

    def test_empty_long_unicode_bodies_and_shared_children(self):
        text = 'α' * 4000 + '\n' + 'x' * 1000
        self.run_metta('''
!(hm:put (sock) (store) "empty" episode "Empty" "" () ())
!(hm:put (sock) (store) "long" episode "Long" TEXT () ())
!(hm:put (sock) (store) "a" task "A" "" ("long") ())
!(hm:put (sock) (store) "b" task "B" "" ("long") ())
!(hm:put (sock) (store) "root" project "Root" "" ("a" "b") ())
!(expect empty (hm:history (sock) (store) "empty" 1) (HOriginal "empty" 1 episode "Empty" "" () ()))
!(expect long (hm:history (sock) (store) "long" 1) (HOriginal "long" 1 episode "Long" TEXT () ()))
!(let (HView $space $nodes) (hm:snapshot (sock) (store))
   (let $stamp (hm:stamp $nodes "root") (expect unique (size-atom $stamp) 7)))
'''.replace('TEXT', json.dumps(text)), checks=3)

    def test_missing_store_is_a_failure_not_empty_recall(self):
        self.run_metta('''
!(case (hm:expand "/nonexistent/native-memory.sock" "test" "missing" 1)
   (((space:unavailable $id $reason) (println! (OK unavailable)))
    ($other (println! (FAIL unavailable $other)))))
''', checks=1)

    def test_unrelated_changes_stay_fresh_and_removal_is_stale(self):
        self.run_metta(self.seed() + '''
!(hm:summarize (sock) (store) "p" "Project interpretation")
!(hm:put (sock) (store) "unrelated" episode "Other" "Elsewhere" () ())
!(case (hm:expand (sock) (store) "p" 0)
   (((HMemory $id $r $k $t $b (HOverview fresh $text $refs) $ss $cs) (println! (OK unrelated)))
    ($other (println! (FAIL unrelated $other)))))
!(hm:put (sock) (store) "t" task "Task" "Original objective" () ())
!(case (hm:expand (sock) (store) "p" 0)
   (((HMemory $id $r $k $t $b (HOverview stale $text $refs) $ss $cs) (println! (OK removed)))
    ($other (println! (FAIL removed $other)))))
!(expect source-retained (hm:history (sock) (store) "e" 1)
   (HOriginal "e" 1 episode "Evidence" "original needle Ω" () ((External "file-v1"))))
''', checks=3)

    def test_atomic_capture_preserves_concurrent_task_membership(self):
        self.run_metta('!(hm:put (sock) (store) "p" project "Project" "" () ())\n')
        def actor(name):
            return self.run_metta(''.join(
                '!(hm:record (sock) (store) "p" "task-' + name + '" "' + name + '-' + str(i) +
                '" "turn" "original ' + name + '" "parallel evidence")\n' for i in range(8)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(actor, ('a', 'b')))
        self.assertTrue(all('captured' in output for output in outputs))
        self.run_metta('''
!(let $hits (hm:search (sock) (store) "parallel evidence" "p")
   (expect all-episodes (size-atom $hits) 16))
!(case (hm:context (sock) (store) "p")
   (((HContext $id $r $k $t $ov $children) (expect both-tasks (size-atom $children) 2))
    ($other (println! (FAIL both-tasks $other)))))
''', checks=2)

    def test_checkpoint_compaction_keeps_recoverable_originals(self):
        self.run_metta(self.seed() + '''
!(hm:put (sock) (store) "e" episode "Evidence" "corrected" () ())
!(let (space $uuid $id $name $backend $kind $rev $count) (hm:open (sock) (store))
   (case (space:call (sock) (request (space:request-id) compact $uuid) 5000)
     (((reply $rid ok $answer) (println! (OK compact)))
      ($other (println! (FAIL compact $other))))))
''', checks=1)
        self.stop()
        self.start()
        self.run_metta('''
!(expect original (hm:history (sock) (store) "e" 1)
   (HOriginal "e" 1 episode "Evidence" "original needle Ω" () ((External "file-v1"))))
!(expect revised (hm:history (sock) (store) "e" 2)
   (HOriginal "e" 2 episode "Evidence" "corrected" () ()))
''', checks=2)

    def test_retirement_excludes_recall_and_survives_capture_and_restart(self):
        self.run_metta(self.seed() + '''
!(hm:summarize (sock) (store) "p" "Project summary")
!(hm:retire (sock) (store) "t" "obsolete interpretation")
!(expect inactive (hm:context (sock) (store) "t") (HInactive "t" "obsolete interpretation"))
!(expect hidden (hm:search (sock) (store) "Original objective" "") ())
!(expect evidence (hm:history (sock) (store) "t" 1)
   (HOriginal "t" 1 task "Task" "Original objective" ("e") ()))
!(case (hm:context (sock) (store) "p")
   (((HContext "p" $r $k $title (HOverview stale $text $refs) ()) (println! (OK parent-stale)))
    ($other (println! (FAIL parent-stale $other)))))
!(hm:record (sock) (store) "p" "t" "later" "later turn" "input" "later evidence")
!(expect remains-inactive (hm:context (sock) (store) "t") (HInactive "t" "obsolete interpretation"))
''', checks=5)
        self.stop(); self.start()
        self.run_metta('''
!(expect restart-inactive (hm:context (sock) (store) "t") (HInactive "t" "obsolete interpretation"))
!(expect restart-hidden (hm:search (sock) (store) "Original objective" "") ())
!(hm:put (sock) (store) "t" task "Task" "chosen revision" ("e") ())
!(case (hm:context (sock) (store) "t")
   (((HContext "t" $r task $title $overview $children) (println! (OK reactivated)))
    ($other (println! (FAIL reactivated $other)))))
''', checks=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', default=ARGS.engine)
    parser.add_argument('--server', default=ARGS.server)
    ARGS, test_args = parser.parse_known_args()
    unittest.main(argv=[__file__] + test_args)

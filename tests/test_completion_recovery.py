"""Local storage faults cannot turn a received answer into another HTTP POST."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_native_llm_boundary as boundary

ROOT, Peer = boundary.ROOT, boundary.Peer


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to the native runtime")
class CompletionRecoveryTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest
    disable_python_accounting = boundary.NativeBoundaryTests.disable_python_accounting

    def fault(self, mode):
        module = self.directory / "fixture_modules/recovery_fault.py"
        module.write_text('''
import os, sqlite3, threading, time
from pathlib import Path
import native_llm_host

def arm(kind):
    original = native_llm_host.decoded_body
    pending = [True]
    def decoded(plan, body):
        result = original(plan, body)
        if pending[0]:
            pending[0] = False
            if kind == "ambiguous":
                Path(os.environ["LLM_TEST_COMMIT_FAULT"]).write_text("armed")
            elif kind == "conflict":
                with sqlite3.connect(os.environ["METTACLAW_LLM_LEDGER_PATH"]) as db:
                    db.execute("UPDATE requests SET status='completed' WHERE id=?", (plan.correlation,))
                    db.execute("UPDATE native_calls SET state='completed',result='different' WHERE id=?", (plan.correlation,))
            else:
                ledger = os.environ["METTACLAW_LLM_LEDGER_PATH"]
                with sqlite3.connect(ledger) as db:
                    db.execute("CREATE TRIGGER fixture_abort BEFORE UPDATE OF state ON native_calls "
                               "WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'fixture completion fault'); END")
                def release():
                    with sqlite3.connect(ledger) as db:
                        db.execute("DROP TRIGGER fixture_abort")
                if kind == "many":
                    def paced(plan, attempt):
                        if attempt >= 255: release()
                        return 1
                    native_llm_host.completion_wait = paced
                else:
                    def later():
                        time.sleep(.35)
                        release()
                    threading.Thread(target=later, daemon=True).start()
        return result
    native_llm_host.decoded_body = decoded
    return 1
''')
        return f'!(import! &self "{module}")\n!(py-call (recovery_fault.arm "{mode}"))\n'

    def assert_recorded_once(self, count=1):
        with sqlite3.connect(self.env['METTACLAW_LLM_LEDGER_PATH']) as db:
            self.assertEqual(db.execute('SELECT state FROM native_calls').fetchall(), [('completed',)] * count)
            self.assertEqual(db.execute('SELECT status FROM requests').fetchall(), [('completed',)] * count)
            for event in ('usage', 'completed'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM llm_call_history WHERE event=?', (event,)).fetchone()[0], count)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM llm_call_history WHERE event IN ('failed','unknown')").fetchone()[0], 0)

    def test_known_rollback_retries_only_local_recording_in_both_dialects(self):
        self.peer.delay = .01
        for lang in ('he', 'petta'):
            with self.subTest(lang=lang):
                ledger = self.directory / (lang + '.db')
                self.env['METTACLAW_LLM_LEDGER_PATH'] = str(ledger)
                self.env['METTACLAW_COGNITIVE_HEALTH_PATH'] = str(self.directory / (lang + '-health.json'))
                before = len(self.peer.requests)
                out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
                    self.disable_python_accounting() + self.fault('rollback') +
                    '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n', lang)
                self.assertIn('(nop)', out)
                self.assertEqual(len(self.peer.requests) - before, 1)
                with sqlite3.connect(ledger) as db:
                    self.assertEqual(db.execute('SELECT state FROM native_calls').fetchall(), [('completed',)])
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM llm_call_history WHERE event='usage'").fetchone()[0], 1)
                health = json.loads(Path(self.env['METTACLAW_COGNITIVE_HEALTH_PATH']).read_text())
                self.assertFalse(health.get('local_commit_pending'))
                self.assertEqual(health['started_count'], 1)
                self.assertEqual(health['completed_count'], 1)
                self.assertEqual(health.get('failed_count', 0), 0)

    def test_duplicate_completion_is_ignored_without_a_false_failed_turn(self):
        fixture = self.directory / 'fixture_modules/recovery_receipt.py'
        fixture.write_text('''
import os, sqlite3
def read(plan):
    with sqlite3.connect(os.environ['METTACLAW_LLM_LEDGER_PATH']) as db:
        return db.execute('SELECT result FROM native_calls WHERE id=?',(plan.correlation,)).fetchone()[0]
''')
        legacy = json.dumps(json.dumps(self.peer.reply))
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + f'!(import! &self "{fixture}")\n' +
            '!(let* (($plan (py-call (native_llm_host.native_command_plan "fixture-model" 100 "high" "input")))\n'
            '        ($first (llm:run $plan)) ($receipt (py-call (recovery_receipt.read $plan)))\n'
            '        ($epoch (py-call (native_llm_host.field $plan "epoch")))\n'
            '        ($identity (py-call (native_llm_host.field $plan "correlation"))))\n'
            '   (progn (println! (FIRST $first))\n'
            f'     (println! (SECOND (account:complete $plan $epoch $identity {legacy} $receipt)))))\n')
        self.assertIn('(FIRST (nop))', out)
        self.assertIn('(SECOND ())', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assert_recorded_once()
        health = json.loads((self.directory / 'health.json').read_text())
        self.assertEqual(health['completed_count'], 1)
        self.assertEqual(health.get('failed_count', 0), 0)

    def test_optional_observation_failure_does_not_discard_a_committed_answer(self):
        module = self.directory / 'fixture_modules/recovery_observer.py'
        module.write_text('''
import synthetic_llm
def arm():
    def fail(*args,**kwargs): raise OSError('fixture metadata unavailable')
    synthetic_llm._completion_metadata=fail
    return 1
''')
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + f'!(import! &self "{module}")\n' +
            '!(py-call (recovery_observer.arm))\n'
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n')
        self.assertIn('(nop)', out)
        self.assertIn('completion observation unavailable', self.last_stderr)
        self.assertEqual(len(self.peer.requests), 1)
        self.assert_recorded_once()

    def test_sqlite_receipt_query_matches_every_compiled_lean_case(self):
        cases = json.loads((ROOT / 'tests/fixtures/completion_recovery_cases.json').read_text())
        cases += [{**c, 'call': 'claimed'} for c in cases if c['call'] == 'submitted']
        cases += [{**c, 'request': 'unknown'} for c in cases if c['request'] == 'failed']
        for lang in ('he', 'petta'):
            dbpath = self.directory / (lang + '-observations.db')
            self.env['METTACLAW_LLM_LEDGER_PATH'] = str(dbpath)
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY,status TEXT)')
                db.execute('CREATE TABLE native_calls(id TEXT PRIMARY KEY,epoch TEXT,state TEXT,result TEXT)')
                for i, case in enumerate(cases):
                    db.execute('INSERT INTO requests VALUES(?,?)', (str(i), case['request']))
                    db.execute('INSERT INTO native_calls VALUES(?,?,?,?)',
                        (str(i), 'owned' if case['same_owner'] else 'foreign', case['call'],
                         'answer' if case['same_answer'] else 'different'))
            forms = f'!(import! &self "{ROOT}/src/native_lifetime_cetta.metta")\n'
            for i in range(len(cases)):
                metadata = f'(JsonObject ((JsonPair "id" "{i}") (JsonPair "epoch" "owned")))'
                forms += f'!(println! (OBS {i} (nl:completion-state {metadata} "answer")))\n'
            out = self.run_guest(forms, lang)
            observed = [line.strip() for line in out.splitlines() if line.lstrip().startswith('(OBS ')]
            self.assertEqual(observed, [f'(OBS {i} (LedgerCompletion {c["expected"]}))'
                                        for i, c in enumerate(cases)], lang)

    def test_coding_retry_loop_does_not_resend_after_a_local_rollback(self):
        self.peer.delay = .01
        self.env.update(METTACLAW_CODING_SESSION='1', METTACLAW_ACTIVITY_CALL_SCOPE='work-fixture',
                        METTACLAW_ACTIVITY_CALL_LANE='background')
        for purpose in ('coding', 'compaction'):
            session = self.directory / purpose; session.mkdir()
            self.env['METTACLAW_LLM_LEDGER_PATH'] = str(self.directory / (purpose + '.db'))
            request = dict(model='session', max_tokens=100, messages=[dict(role='user', content='input')])
            if purpose == 'coding': request['tools'] = []
            before = len(self.peer.requests)
            # This is the real coding:ask retry boundary, shared with compaction.
            out = self.run_guest('!(import! &self (library lib_import))\n' +
                f'!(import! &self "{ROOT}/src/native_llm.metta")\n!(import! &self "{ROOT}/src/coding_session.metta")\n' +
                self.disable_python_accounting() + self.fault('rollback') +
                f'!(println! (RESULT (coding:ask "{session}" (json-parse {json.dumps(json.dumps(request))}) 0)))\n')
            self.assertNotIn('CodingCallFailed', out)
            self.assertEqual(len(self.peer.requests) - before, 1)
            self.assert_recorded_once()
            with sqlite3.connect(self.env['METTACLAW_LLM_LEDGER_PATH']) as db:
                self.assertEqual(db.execute('SELECT purpose FROM native_calls').fetchall(), [(purpose,)])

    def test_many_local_rollbacks_keep_one_owned_answer_and_one_provider_request(self):
        self.peer.delay = .01
        self.peer.reply['usage']['fixture_padding'] = 'retained answer ' * 4000
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + self.fault('many') +
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n', timeout=60)
        self.assertIn('(nop)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assert_recorded_once()

    def test_conflicting_receipt_stops_the_coding_retry_loop(self):
        self.peer.delay = .01
        session = self.directory / 'conflict'; session.mkdir()
        request = dict(model='session', max_tokens=100, tools=[], messages=[dict(role='user', content='input')])
        out = self.run_guest('!(import! &self (library lib_import))\n' +
            f'!(import! &self "{ROOT}/src/native_llm.metta")\n!(import! &self "{ROOT}/src/coding_session.metta")\n' +
            self.disable_python_accounting() + self.fault('conflict') +
            f'!(println! (RESULT (coding:ask "{session}" (json-parse {json.dumps(json.dumps(request))}) 0)))\n')
        self.assertIn('CallFailed', out)
        self.assertIn('CompletionConflict', out)
        self.assertEqual(len(self.peer.requests), 1)

    @unittest.skipUnless(shutil.which('cc') and sys.platform == 'linux', 'requires a C compiler and Linux preload support')
    def test_landed_unknown_commit_is_read_back_without_resend(self):
        shim = self.directory / 'commit-fault.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-o', str(shim),
                        str(ROOT / 'tests/fixtures/sqlite_commit_unknown.c'), '-ldl'], check=True, capture_output=True)
        flag = self.directory / 'armed'
        witness = self.directory / 'receipt-reads'
        self.env.update(LD_PRELOAD=str(shim), LLM_TEST_COMMIT_FAULT=str(flag),
                        LLM_TEST_RECEIPT_READ=str(witness))
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + self.fault('ambiguous') +
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n'
            '!(println! (NEXT (llm:command "fixture-model" 100 "high" "fresh")))\n')
        self.assertFalse(flag.exists(), 'The injected ambiguous COMMIT was not exercised')
        self.assertEqual(witness.read_text().splitlines(), ['receipt-query'],
                         'The landed commit must be resolved by the actual native receipt query')
        self.assertEqual(len(self.peer.requests), 2, 'One original POST and one distinct fresh request')
        self.assertEqual(out.count('(nop)'), 2)
        self.assert_recorded_once(2)

    def test_a_real_long_lock_recovers_without_stranding_the_live_owner(self):
        self.peer.delay = .01
        ledger = self.directory / 'ledger.db'; held = threading.Event()
        original = Peer.do_POST
        count = [0]
        def post(handler):
            count[0] += 1
            if count[0] == 1:
                def hold():
                    db = sqlite3.connect(ledger, timeout=30, isolation_level=None)
                    db.execute('BEGIN EXCLUSIVE'); held.set()
                    time.sleep(17)
                    db.execute('COMMIT'); db.close()
                threading.Thread(target=hold, daemon=True).start()
                self.assertTrue(held.wait(10))
            return original(handler)
        with patch.object(Peer, 'do_POST', post):
            out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
                self.disable_python_accounting() +
                '!(println! (FIRST (llm:command "fixture-model" 100 "high" "input")))\n'
                '!(println! (NEXT (llm:command "fixture-model" 100 "high" "fresh")))\n', timeout=65)
        self.assertEqual(len(self.peer.requests), 2)
        self.assertEqual(out.count('(nop)'), 2)
        self.assert_recorded_once(2)


if __name__ == '__main__':
    unittest.main()

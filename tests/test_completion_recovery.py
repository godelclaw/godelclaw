"""Local storage faults cannot turn a received answer into another HTTP POST."""
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import native_ledger
import test_native_llm_boundary as boundary

ROOT, Peer = boundary.ROOT, boundary.Peer


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to the native runtime")
class CompletionRecoveryTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest
    disable_python_accounting = boundary.NativeBoundaryTests.disable_python_accounting
    fresh_ledger = boundary.NativeBoundaryTests.fresh_ledger
    records = boundary.NativeBoundaryTests.records
    column = boundary.NativeBoundaryTests.column
    events = boundary.NativeBoundaryTests.events

    def test_entrypoint_admission_dependencies_and_owner_values_in_both_dialects(self):
        for lang in ('he', 'petta'):
            with self.subTest(lang=lang):
                out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' + '''
!(println! (OWNER (process:owner)))
!(println! (LIVE (process:alive? (process:owner))))
!(let $pid (system:pid)
   (println! (STALE (process:alive? (format-args "{}:0" ($pid))))))
!(println! (INVALID (process:alive? "not-a-process")))
''', lang)
                self.assertRegex(out, r'\(OWNER "[1-9][0-9]*:[0-9]+"\)')
                self.assertRegex(out, r'\(LIVE [Tt]rue\)')
                self.assertRegex(out, r'\(STALE [Ff]alse\)')
                self.assertRegex(out, r'\(INVALID [Ff]alse\)')
                self.assertEqual(self.peer.requests, [])

    def fault(self, mode):
        """Once the request is submitted (a Python observer; the native owner decodes the
        body itself), completion writes fail the way `mode` names:
        rollback  the completion write is refused for .35 s, then the ledger is back;
        many      the completion write is refused until the 255th local retry;
        ambiguous the completion write lands but its reply is lost (once);
        conflict  another writer has already completed the call with a different answer."""
        armed = self.directory / 'completion-fault'
        if mode in ('rollback', 'many', 'ambiguous'):
            proxy, self.faults = native_ledger.faulty(
                self, self.directory, self.socket, b'llm-complete', armed,
                lose='reply' if mode == 'ambiguous' else 'request', once=mode == 'ambiguous')
            self.env.update(METTACLAW_INPUT_LEDGER_SOCKET=proxy, LLM_TEST_COMMIT_FAULT=str(armed))
        module = self.directory / "fixture_modules/recovery_fault.py"
        module.write_text('''
import json, os, threading, time
from pathlib import Path
import llm_policy
import native_llm_host

def detail(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

def complete_elsewhere(plan):
    purpose = getattr(plan, "purpose", "turn")
    usage = {"counts": {"input_tokens": 1, "output_tokens": 1}}
    llm_policy.native("llm-complete", (plan.correlation, plan.epoch, detail(usage), 1, "stop",
        detail({"finish": "stop"}), detail({"epoch": plan.epoch}), "different", 0, purpose,
        time.time(), llm_policy._request()))

def arm(kind):
    original = native_llm_host.submitted
    pending = [True]
    def received(plan, transport):
        result = original(plan, transport)
        if pending[0]:
            pending[0] = False
            if kind == "conflict":
                complete_elsewhere(plan)
                return result
            flag = Path(os.environ["LLM_TEST_COMMIT_FAULT"])
            flag.write_text("armed")
            if kind == "many":
                def paced(plan, attempt):
                    if attempt >= 255: flag.unlink(missing_ok=True)
                    return 1
                native_llm_host.completion_wait = paced
            elif kind == "rollback":
                def later():
                    time.sleep(.35)
                    flag.unlink(missing_ok=True)
                threading.Thread(target=later, daemon=True).start()
        return result
    native_llm_host.submitted = received
    return 1
''')
        return f'!(import! &self "{module}")\n!(py-call (recovery_fault.arm "{mode}"))\n'

    def assert_recorded_once(self, count=1):
        self.assertEqual(self.column('calls', 'state'), [('completed',)] * count)
        self.assertEqual(self.column('requests', 'status'), [('completed',)] * count)
        for event in ('usage', 'completed'):
            self.assertEqual(len(self.events(event)), count)
        self.assertEqual([row for row in self.events() if row['event'] in ('failed', 'unknown')], [])

    def test_known_rollback_retries_only_local_recording(self):
        self.peer.delay = .01
        for lang in ('petta',):
            with self.subTest(lang=lang):
                self.fresh_ledger(lang)
                self.env['METTACLAW_COGNITIVE_HEALTH_PATH'] = str(self.directory / (lang + '-health.json'))
                before = len(self.peer.requests)
                out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
                    self.disable_python_accounting() + self.fault('rollback') +
                    '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n', lang)
                self.assertIn('(nop)', out)
                self.assertEqual(len(self.peer.requests) - before, 1)
                self.assertTrue(self.faults, 'the completion fault was not exercised')
                self.assertEqual(self.column('calls', 'state'), [('completed',)])
                self.assertEqual(len(self.events('usage')), 1)
                health = json.loads(Path(self.env['METTACLAW_COGNITIVE_HEALTH_PATH']).read_text())
                self.assertFalse(health.get('local_commit_pending'))
                self.assertEqual(health['started_count'], 1)
                self.assertEqual(health['completed_count'], 1)
                self.assertEqual(health.get('failed_count', 0), 0)

    def test_duplicate_completion_is_ignored_without_a_false_failed_turn(self):
        fixture = self.directory / 'fixture_modules/recovery_receipt.py'
        fixture.write_text('''
import native_ledger
def read(identity):
    return native_ledger.llm_call(str(identity))["result"]
''')
        self.env["PYTHONPATH"] += os.pathsep + str(ROOT / "tests")
        legacy = json.dumps(json.dumps(self.peer.reply))
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + f'!(import! &self "{fixture}")\n' +
            '!(let* (($prepared (pcall:command-plan "fixture-model" 100 "high" "input"))\n'
            '        ($plan (llm:owner $prepared))\n'
            '        ($epoch (call:fact $plan "epoch" "")) ($identity (call:correlation $plan))\n'
            '        ($first (llm:run $prepared)) ($receipt (py-call (recovery_receipt.read $identity))))\n'
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
        # The completion observation's directory is a file: no observation can
        # be written, and the committed answer is still delivered once.
        blocker = self.directory / 'observation-blocker'
        blocker.write_text('not a directory')
        self.env['METTACLAW_LLM_COMPLETION_PATH'] = str(blocker / 'llm-completion.json')
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() +
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n')
        self.assertIn('(nop)', out)
        self.assertFalse((blocker / 'llm-completion.json').exists())
        self.assertEqual(len(self.peer.requests), 1)
        self.assert_recorded_once()

    def test_receipt_classifier_matches_every_compiled_lean_case(self):
        """The ledger's own completion-state classifier, over records built from each case."""
        cases = json.loads((ROOT / 'tests/fixtures/completion_recovery_cases.json').read_text())
        cases += [{**c, 'call': 'claimed'} for c in cases if c['call'] == 'submitted']
        cases += [{**c, 'request': 'unknown'} for c in cases if c['request'] == 'failed']
        forms = f'!(import! &self "{ROOT}/src/input_ledger.metta")\n!(bind! &cases (new-space))\n'
        for i, case in enumerate(cases):
            epoch = 'owned' if case['same_owner'] else 'foreign'
            result = 'answer' if case['same_answer'] else 'different'
            forms += (f'!(add-atom &cases (llm:request "{i}" 0 0 "{case["request"]}" none "p" "m" none none none))\n'
                      f'!(add-atom &cases (llm:call "{i}" "{epoch}" 0 0 0 0 "{case["call"]}" none none 0 0 0 none none none "turn" 0))\n'
                      f'!(add-atom &cases (llm:result "{i}" "{result}"))\n')
        for i in range(len(cases)):
            forms += f'!(println! (OBS {i} (llm-ledger:completion-state-of &cases "{i}" "owned" "answer")))\n'
        out = self.run_guest(forms)
        observed = [line.strip() for line in out.splitlines() if line.lstrip().startswith('(OBS ')]
        self.assertEqual(observed, [f'(OBS {i} (LedgerCompletion {c["expected"]}))' for i, c in enumerate(cases)])

    def test_coding_retry_loop_does_not_resend_after_a_local_rollback(self):
        self.peer.delay = .01
        self.env.update(METTACLAW_CODING_SESSION='1', METTACLAW_ACTIVITY_CALL_SCOPE='work-fixture',
                        METTACLAW_ACTIVITY_CALL_LANE='background')
        for purpose in ('coding', 'compaction'):
            session = self.directory / purpose; session.mkdir()
            self.fresh_ledger(purpose)
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
            self.assertEqual(self.column('calls', 'purpose'), [(purpose,)])

    def test_many_local_rollbacks_keep_one_owned_answer_and_one_provider_request(self):
        self.peer.delay = .01
        self.peer.reply['usage']['fixture_padding'] = 'retained answer ' * 4000
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + self.fault('many') +
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n', timeout=60)
        self.assertIn('(nop)', out)
        self.assertEqual(len(self.peer.requests), 1)
        self.assertGreaterEqual(len(self.faults), 255)
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

    def test_landed_unknown_commit_is_read_back_without_resend(self):
        self.peer.delay = .01
        out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
            self.disable_python_accounting() + self.fault('ambiguous') +
            '!(println! (RESULT (llm:command "fixture-model" 100 "high" "input")))\n'
            '!(println! (NEXT (llm:command "fixture-model" 100 "high" "fresh")))\n')
        self.assertEqual(self.faults, ['reply'], 'The landed completion with a lost reply was not exercised')
        self.assertFalse(Path(self.env['LLM_TEST_COMMIT_FAULT']).exists())
        self.assertEqual(len(self.peer.requests), 2, 'One original POST and one distinct fresh request')
        self.assertEqual(out.count('(nop)'), 2)
        self.assert_recorded_once(2)

    def test_a_long_ledger_outage_recovers_without_stranding_the_live_owner(self):
        """Every ledger verb is unreachable for 17 s from the first POST on."""
        self.peer.delay = .01
        outage = self.directory / 'outage'
        proxy, faults = native_ledger.faulty(self, self.directory, self.socket, None, outage)
        self.env['METTACLAW_INPUT_LEDGER_SOCKET'] = proxy
        original = Peer.do_POST
        count = [0]
        def post(handler):
            count[0] += 1
            if count[0] == 1:
                outage.touch()
                ending = threading.Timer(17, lambda: outage.unlink(missing_ok=True))
                ending.daemon = True
                ending.start()
            return original(handler)
        with patch.object(Peer, 'do_POST', post):
            out = self.run_guest(f'!(import! &self "{ROOT}/src/native_llm.metta")\n' +
                self.disable_python_accounting() +
                '!(println! (FIRST (llm:command "fixture-model" 100 "high" "input")))\n'
                '!(println! (NEXT (llm:command "fixture-model" 100 "high" "fresh")))\n', timeout=65)
        self.assertTrue(faults, 'the outage was not exercised')
        self.assertEqual(len(self.peer.requests), 2)
        self.assertEqual(out.count('(nop)'), 2)
        self.assert_recorded_once(2)

if __name__ == '__main__':
    unittest.main()

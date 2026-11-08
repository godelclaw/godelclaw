"""The MeTTa call-lifetime client (nl:*) and the Python one (native_lifetime) make the same native ledger
records, including at the recovery boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from execution_gateway.cetta_worker import S
import input_ack
import llm_policy
import native_ledger
import native_lifetime
import test_llm_policy_cetta as policy_tests


def meta(identity, **fields):
    result = dict(id=identity, epoch='epoch', guard='revision', cap=1024,
                  activity='', lane='', purpose='turn')
    result.update(fields)
    return result


class NativeLifetimeTests(policy_tests.NativeLedgerFixture):
    def replay_lifetime(self, steps, metadata, records=True):
        forms, expected, plans = [], [], {}
        for key, data in metadata.items():
            plans[key] = SimpleNamespace(correlation=data['id'], epoch=data['epoch'],
                                        guard=data.get('guard'), cap=data['cap'],
                                        activity=data['activity'], lane=data['lane'], purpose=data['purpose'])
        with patch.dict(os.environ, self.env, clear=True):
            for step in steps:
                kind, *args = step
                now = args.pop()
                key = args.pop(0) if kind != 'recover' else None
                data = metadata.get(key, {})
                identity = data.get('id', key)
                native = '(json:parse %s)' % policy_tests.quoted(json.dumps(data))
                with patch.object(llm_policy.time, 'time', return_value=now):
                    try:
                        if kind == 'admit':
                            forms.append('(lp:admit %s "synthetic" 10 "" %s)' % (policy_tests.quoted(identity), now))
                            with patch.object(llm_policy.uuid, 'uuid4') as uuid:
                                uuid.return_value.hex = identity
                                value = llm_policy.admit('synthetic', 10, now=now)
                            expected.append(['admitted', value])
                        elif kind == 'claim':
                            forms.append('(nl:claim %s %s)' % (native, now))
                            expected.append(['changed', native_lifetime.claim(plans[key])])
                        elif kind == 'submitted':
                            # A statement on both sides: its effect is in the records (agree()).
                            forms.append('(let $_ (nl:submitted %s (json:parse %s) %s) ok)'
                                         % (native, policy_tests.quoted(json.dumps(args[0])), now))
                            native_lifetime.submitted(plans[key], args[0]); expected.append(['ok'])
                        elif kind == 'progress':
                            forms.append('(let $_ (nl:progress %s %s %s) ok)' % (native, ' '.join(
                                '(json:parse %s)' % policy_tests.quoted(json.dumps(value)) for value in args), now))
                            native_lifetime.progress(plans[key], *args); expected.append(['ok'])
                        elif kind == 'settle':
                            state, result, reason, soft = args
                            forms.append('(nl:settle %s %s)' % (native, ' '.join(map(policy_tests.literal, (*args, now)))))
                            plan = plans[key]
                            if soft:
                                plan.submitted_at, plan.soft = 0, 1
                            elif hasattr(plan, 'submitted_at'):
                                del plan.submitted_at
                            with patch.object(native_lifetime.time, 'monotonic', return_value=1):
                                changed = native_lifetime.settle(plan, state, result, reason)
                            expected.append(['changed', changed])
                        elif kind == 'fail':
                            sent, status = args
                            forms.append('(lp:fail %s %s %s JsonNull %s)' %
                                         (policy_tests.quoted(identity), policy_tests.literal(sent), policy_tests.literal(status), now))
                            expected.append(['changed', llm_policy.fail(identity, sent=sent, status=status, now=now)])
                        elif kind == 'complete':
                            usage = dict(prompt_tokens=12, completion_tokens=3)
                            forms.append('(lp:complete %s (json:parse %s) "stop" %s)' %
                                         (policy_tests.quoted(identity), policy_tests.quoted(json.dumps(usage)), now))
                            expected.append(['changed', llm_policy.complete(identity, dict(usage=usage,
                                                                  choices=[dict(finish_reason='stop')]))])
                        elif kind == 'complete-owned':
                            usage, finish, canonical, soft = args
                            forms.append('(nl:complete %s (json:parse %s) %s %s %s %s)' %
                                         (native, policy_tests.quoted(json.dumps(usage)),
                                          policy_tests.literal(finish), policy_tests.literal(canonical), soft, now))
                            changed = llm_policy.complete(identity, dict(usage=usage, choices=[dict(finish_reason=finish)]))
                            if changed:
                                plan = plans[key]
                                if soft:
                                    plan.submitted_at, plan.soft = 0, 1
                                with patch.object(native_lifetime.time, 'monotonic', return_value=1):
                                    native_lifetime.settle(plan, 'completed', canonical)
                            expected.append(['changed', changed])
                        elif kind == 'fail-owned':
                            reason, status, retry, soft = args
                            forms.append('(nl:fail %s %s)' % (native,
                                         ' '.join(map(policy_tests.literal, (*args, now)))))
                            sent = data.get('sent', False)
                            llm_policy.fail(identity, sent=sent, status=status, retry_after=retry, now=now)
                            state = 'uncertain' if sent and reason == 'transport-error' else 'failed'
                            plan = plans[key]
                            if soft:
                                plan.submitted_at, plan.soft = 0, 1
                            with patch.object(native_lifetime.time, 'monotonic', return_value=1):
                                changed = native_lifetime.settle(plan, state, reason=reason)
                            expected.append(['changed', changed])
                        elif kind == 'recover':
                            forms.append('(let $_ (lp:native "llm-recover" (%s (lp:request-id))) ok)' % now)
                            native_lifetime.recover(); expected.append(['ok'])
                        else:
                            self.fail('unknown lifetime operation ' + kind)
                    except llm_policy.IntegrityError as error:
                        # A constraint answer is exact; storage faults stay coarse.
                        expected.append(policy_tests.python_error(error))
                    except input_ack.LedgerUnavailable:
                        expected.append(['error'])
                    except (ValueError, TypeError, OverflowError) as error:
                        expected.append(policy_tests.python_error(error))
        actual = self.native(forms, module='native_lifetime_cetta.metta')
        self.assertEqual(actual, expected)
        if records:
            self.agree()

    def seed(self, rows):
        """Identical preexisting records on both ledgers, made with the ledger's own verbs: an admission at 900
        (unless admitted=False), a claim by the row's process (a scoped call, so that several may be active), the
        call's state and then the request's status at `updated`. These are never live agent ledgers."""
        for socket in (self.python_socket, self.native_socket):
            def call(verb, *arguments):
                arguments = tuple(llm_policy._argument(item) for item in arguments)
                return input_ack._ledger_call(verb, arguments + ("rq1." + uuid.uuid4().hex,), 30, socket)
            for number, row in enumerate(rows):
                identity, updated, purpose = row['id'], float(row.get('updated', 1000)), row.get('purpose', 'turn')
                if row.get('admitted', True):
                    call("llm-admit", identity, "synthetic", 10, "", "managed", "godelclaw",
                         row.get('owner', llm_policy._owner), '{"provider":"synthetic"}', 900.0)
                pid = row.get('pid', os.getpid())
                start = row['start'] if 'start' in row else native_lifetime.process_start(os.getpid())
                call("llm-claim", identity, "epoch", pid, start, "revision", "seed-%d" % number, "background", purpose,
                     False, True, 100000, 1, S("ok"), '{"epoch":"epoch"}', updated)
                state = row.get('state', 'claimed')
                if state == 'submitted':
                    call("llm-submitted", identity, "epoch", 0, updated)
                elif state != 'claimed':
                    call("llm-settle", identity, "epoch", purpose, state, row.get('result'), None, 0, updated)
                request = row.get('request', 'pending')
                with patch.dict(os.environ, {input_ack.LEDGER_SOCKET: socket}), \
                        patch.object(llm_policy.time, 'time', return_value=updated):
                    if request == 'completed':
                        llm_policy.complete_native(identity, dict(usage={}, choices=[dict(finish_reason='stop')]))
                    elif request == 'failed':
                        llm_policy.fail_native(identity, sent=False, now=updated)

    def test_claim_submit_progress_wrong_epoch_and_uncertain_settlement(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('claim', 'a', 1000),
            ('submitted', 'wrong', 99, 1001), ('submitted', 'a', 7, 1001),
            ('progress', 'wrong', 999, 999, 0, 1002), ('progress', 'a', 30, 4, 1, 1002),
            ('settle', 'wrong', 'completed', 'receipt', None, 0, 1003),
            ('settle', 'a', 'uncertain', None, 'transport-error', 0, 1004),
            ('settle', 'a', 'completed', 'late', None, 1, 1005),
        ], dict(a=meta('a', activity='one', lane='interactive'),
                wrong=meta('a', activity='one', lane='interactive', epoch='wrong')))

    def test_combined_completion_commits_usage_request_and_receipt_together(self):
        usage = dict(input_tokens=10, cache_read_input_tokens=20,
                     cache_creation_input_tokens=30, output_tokens=4)
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('complete-owned', 'a', usage, 'stop', 'canonical receipt', 1, 1002),
            ('complete-owned', 'a', dict(output_tokens=900), 'stop', 'duplicate', 0, 1003),
        ], dict(a=meta('a')))

    def test_combined_failure_commits_cooldown_and_native_state_together(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('fail-owned', 'a', 'http-status', 429, 10, 0, 1002),
            ('admit', 'b', 1013), ('claim', 'b', 1013), ('submitted', 'b', 8, 1014),
            ('fail-owned', 'b', 'transport-error', None, None, 1, 1015),
        ], dict(a=meta('a', sent=True), b=meta('b', sent=True)))

    def test_combined_settlement_fault_cannot_leave_usage_or_request_half_committed(self):
        self.seed([dict(id='a', state='submitted')])
        before = policy_tests.snapshot(self.native_socket, self.native_owners)
        proxy, faults = native_ledger.faulty(self, self.root, self.native_socket, b'llm-complete')
        refusing_fail, more = native_ledger.faulty(self, self.root, proxy, b'llm-fail')
        self.native_socket, real = refusing_fail, self.native_socket
        metadata = '(json:parse %s)' % policy_tests.quoted(json.dumps(meta('a', sent=True)))
        try:
            self.assertEqual(self.native([
                '(nl:complete %s (json:parse "{\\\"output_tokens\\\":4}") "stop" "receipt" 0 1001)' % metadata,
                '(nl:fail %s "http-status" 429 10 0 1002)' % metadata,
            ], module='native_lifetime_cetta.metta'), [['error']] * 2)
        finally:
            self.native_socket = real
        self.assertTrue(faults and more)
        self.assertEqual(policy_tests.snapshot(self.native_socket, self.native_owners), before)

    def test_wrong_epoch_cannot_add_usage_or_fail_someone_elses_request(self):
        self.seed([dict(id='a', state='submitted')])
        before = policy_tests.snapshot(self.native_socket, self.native_owners)
        metadata = '(json:parse %s)' % policy_tests.quoted(json.dumps(meta('a', epoch='wrong', sent=True)))
        self.assertEqual(self.native([
            '(nl:complete %s (json:parse "{\\\"output_tokens\\\":4}") "stop" "receipt" 0 1001)' % metadata,
            '(nl:fail %s "http-status" 429 10 0 1002)' % metadata,
        ], module='native_lifetime_cetta.metta'), [['changed', False], ['changed', False]])
        self.assertEqual(policy_tests.snapshot(self.native_socket, self.native_owners), before)

    def test_scoped_claims_preserve_capacity_and_interactive_reserve(self):
        metadata = dict(a=meta('a', activity='one', lane='interactive'),
                        b=meta('b', activity='two', lane='background'),
                        c=meta('c', activity='three', lane='background'),
                        d=meta('d', activity='one', lane='interactive'), e=meta('e'))
        self.replay_lifetime([(kind, key, 1000) for key in metadata for kind in ('admit', 'claim')], metadata)

    def test_real_processes_share_activity_exclusion_capacity_and_reserve(self):
        metadata = [meta('a', activity='foreground', lane='interactive'),
                    meta('b', activity='foreground', lane='interactive'),
                    meta('c', activity='background-one', lane='background'),
                    meta('d', activity='background-two', lane='background')]
        for data in metadata:
            input_ack._ledger_call("llm-admit", (data['id'], "synthetic", 10, "", "managed", "godelclaw", llm_policy._owner,
                                                 '{"provider":"synthetic"}', 900.0, "rq1." + uuid.uuid4().hex), 30, self.native_socket)
        children = []
        env = dict(self.env, METTACLAW_INPUT_LEDGER_SOCKET=self.native_socket)
        try:
            for data in metadata:
                script, output = self.root / (data['id'] + '.metta'), self.root / (data['id'] + '.out')
                script.write_text('!(import! &self ' + policy_tests.quoted(str(policy_tests.ROOT / 'src/native_lifetime_cetta.metta')) + ')\n'
                    '!(system:read-line)\n'
                    '!(let $answer (nl:claim (json:parse ' + policy_tests.quoted(json.dumps(data)) + ') 1000) '
                    '(fs:write ' + policy_tests.quoted(str(output)) + ' (repr $answer)))\n'
                    '!(system:read-line)\n')
                child = subprocess.Popen([policy_tests.CETTA, '--lang', 'petta', '--import-mode',
                    'ancestor-walk', '--quiet', str(script)], cwd=policy_tests.ROOT, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                children.append((child, output))
            for child, _ in children:
                child.stdin.write('claim\n'); child.stdin.flush()
            deadline = time.monotonic() + 20
            while not all(output.exists() for _, output in children) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(all(output.exists() for _, output in children),
                            'concurrent claim did not publish its result')
            answers = [output.read_text().lower() for _, output in children]
            self.assertEqual(answers.count('(ledgerchanged true)'), 2, answers)
            self.assertEqual(answers.count('(ledgerchanged false)'), 2, answers)
            claimed = [row for row in native_ledger.llm_records('calls', self.native_socket) if row['state'] == 'claimed']
            self.assertEqual(sorted(row['lane'] for row in claimed), ['background', 'interactive'])
            self.assertEqual(len({row['activity'] for row in claimed}), 2)
            self.assertEqual(sum(1 for row in native_ledger.llm_records('requests', self.native_socket)
                                 if row['status'] == 'pending'), 4)
        finally:
            for child, _ in children:
                try:
                    out, err = child.communicate('release\n', timeout=5)
                    self.assertEqual(child.returncode, 0, out[-1000:] + err[-1000:])
                except (subprocess.TimeoutExpired, BrokenPipeError):
                    child.kill(); child.communicate()

    def test_compaction_uncertain_is_abandoned_but_accounting_stays_unknown(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('settle', 'a', 'uncertain', 'discard this receipt', 'transport-error', 0, 1002),
        ], dict(a=meta('a', purpose='compaction')))
        notify = Mock()
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(llm_policy.time, 'time', return_value=1003), \
             patch.dict('sys.modules', telegram=SimpleNamespace(enqueue_runtime_event=notify)):
            self.assertEqual(native_lifetime.status(), [])
            native_lifetime.notify()
            notify.assert_not_called()

    def test_completed_request_gap_has_a_grace_then_recovers_without_replay(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('complete', 'a', 1001),
            ('recover', 1001), ('recover', 1003),
        ], dict(a=meta('a')))

    def test_failed_claim_and_failed_submission_reconcile_differently(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('fail', 'a', False, None, 1001),
            ('recover', 1003),
            ('admit', 'b', 1004), ('claim', 'b', 1004), ('submitted', 'b', 7, 1005),
            ('fail', 'b', True, None, 1006), ('recover', 1008),
        ], dict(a=meta('a'), b=meta('b')))

    def test_compaction_terminal_reconciliation_does_not_rewrite_request_status(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('fail', 'a', True, None, 1001),
            ('recover', 1003),
        ], dict(a=meta('a', purpose='compaction')))

    def test_foreign_owner_death_pid_reuse_and_unknown_birth_stamp(self):
        self.seed([dict(id='live'), dict(id='dead', pid=0, start='old', owner='0:0'),
                   dict(id='reused', start='wrong-birth-stamp'), dict(id='unknown-start', start=None)])
        self.replay_lifetime([('recover', 1003)], {})
        calls = native_ledger.llm_records('calls', self.native_socket)
        self.assertEqual([row['state'] for row in calls if row['id'] == 'live'], ['claimed'])
        self.assertEqual(sum(1 for row in calls if row['state'] == 'uncertain'), 3)

    def test_a_failed_settlement_changes_nothing_on_either_ledger_and_the_next_one_commits(self):
        refused = []
        for name in ('python_socket', 'native_socket'):
            (self.root / name).mkdir()
            proxy, faults = native_ledger.faulty(self, self.root / name, getattr(self, name), b'uncertain', count=3)
            setattr(self, name, proxy)
            refused.append(faults)
        self.env['METTACLAW_INPUT_LEDGER_SOCKET'] = self.python_socket
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('settle', 'a', 'uncertain', None, 'transport-error', 0, 1002),
            ('settle', 'a', 'completed', 'receipt', None, 0, 1003),
        ], dict(a=meta('a')))
        self.assertEqual([len(faults) for faults in refused], [3, 3])

    def test_purpose_is_validated_only_after_refusal_checks(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000),
            ('admit', 'bad', 1001), ('claim', 'bad', 1001),
            ('settle', 'a', 'completed', 'receipt', None, 0, 1002),
            ('claim', 'bad', 1003),
        ], dict(a=meta('a', activity='one', lane='interactive'),
                bad=meta('bad', activity='one', lane='interactive', purpose='invalid')))

    def test_bad_activity_reports_an_error_before_invalid_capacity_or_purpose(self):
        self.env['METTACLAW_ACTIVITY_CALL_CAPACITY'] = '0'
        self.replay_lifetime([('admit', 'bad', 1000), ('claim', 'bad', 1000)],
                             dict(bad=meta('bad', activity='non-ascii-試験', lane='interactive', purpose='invalid')))

    def test_invalid_activity_and_lane_are_errors_even_with_no_other_owner(self):
        data = dict(a=meta('a', activity='bad activity', lane='background'),
                    b=meta('b', activity='x' * 65, lane='background'),
                    c=meta('c', activity='valid', lane='sideways'))
        self.replay_lifetime([(kind, key, 1000) for key in data for kind in ('admit', 'claim')], data)

    def test_activity_values_claim_as_python_sees_them(self):
        # `not activity` decides the unscoped path and the regex reads str(activity): both clients decide
        # alike. An activity that is not text is stored as each client holds it (Python its str()), so the
        # records are not compared here.
        data, steps = {}, []
        cases = [(activity, 'interactive') for activity in (False, 0, None, True, 7, 7.5, -1.5, 1e30)]
        # The unscoped path never reads the lane.
        cases += [(activity, '') for activity in (False, 0, '')]
        for i, (activity, lane) in enumerate(cases):
            key = 'activity-%d' % i
            data[key] = meta(key, activity=activity, lane=lane)
            now = 1000 + 3 * i
            steps += [('admit', key, now), ('claim', key, now + 1),
                      ('settle', key, 'completed', 'receipt', None, 0, now + 2)]
        self.replay_lifetime(steps, data, records=False)

    def test_a_boolean_identity_binds_as_an_integer(self):
        # True binds as 1, and TEXT affinity matches the stored id '1'.
        self.replay_lifetime([('admit', 'one', 1000), ('claim', 'one', 1001),
                              ('submitted', 'flag', 7, 1002)],
                             dict(one=meta('1'), flag=meta(True)))

    def test_fractional_timestamps_survive_the_recovery_compare_and_set(self):
        stamp = 1792850686.1234567
        self.seed([dict(id='a', request='failed', state='submitted', updated=stamp)])
        self.replay_lifetime([('recover', stamp + 1.9999), ('recover', stamp + 2.0)], {})

    def test_dead_orphan_without_a_request_row_is_recovered(self):
        self.seed([dict(id='orphan', pid=0, start='old', admitted=False)])
        self.replay_lifetime([('recover', 1003)], {})

    def test_settlement_records_expired_soft_wait_without_a_progress_poll(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('settle', 'a', 'completed', 'receipt', None, 1, 1002),
        ], dict(a=meta('a')))
        self.assertEqual(native_ledger.llm_call('a', socket=self.native_socket)['soft'], 1)

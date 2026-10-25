"""Native call ownership matches Python, including its recovery boundaries."""
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import llm_policy
import native_lifetime
import usage_history
import test_llm_policy_cetta as policy_tests


def meta(identity, **fields):
    result = dict(id=identity, epoch='epoch', guard='revision', cap=1024,
                  activity='', lane='', purpose='turn')
    result.update(fields)
    return result


class NativeLifetimeTests(policy_tests.NativeLedgerFixture):
    def replay_lifetime(self, steps, metadata):
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
                            forms.append('(nl:submitted %s %s %s)' % (native, args[0], now))
                            native_lifetime.submitted(plans[key], args[0]); expected.append(['ok'])
                        elif kind == 'progress':
                            forms.append('(nl:progress %s %s)' % (native, ' '.join(map(policy_tests.literal, (*args, now)))))
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
                            forms.append('(nl:recover %s)' % now)
                            native_lifetime.recover(); expected.append(['ok'])
                        else:
                            self.fail('unknown lifetime operation ' + kind)
                    except (ValueError, TypeError, sqlite3.Error):
                        expected.append(['error'])
        actual = self.native(forms, module='native_lifetime_cetta.metta')
        self.assertEqual(actual, expected)
        self.assertEqual(policy_tests.snapshot(self.native_db), policy_tests.snapshot(self.python_db))

    def seed(self, rows):
        """Seed identical preexisting rows; these are never live agent ledgers."""
        for path in (self.python_db, self.native_db):
            with closing(sqlite3.connect(path)) as conn:
                for row in rows:
                    identity = row['id']
                    conn.execute("INSERT INTO requests(id,started,reserved,status,provider,mode,owner,fingerprint) "
                                 "VALUES(?,900,10,?,'synthetic','godelclaw',?,'')",
                                 (identity, row.get('request', 'pending'), row.get('owner', llm_policy._owner)))
                    conn.execute("INSERT INTO native_calls(id,epoch,pid,process_start,created,updated,state,guard,result,purpose) "
                                 "VALUES(?,'epoch',?,?,900,?,?,'revision',?,?)",
                                 (identity, row.get('pid', os.getpid()),
                                  row.get('start', native_lifetime.process_start(os.getpid())),
                                  row.get('updated', 1000), row.get('state', 'claimed'),
                                  row.get('result'), row.get('purpose', 'turn')))
                    conn.execute("INSERT INTO llm_call_history(call_id,event,observed,detail) VALUES(?,'admitted',900,?)",
                                 (identity, '{"provider":"synthetic"}'))
                conn.commit()

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
        with closing(sqlite3.connect(self.native_db)) as db:
            db.execute("CREATE TRIGGER fixture_abort BEFORE UPDATE OF state ON native_calls "
                       "WHEN NEW.state IN ('completed','failed') BEGIN SELECT RAISE(ABORT,'fixture fault'); END")
            db.commit()
        before = policy_tests.snapshot(self.native_db)
        metadata = '(json:parse %s)' % policy_tests.quoted(json.dumps(meta('a', sent=True)))
        self.assertEqual(self.native([
            '(nl:complete %s (json:parse "{\\\"output_tokens\\\":4}") "stop" "receipt" 0 1001)' % metadata,
            '(nl:fail %s "http-status" 429 10 0 1002)' % metadata,
        ], module='native_lifetime_cetta.metta'), [['error'], ['error']])
        self.assertEqual(policy_tests.snapshot(self.native_db), before)

    def test_wrong_epoch_cannot_add_usage_or_fail_someone_elses_request(self):
        self.seed([dict(id='a', state='submitted')])
        before = policy_tests.snapshot(self.native_db)
        metadata = '(json:parse %s)' % policy_tests.quoted(json.dumps(meta('a', epoch='wrong', sent=True)))
        self.assertEqual(self.native([
            '(nl:complete %s (json:parse "{\\\"output_tokens\\\":4}") "stop" "receipt" 0 1001)' % metadata,
            '(nl:fail %s "http-status" 429 10 0 1002)' % metadata,
        ], module='native_lifetime_cetta.metta'), [['changed', False], ['changed', False]])
        self.assertEqual(policy_tests.snapshot(self.native_db), before)

    def test_pruned_terminal_call_still_accepts_only_its_own_late_usage(self):
        self.seed([dict(id='a', state='failed', request='failed')])
        for path in (self.python_db, self.native_db):
            with closing(sqlite3.connect(path)) as db:
                db.execute("INSERT INTO llm_call_history(call_id,event,observed,detail) VALUES('a','claimed',900,?)",
                           (json.dumps(dict(epoch='epoch')),))
                db.execute("DELETE FROM native_calls WHERE id='a'")
                db.commit()
        wrong = '(json:parse %s)' % policy_tests.quoted(json.dumps(meta('a', epoch='wrong')))
        self.assertEqual(self.native(['(nl:complete %s (json:parse "{\\\"output_tokens\\\":900}") "stop" "foreign" 0 1001)' % wrong],
                                     module='native_lifetime_cetta.metta'), [['changed', False]])
        self.assertEqual(policy_tests.snapshot(self.native_db), policy_tests.snapshot(self.python_db))
        self.replay_lifetime([('complete-owned', 'a', dict(output_tokens=4), 'stop', 'late receipt', 0, 1002)],
                             dict(a=meta('a')))

    def test_existing_ledger_migration_preserves_rows_and_notification_compatibility(self):
        self.seed([dict(id='old-terminal', state='completed', request='completed'), dict(id='active')])
        for path in (self.python_db, self.native_db):
            with closing(sqlite3.connect(path)) as db:
                for name in ('owner', 'fingerprint'):
                    db.execute('ALTER TABLE requests DROP COLUMN ' + name)
                for name in ('activity', 'lane', 'purpose', 'notified'):
                    db.execute('ALTER TABLE native_calls DROP COLUMN ' + name)
                db.commit()
        with patch.dict(os.environ, self.env, clear=True), patch.object(llm_policy.time, 'time', return_value=1000):
            with native_lifetime.transaction():
                pass
        self.assertEqual(self.native(['(lp:init)'], module='native_lifetime_cetta.metta'), [['ok']])
        self.assertEqual(policy_tests.snapshot(self.native_db), policy_tests.snapshot(self.python_db))
        with closing(sqlite3.connect(self.native_db)) as db:
            self.assertEqual(db.execute('SELECT id,notified FROM native_calls ORDER BY id').fetchall(),
                             [('active', 0), ('old-terminal', 1)])

    def test_upgrading_an_unrecorded_ledger_does_not_invent_historical_usage(self):
        self.seed([dict(id='old-terminal', state='completed', request='completed'), dict(id='active')])
        with closing(sqlite3.connect(self.native_db)) as db:
            db.execute('DROP TABLE llm_call_history')
            db.commit()
        before = time.time()
        self.assertEqual(self.native(['(lp:init)'], module='native_lifetime_cetta.metta'), [['ok']])
        after = time.time()
        report = usage_history.report(self.native_db)
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['legacy_request_rows_without_history'], 2)
        self.assertEqual(report['totals'], {})
        self.assertTrue(before <= report['recording_started'] <= after)

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
        self.seed([dict(id=data['id'], state='completed') for data in metadata])
        with closing(sqlite3.connect(self.native_db)) as db:
            db.execute('DELETE FROM native_calls')
            db.commit()
        children = []
        env = dict(self.env, METTACLAW_LLM_LEDGER_PATH=str(self.native_db))
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
            with closing(sqlite3.connect(self.native_db)) as db:
                self.assertEqual(db.execute('SELECT lane,COUNT(*) FROM native_calls '
                    "WHERE state='claimed' GROUP BY lane ORDER BY lane").fetchall(),
                    [('background', 1), ('interactive', 1)])
                self.assertEqual(db.execute("SELECT COUNT(DISTINCT activity) FROM native_calls WHERE state='claimed'").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0], 4)
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
        with closing(sqlite3.connect(self.native_db)) as conn:
            self.assertEqual(conn.execute("SELECT state FROM native_calls WHERE id='live'").fetchone()[0], 'claimed')
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM native_calls WHERE state='uncertain'").fetchone()[0], 3)

    def test_mid_settlement_sql_failure_rolls_both_ledgers_back(self):
        for path in (self.python_db, self.native_db):
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TRIGGER fixture_abort BEFORE UPDATE OF status ON requests "
                             "WHEN NEW.status='unknown' BEGIN SELECT RAISE(ABORT,'fixture fault'); END")
                conn.commit()
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('settle', 'a', 'uncertain', None, 'transport-error', 0, 1002),
            ('settle', 'a', 'completed', 'receipt', None, 0, 1003),
        ], dict(a=meta('a')))

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

    def test_fractional_timestamps_survive_the_recovery_compare_and_set(self):
        stamp = 1792850686.1234567
        self.seed([dict(id='a', request='failed', state='submitted', updated=stamp)])
        self.replay_lifetime([('recover', stamp + 1.9999), ('recover', stamp + 2.0)], {})

    def test_dead_orphan_without_a_request_row_is_recovered(self):
        self.seed([dict(id='orphan', pid=0, start='old', owner='0:0')])
        for path in (self.python_db, self.native_db):
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("DELETE FROM requests WHERE id='orphan'")
                conn.commit()
        self.replay_lifetime([('recover', 1003)], {})

    def test_settlement_records_expired_soft_wait_without_a_progress_poll(self):
        self.replay_lifetime([
            ('admit', 'a', 1000), ('claim', 'a', 1000), ('submitted', 'a', 7, 1001),
            ('settle', 'a', 'completed', 'receipt', None, 1, 1002),
        ], dict(a=meta('a')))
        with closing(sqlite3.connect(self.native_db)) as conn:
            self.assertEqual(conn.execute("SELECT soft_elapsed FROM native_calls WHERE id='a'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT typeof(process_start) FROM native_calls WHERE id='a'").fetchone()[0], 'text')

    def test_receipt_pruning_never_retires_an_active_call(self):
        self.seed([dict(id='terminal-%03d' % i, state='completed', request='completed',
                        updated=900 + i / 10, result='receipt') for i in range(260)] +
                  [dict(id='active', updated=0)])
        self.replay_lifetime([('recover', 1000)], {})
        with closing(sqlite3.connect(self.native_db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM native_calls WHERE state='completed'").fetchone()[0], 256)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM native_calls WHERE result IS NOT NULL").fetchone()[0], 32)
            self.assertEqual(conn.execute("SELECT state FROM native_calls WHERE id='active'").fetchone()[0], 'claimed')

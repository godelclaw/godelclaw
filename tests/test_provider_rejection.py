"""A rejected request waits for evidence of correction, independently of time."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cognitive_health
import provider_rejection as rejection
import test_native_llm_boundary as boundary


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, METTACLAW_COGNITIVE_HEALTH_PATH=str(self.root / 'health.json'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.context = {'model': 'fixture', 'configuration': 'v1', 'operator_input': [1, 100, 100]}
        self.facts = mock.patch.object(rejection, 'context', side_effect=lambda: dict(self.context))
        self.facts.start()
        self.addCleanup(self.facts.stop)
        self.notify = mock.patch.object(rejection, 'notify', return_value=0)
        self.notify.start()
        self.addCleanup(self.notify.stop)

    def block(self):
        cognitive_health.expect_turn(now=1)
        rejection.record(400, 'invalid_input', 'Field too long')
        cognitive_health.turn_failed('provider-rejected-400', now=2)

    def test_time_and_restart_do_not_authorize_a_rejected_request(self):
        self.block()
        for now in (2, 500, 1e20):
            self.assertTrue(rejection.blocked())
            self.assertEqual(cognitive_health.retry_wait_seconds(now), 1)
        importlib.reload(rejection)
        with mock.patch.object(rejection, 'context', return_value=dict(self.context)):
            self.assertTrue(rejection.blocked())
        self.assertEqual(cognitive_health.snapshot()['obligation_since'], 1)

    def test_unwritable_receipt_keeps_the_known_rejection_in_memory(self):
        with mock.patch.object(cognitive_health, '_write', side_effect=OSError('full disk')):
            self.block()
            self.assertTrue(rejection.blocked())
        self.assertTrue(rejection.blocked())
        self.assertIn('provider_rejection', cognitive_health.snapshot())

    def test_each_observed_correction_releases_the_gate(self):
        for field, new in [('model', 'other'), ('configuration', 'v2'),
                           ('operator_input', [2, 101, 100]), ('release', 'new')]:
            with self.subTest(field=field):
                self.context = {'model': 'fixture', 'configuration': 'v1',
                                'operator_input': [1, 100, 100], 'release': 'old'}
                self.block()
                self.context[field] = new
                self.assertFalse(rejection.blocked())
                self.assertNotIn('provider_rejection', cognitive_health.snapshot())

    def test_new_or_unavailable_observations_are_not_corrections(self):
        self.block()
        saved = self.context
        self.context = {}
        self.assertTrue(rejection.blocked())
        self.context = {**saved, 'release': 'first-observation'}
        self.assertTrue(rejection.blocked())
        self.context['release'] = 'changed'
        self.assertFalse(rejection.blocked())

    def test_input_arriving_during_http_releases_the_old_request(self):
        captured = dict(self.context)
        self.context['operator_input'] = [2, 101, 100]
        rejection.record(400, captured=captured)
        self.assertFalse(rejection.blocked())

    def test_first_input_can_recover_after_inbox_observation_was_absent(self):
        self.context.pop('operator_input')
        self.block()
        self.context['operator_input'] = [1, 101, 101]
        self.assertFalse(rejection.blocked())

    def test_real_context_tracks_policy_and_checkpoint_without_opening_missing_db(self):
        import sqlite3
        self.facts.stop()
        db_path = self.root / 'strategy.db'
        with mock.patch.dict(os.environ, METTACLAW_STRATEGY_PATH=str(db_path)):
            before = rejection.context()
            self.assertFalse(db_path.exists())
            (self.root / 'context-policy.json').write_text('{"mode":"full"}')
            self.assertNotEqual(rejection.context()['configuration'], before['configuration'])
            with sqlite3.connect(db_path) as db:
                db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)')
                db.execute('INSERT INTO meta VALUES (?,?)', ('task_window:conversation:fixture', 'old'))
            before = rejection.context()
            rejection.record(400, captured=before)
            with sqlite3.connect(db_path) as db:
                db.execute('UPDATE meta SET value=?', ('new checked handoff',))
            self.assertFalse(rejection.blocked())

    def test_transient_and_auth_failures_keep_their_existing_recovery(self):
        for status in (401, 403, 408, 409, 425, 429, 500, 503):
            self.assertFalse(rejection.record(status))
        self.assertNotIn('provider_rejection', cognitive_health.snapshot())

    def test_blocked_wait_remains_interruptible_without_attempts(self):
        self.block()
        with mock.patch('lifecycle.cognition_enabled', side_effect=[1, 1, 0]), \
             mock.patch('helper.recycle_requested', return_value=0), \
             mock.patch('process_supervision.progress'), \
             mock.patch.object(cognitive_health.time, 'sleep') as sleep:
            self.assertEqual(cognitive_health.wait_for_retry(0), 'operator stopped during provider retry')
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(rejection.blocked())

    def test_notice_has_one_durable_outbox_identity(self):
        self.block()
        self.notify.stop()
        import mode_channel
        writes = []
        with mock.patch.object(mode_channel, 'resolve', return_value={'chat': '7', 'thread': 0}), \
             mock.patch.object(mode_channel, 'state', return_value=self.root), \
             mock.patch.object(mode_channel, 'store', side_effect=lambda path, value: writes.append((path, value))):
            self.assertEqual(rejection.notify(), 1)
            self.assertEqual(rejection.notify(), 0)
            rejection.record(400, 'invalid_input', 'Field too long')
        self.assertEqual(len(writes), 1)
        self.assertIn('HTTP 400', writes[0][1]['commands'][0][1])
        self.assertTrue(cognitive_health.snapshot()['provider_rejection']['alert_queued'])

    def test_error_receipts_redact_credentials_and_ignore_html_bodies(self):
        with mock.patch.dict(os.environ, FIXTURE_API_KEY='fixture-private-token'):
            code, text = rejection.error_fields(json.dumps({'error': {
                'code': 'bad_request', 'message': 'bad fixture-private-token\nfield'}}))
        self.assertEqual((code, text), ('bad_request', 'bad [redacted] field'))
        self.assertEqual(rejection.error_fields('<html>private text</html>'), ('', 'Non-JSON provider rejection'))


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to a Python/HTTP release')
class NativeRejectionTests(unittest.TestCase):
    setUp = boundary.NativeBoundaryTests.setUp
    tearDown = boundary.NativeBoundaryTests.tearDown
    run_guest = boundary.NativeBoundaryTests.run_guest

    def test_native_rejection_is_visible_and_not_sent_again_after_restart(self):
        self.peer.delay, self.peer.status = .01, 400
        self.peer.reply = {'error': {'code': 'invalid_input', 'message': 'Field too long'}}
        forms = f'!(import! &self "{boundary.ROOT}/src/native_llm.metta")\n'
        call = '!(println! (RESULT (llm:command "fixture-model" 42 "low" "retained input")))\n'
        self.run_guest(forms + call + call)
        self.run_guest(forms + call)  # A fresh process sees the same durable blockage.
        self.assertEqual(len(self.peer.requests), 1)
        health = json.loads((self.directory / 'health.json').read_text())
        self.assertEqual(health['provider_rejection']['code'], 'invalid_input')
        self.assertEqual(health['provider_rejection']['status'], 400)
        self.assertEqual(health['failed_count'], 1)
        # Selecting a corrected model is a new request, without discarding input.
        self.env['SYNTHETIC_MODEL'] = 'corrected-fixture'
        self.peer.status = 200
        self.peer.reply = {'choices': [{'message': {'content': '(nop)'}, 'finish_reason': 'stop'}]}
        self.run_guest(forms + call.replace('fixture-model', 'corrected-fixture'))
        self.assertEqual(len(self.peer.requests), 2)
        self.assertEqual(self.peer.requests[-1][2]['messages'][-1]['content'], 'retained input')


if __name__ == '__main__':
    unittest.main()

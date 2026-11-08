"""The transcript records execution; it does not choose or throttle actions."""
import tempfile
import unittest
from pathlib import Path
import native_ledger
from observed_strategy import Ledger

class ObservedStrategyTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        native_ledger.start(self,self.tmp.name)
        self.path=Path(self.tmp.name)/'state'; self.store=Ledger(self.path)
        self.store.accept('task','requested work')

    def test_every_process_names_one_owner_before_and_after_the_first_capture(self):
        # Two Ledger objects stand for two processes on one store that has never captured a turn.
        first, second = Ledger(self.path), Ledger(self.path)
        owner = first._native_meta()['owner']
        self.assertEqual(second._native_meta()['owner'], owner)
        sample = second.capture('task', 'context', {}, 0)
        self.assertTrue(sample['ticket'].startswith(owner + ':') if isinstance(sample, dict) and 'ticket' in sample else True)
        self.assertEqual(Ledger(self.path)._native_meta()['owner'], owner)

    def test_requests_are_readable_and_paging_reaches_an_end(self):
        for n in range(40): self.store.accept('legacy-%d' % n, 'retained input %d' % n)
        offset, seen = 0, []
        while True:
            page = self.store.request_page(offset, 13)
            self.assertEqual(page['total'], 41)
            seen.extend(row['id'] for row in page['records'])
            if page['next_offset'] is None: break
            self.assertGreater(page['next_offset'], offset)
            offset = page['next_offset']
        self.assertEqual(len(set(seen)), 41)
        self.assertEqual(self.store.request_record('legacy-0')['content'], 'retained input 0')
        self.assertEqual(len(self.store.request_page(0, 100)['records']), 41)

    def issue(self):
        sample=self.store.capture('task','context',{},0)
        return self.store.decide(sample['serial'],dict(kind='command',tool='shell',command=['shell','fixed command']),0)

    def test_metadata_changes_do_not_discard_a_response(self):
        sample=self.store.capture('task','context',{},0)
        self.store.accept('new','new input')
        self.store.control(mode='other',router_revision='edited')
        ticket=self.store.decide(sample['serial'],dict(kind='command',tool='unknown'),0)
        self.assertNotIn(ticket,('stale','denied','ignored'))

    def test_duplicate_response_and_result_are_recorded_once(self):
        ticket=self.issue(); serial=self.store.projection()['actions'][0]['serial']
        self.assertEqual(self.store.decide(serial,dict(kind='command',tool='shell'),0),'ignored')
        self.assertTrue(self.store.report(ticket,'result'))
        self.assertFalse(self.store.report(ticket,'second result'))
        self.assertEqual(len(self.store.projection()['actions']),1)

    def test_restart_records_unknown_without_blocking_fresh_work(self):
        ticket=self.issue(); self.store=Ledger(self.path); self.store.restore()
        self.assertEqual(self.store.projection()['actions'][0]['status'],'uncertain')
        self.assertNotEqual(self.issue(),ticket)
        self.assertTrue(self.store.report(ticket,'late result'))

    def test_only_stop_blocks_capture(self):
        self.store.control(paused=True)
        self.assertIsNone(self.store.capture('task','context',{},0))
        self.store.control(paused=False)
        self.assertIsNotNone(self.store.capture('task','context',{},0))

    def test_completion_is_separate_from_tool_return(self):
        ticket=self.issue(); self.store.report(ticket,'result',domain='success')
        self.assertEqual(self.store.request_record('task')['status'],'active')
        receipt=self.store.protected('task')['results'][0]
        self.store.observe([ticket],{ticket:[receipt]})
        self.assertFalse(self.store.finish('task',ticket,{'operator':lambda *a:False}))
        self.assertTrue(self.store.finish('task',ticket,{'operator':lambda *a:True}))

    def test_failed_provider_has_no_host_cooldown(self):
        snap=self.store.capture('task','context',{},0)
        self.store.decide(snap['serial'],dict(kind='failure'),0)
        self.assertIsNotNone(self.store.capture('task','context',{},0))

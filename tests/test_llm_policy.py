"""Provider accounting preserves results without imposing host work quotas."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import llm_policy

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,METTACLAW_LLM_POLICY='managed',METTACLAW_LLM_LEDGER_PATH=str(Path(self.tmp.name)/'llm.sqlite3'))
        env.start();self.addCleanup(env.stop)
        self.hook=patch('strategy_host.provider_admitted',return_value=1)
        self.hook.start();self.addCleanup(self.hook.stop)

    def test_parallel_requests_are_accounted_without_an_invented_quota(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            tickets=list(pool.map(lambda n:llm_policy.admit('fixture',100,now=100),range(6)))
        self.assertEqual(len(set(tickets)),6)
        self.assertEqual(llm_policy.snapshot()['requests'],{'pending':6})

    def test_uncertain_old_request_does_not_block_a_new_request(self):
        with patch.object(llm_policy,'_owner','0:0'):
            old=llm_policy.admit('a',100,now=100,request_fingerprint='same')
        new=llm_policy.admit('a',100,now=101,request_fingerprint='same')
        self.assertNotEqual(old,new)
        self.assertEqual(llm_policy.snapshot()['requests'],{'pending':1,'unknown':1})

    def test_retry_after_is_honored_without_a_five_minute_floor(self):
        ticket=llm_policy.admit('a',10,now=100)
        llm_policy.fail(ticket,status=429,retry_after=2,now=100)
        with self.assertRaises(llm_policy.AdmissionDenied) as caught:
            llm_policy.admit('a',10,now=101)
        self.assertEqual(caught.exception.retry_after,1)
        llm_policy.admit('a',10,now=102)

    def test_internal_failure_has_no_provider_cooldown(self):
        for _ in range(5):
            ticket=llm_policy.admit('a',10,now=100)
            llm_policy.fail(ticket,sent=False,now=100)
        llm_policy.admit('a',10,now=100)

    def test_completion_is_idempotent(self):
        ticket=llm_policy.admit('a',10,now=100)
        result={'choices':[{'finish_reason':'stop'}],'usage':{'completion_tokens':3}}
        self.assertTrue(llm_policy.complete(ticket,result))
        self.assertFalse(llm_policy.complete(ticket,result))

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import context_budget

class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ, METTACLAW_STRATEGY_PATH=str(Path(self.tmp.name)/'ledger.sqlite3'))
        env.start();self.addCleanup(env.stop)

    def test_provider_calibration_is_specific_to_model(self):
        with patch.object(context_budget,'_model',return_value='one'):
            context_budget.observe('x'*1000, {'input_tokens':100})
            self.assertEqual(context_budget.tokens('x'*100),10)
        with patch.object(context_budget,'_model',return_value='two'):
            self.assertEqual(context_budget.tokens('x'*100),40)

    def test_cached_anthropic_tokens_are_counted_in_total(self):
        with patch.object(context_budget,'_model',return_value='one'):
            context_budget.observe('x'*1000, {'input_tokens':10,'cache_read_input_tokens':90})
            self.assertEqual(context_budget.tokens('x'*100),10)

    def test_agent_changes_target_but_actual_window_still_applies(self):
        context_budget.policy(1000)
        self.assertEqual(context_budget.available('',None),1000)
        self.assertEqual(context_budget.available('',700),572)

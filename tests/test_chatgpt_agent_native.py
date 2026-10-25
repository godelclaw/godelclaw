"""End-to-end native subscription fixtures; never contacts a paid provider.

Set SIWC_TEST_MODULE to CeTTa's tests/test_siwc.py and SIWC_DRIVER to its
synthetic-provider driver. CETTA_BIN must be an SIWC_TEST/Python build.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest

import test_native_llm_boundary as boundary


@unittest.skipUnless(all(os.environ.get(key) for key in ('CETTA_BIN', 'SIWC_DRIVER', 'SIWC_TEST_MODULE')),
                     'set native engine and synthetic SIWC fixture paths')
class ChatGPTAgentTests(unittest.TestCase):
    run_guest = boundary.NativeBoundaryTests.run_guest

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('siwc_fixture', os.environ['SIWC_TEST_MODULE'])
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        cls.fixture = module.NativeSiwcTests
        cls.fixture.setUpClass()

    @classmethod
    def tearDownClass(cls): cls.fixture.tearDownClass()

    def setUp(self):
        self.identity_provider = self.fixture(); self.identity_provider.setUp()
        boundary.NativeBoundaryTests.setUp(self)
        self.identity = self.identity_provider.login()
        self.env.update(CETTA_SIWC_STORE=str(self.identity_provider.store),
            CETTA_SIWC_TEST_ISSUER=self.identity_provider.issuer,
            METTACLAW_CHATGPT_POLICY_PATH=str(self.directory/'chatgpt.json'),
            SYNTHETIC_MODEL='chatgpt:fixture-model')
        self.env.pop('OPENAI_API_KEY', None)
        self.env.pop('SYNTHETIC_API_KEY', None)

    def tearDown(self):
        boundary.NativeBoundaryTests.tearDown(self)
        self.identity_provider.tearDown()

    def forms(self, body, select=True):
        return ('!(import! &self '+json.dumps(str(boundary.ROOT/'src/native_llm.metta'))+')\n'
                + ('!(chatgpt-policy "account" '+json.dumps(self.identity)+')\n' if select else '') + body)

    def guest(self, body, select=True):
        out = self.run_guest(self.forms(body, select))
        self.assertNotIn('(Error ', out)
        self.assertNotIn('fixture-access-', out+self.last_stderr)
        self.assertNotIn('fixture-refresh-', out+self.last_stderr)
        return out

    def test_real_native_alias_command_stream_and_usage_without_api_key(self):
        prompt = 'stable_cache_boundary_prefix_cache_boundary_api_cache_boundary_user _cache_boundary_ literal'
        out = self.guest('!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" '+json.dumps(prompt)+')))\n')
        self.assertIn('(RESULT (nop))', out)
        self.assertEqual(self.peer.requests, [])
        self.assertEqual(len(self.identity_provider.api_requests), 1)
        path, headers, body = self.identity_provider.api_requests[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(headers['Authorization'], 'Bearer fixture-access-1')
        self.assertEqual(body['model'], 'fixture-model')
        self.assertTrue(body['stream']); self.assertFalse(body['store'])
        self.assertNotIn('max_output_tokens', body); self.assertNotIn('stream_options', body)
        self.assertEqual(body['instructions'], 'stableprefixapi')
        self.assertEqual(body['input'], [{'role': 'user', 'content': 'user _cache_boundary_ literal'}])
        self.assertEqual(body['reasoning'], {'effort': 'high'})
        with sqlite3.connect(self.directory/'ledger.db') as db:
            state, canonical = db.execute('SELECT state,result FROM native_calls').fetchone()
            self.assertEqual(state, 'completed')
            raw = json.loads(canonical)['native']
            self.assertEqual(raw['output'][0]['encrypted_content'], 'opaque-fixture')
            self.assertEqual(raw['usage'], {'input_tokens': 10, 'output_tokens': 2})

    def test_commentary_route_keeps_stable_parts_out_of_instructions(self):
        # The live route caps `instructions` at 1,048,576 characters, and the
        # stable parts carry the transcript: a 1.1M-character part once made
        # every request fail. Each part stays a system input item, in order.
        self.env['METTACLAW_MODEL_COMMENTARY'] = '1'
        prompt = self.directory/'prompt.txt'
        stable = 'transcript ' * 100000
        prompt.write_text(stable + '_cache_boundary_policy_cache_boundary_frame_cache_boundary_user turn')
        out = self.guest('!(import! &self (library fs))\n'
                         '!(let $prompt (fs:read-text '+json.dumps(str(prompt))+')\n'
                         '   (println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" $prompt))))\n')
        self.assertIn('(RESULT ', out)
        self.assertEqual(len(self.identity_provider.api_requests), 1)
        body = self.identity_provider.api_requests[0][2]
        self.assertNotIn('instructions', body)
        self.assertEqual(body['input'], [{'role': 'system', 'content': stable},
                                         {'role': 'system', 'content': 'policy'},
                                         {'role': 'system', 'content': 'frame'},
                                         {'role': 'user', 'content': 'user turn'}])
        self.assertEqual(body['tool_choice'], 'auto')
        self.assertIn('prompt_cache_key', body)

    def test_catalogue_and_account_specific_models_preserve_server_order(self):
        out = self.guest('!(println! (settings "chatgpt"))\n!(println! (chatgpt-models))\n')
        self.assertIn('auth-deadline-ms', out); self.assertIn('response-bytes', out)
        self.assertIn('ChatGPTModels', out); self.assertNotIn('hidden-fixture', out)
        self.assertLess(out.index('fixture-model'), out.index('fixture-second'))
        self.assertEqual(len(self.identity_provider.api_requests), 1)

    def test_full_bootstrap_discovers_subscription_api_without_constructing_an_sdk_client(self):
        from test_command_dispatch_engine import CommandDispatchEngineTest
        env = CommandDispatchEngineTest()._cetta_env()
        env.update(self.env)
        env.pop('OPENAI_API_KEY', None)
        env['PYTHONPATH'] += os.pathsep + os.pathsep.join([
            str(boundary.ROOT/'channels'), str(boundary.ROOT/'repos/petta_lib_chromadb')])
        probe = self.directory/'bootstrap.metta'
        probe.write_text('!(println! (help "chatgpt-login"))\n!(println! (getSkills))\n'
                         '!(println! (check "(chatgpt-models)"))\n')
        p = subprocess.run([os.environ['CETTA_BIN'], '--lang', 'petta', '--import-mode', 'ancestor-walk',
                            '--quiet', str(boundary.ROOT/'cetta_bootstrap.metta'), str(probe)],
                           cwd=boundary.ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertNotIn('(Error ', p.stdout)
        self.assertIn('CommandHelp "chatgpt" "chatgpt-login"', p.stdout)
        self.assertIn('valid call, not executed', p.stdout)
        self.assertIn('chatgpt-policy', p.stdout)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_automatic_native_refresh_and_stable_cache_key(self):
        self.identity_provider.expire(self.identity)
        out = self.guest('!(println! (FIRST (llm:command "chatgpt:fixture-model" 42 "high" "stable_cache_boundary_one")))\n'
                         '!(println! (SECOND (llm:command "chatgpt:fixture-model" 42 "high" "stable_cache_boundary_two")))\n')
        self.assertIn('(FIRST (nop))', out); self.assertIn('(SECOND (nop))', out)
        self.assertEqual(self.identity_provider.refresh_count, 1)
        requests = self.identity_provider.api_requests
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][2]['prompt_cache_key'], requests[1][2]['prompt_cache_key'])
        self.assertRegex(requests[0][1]['session_id'],
                         r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
        self.assertEqual(requests[0][1]['session_id'], requests[1][1]['session_id'])
        self.assertEqual(requests[0][1]['Authorization'], 'Bearer fixture-access-2')

    def test_missing_account_never_falls_back_to_another_provider(self):
        out = self.guest('!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" "hello")))\n', select=False)
        self.assertIn('(RESULT ())', out)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_cache_session_survives_restart_and_prompt_changes_but_separates_agents_and_models(self):
        for prompt in ('first instructions_cache_boundary_one',
                       'revised instructions_cache_boundary_two'):
            self.guest('!(println! (llm:command "chatgpt:fixture-model" 42 "high" '
                       +json.dumps(prompt)+'))\n')
        requests = self.identity_provider.api_requests
        first = requests[0][1]['session_id']
        self.assertEqual(first, requests[1][1]['session_id'])
        self.assertNotEqual(requests[0][2]['prompt_cache_key'], requests[1][2]['prompt_cache_key'])
        self.env['SYNTHETIC_MODEL'] = 'chatgpt:fixture-second'
        self.guest('!(println! (llm:command "chatgpt:fixture-second" 42 "high" "hello"))\n')
        self.assertNotEqual(first, requests[2][1]['session_id'])
        self.env['SYNTHETIC_MODEL'] = 'chatgpt:fixture-model'
        self.env['METTACLAW_CHATGPT_POLICY_PATH'] = str(self.directory/'other-agent.json')
        self.guest('!(println! (llm:command "chatgpt:fixture-model" 42 "high" "hello"))\n')
        self.assertNotEqual(first, requests[3][1]['session_id'])

    def test_subscription_selection_with_native_transport_disabled_never_falls_back(self):
        self.env['METTACLAW_LLM_TRANSPORT'] = 'python'
        out = self.guest('!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" "hello")))\n', select=False)
        self.assertIn('(RESULT ())', out)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_swi_petta_reports_unsupported_subscription_without_legacy_provider_request(self):
        root = Path(os.environ.get('PETTA_ROOT', Path.home()/'repos/PeTTa'))
        if not shutil.which('swipl') or not (root/'src/main.pl').is_file():
            self.skipTest('set PETTA_ROOT and install SWI-PeTTa for compatibility qualification')
        probe = self.directory/'swi.metta'
        probe.write_text(self.forms('!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" "hello")))\n', select=False))
        env = {**self.env, 'METTACLAW_LLM_TRANSPORT':'python',
               'METTACLAW_ACTIVE_ENGINE':'petta', 'METTACLAW_ENGINE':'petta'}
        p = subprocess.run(['swipl', '-q', '-s', str(root/'src/main.pl'), '--', str(probe)],
                           cwd=boundary.ROOT, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertIn('(RESULT ())', p.stdout)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_structured_profile_is_explicitly_unsupported_without_a_provider_request(self):
        for transport in ('native', 'python'):
            with self.subTest(transport=transport):
                self.env['METTACLAW_LLM_TRANSPORT'] = transport
                out = self.run_guest(self.forms('!(println! (catch (llm:structured "{}")))\n', select=False))
                self.assertIn('chatgpt-command-mode-only', out)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_api_key_cooldown_does_not_block_separate_subscription_quota(self):
        fixture = self.directory/'cooldown_fixture.py'
        fixture.write_text('import llm_policy\n'
            'def create():\n'
            '    ticket = llm_policy.admit("openai", 42)\n'
            '    llm_policy.fail(ticket, status=429, retry_after=60, sent=False)\n'
            '    return True\n')
        out = self.guest('!(import! &self '+json.dumps(str(fixture))+')\n'
                         '!(py-call (cooldown_fixture.create))\n'
                         '!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" "hello")))\n')
        self.assertIn('(RESULT (nop))', out)
        self.assertEqual(len(self.identity_provider.api_requests), 1)
        with sqlite3.connect(self.directory/'ledger.db') as db:
            self.assertEqual(db.execute('SELECT provider FROM requests ORDER BY started').fetchall(),
                             [('openai',), ('chatgpt',)])

    def test_legacy_sdk_is_lazy_and_reused_when_explicitly_called(self):
        module = self.directory/'fixture_modules/openai.py'
        module.write_text('from types import SimpleNamespace\n'
            'created = 0\n'
            'def count_clients(): return created\n'
            'class OpenAI:\n'
            '    def __init__(self, *args, **kwargs):\n'
            '        global created\n'
            '        created += 1\n'
            '        self.responses = SimpleNamespace(create=lambda **kw: SimpleNamespace(output_text="fixture SDK answer"))\n'
            '        self.embeddings = SimpleNamespace(create=lambda **kw: SimpleNamespace(data=[SimpleNamespace(embedding=[1., 0.])]))\n')
        out = self.guest('!(import! &self (library lib_llm))\n'
                         '!(println! (BEFORE (py-call (openai.count_clients))))\n'
                         '!(println! (FIRST (useGPT "fixture-model" 8 "low" "hello")))\n'
                         '!(println! (SECOND (useGPT "fixture-model" 8 "low" "hello again")))\n'
                         '!(println! (EMBED (useGPTEmbedding "hello")))\n'
                         '!(println! (AFTER (py-call (openai.count_clients))))\n')
        self.assertIn('(BEFORE 0)', out)
        self.assertIn('fixture SDK answer', out)
        self.assertIn('(AFTER 1)', out)
        self.assertEqual(self.identity_provider.api_requests, []); self.assertEqual(self.peer.requests, [])

    def test_quota_failure_after_deltas_is_not_a_success_or_a_replayed_call(self):
        self.identity_provider.options['quota_failure'] = True
        out = self.guest('!(println! (RESULT (llm:command "chatgpt:fixture-model" 42 "high" "hello")))\n')
        self.assertIn('(RESULT ())', out); self.assertEqual(len(self.identity_provider.api_requests), 1)
        with sqlite3.connect(self.directory/'ledger.db') as db:
            self.assertEqual(db.execute('SELECT state FROM native_calls').fetchall(), [('failed',)])

    def test_reflected_bearer_never_enters_metta_or_diagnostics(self):
        self.identity_provider.options['reflect_token'] = True
        out = self.guest('!(println! (chatgpt-models))\n')
        self.assertIn('ChatGPTUnavailable', out)
        self.assertEqual(len(self.identity_provider.api_requests), 1)


if __name__ == '__main__': unittest.main()

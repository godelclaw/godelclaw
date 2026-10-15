"""Dynamic native API consumers: no agent memory or provider request is used."""
import json
import subprocess
import unittest

import test_semantic_memory_engine as fixture


class APITests(unittest.TestCase):
    setUp = fixture.MemoryTests.setUp
    stop_store = fixture.MemoryTests.stop_store
    run_metta = fixture.MemoryTests.run_metta

    def test_imported_command_drives_help_prompt_checks_and_module_fields(self):
        provider = self.home / 'component.metta'
        provider.write_text('''
!(bind! &api-calls (new-state 0))
(= (fixture-command $text) (change-state! &api-calls (+ 1 (get-state &api-calls))))
(CommandSpec 1 "fixture" "fixture-command" 1 1 "(fixture-command text)" "An imported command — Ω." "")
''')
        output = self.run_metta('''
!(println! (BEFORE (api:help "fixture-command")))
!(import! &self ''' + json.dumps(str(provider)) + ''')
!(println! (HELP (api:help "fixture-command")))
!(println! (PROMPT (api:prompt-lines)))
!(println! (VALID (api:check (quote (fixture-command "text")))))
!(println! (BAD (api:check (quote (fixture-command)))))
!(println! (FIELDS (api:module-fields "fixture")))
!(println! (NOT_RUN (get-state &api-calls)))
''')
        self.assertIn('(BEFORE (APINotRegistered "fixture-command"))', output)
        self.assertIn('(HELP (CommandHelp "fixture" "fixture-command"', output)
        self.assertIn('An imported command — Ω.', output)
        self.assertIn('valid call, not executed', output)
        self.assertIn('COMMAND_ERROR: wrong number of arguments. usage: (fixture-command text)', output)
        self.assertIn('(CommandField "fixture" "fixture-command" 1 1', output)
        self.assertIn('(NOT_RUN 0)', output)

    def test_attention_metadata_is_complete_and_inspection_is_passive(self):
        self.run_metta('!(attention-use "a")')
        snapshot = self.home / 'attention.metta'
        before = snapshot.read_bytes()
        output = self.run_metta('''
!(println! (COMMANDS (size-atom (api:command-fields "attention" (api:commands)))))
!(println! (FIELDS (size-atom (api:module-fields "attention"))))
!(println! (DERIVED (== (size-atom (api:module-fields "attention"))
                       (+ 6 (size-atom (settings:specs "attention"))))))
!(println! (HELP (api:help "attention-policy")))
!(println! (VALID (api:check (quote (attention-link "a" "b" 0)))))
!(println! (BAD (api:argument-error (quote (attention-last 1)))))
!(println! (LATER (api:check (quote (attention-last)))))
''')
        self.assertIn('(COMMANDS 6)', output)
        self.assertIn('(DERIVED true)', output)
        self.assertIn('(Default 300) (Unit "seconds")', output)
        self.assertIn('COMMAND_ERROR: wrong number of arguments. usage: (attention-last)', output)
        self.assertIn('(LATER "OK: (attention-last)', output)
        self.assertEqual(snapshot.read_bytes(), before)

    def test_full_bootstrap_help_check_and_dispatch_share_the_metadata(self):
        from test_command_dispatch_engine import CommandDispatchEngineTest
        helper = CommandDispatchEngineTest()
        probe = self.home / 'api-bootstrap.metta'
        probe.write_text('''
!(println! (HELP (help "attention-use")))
!(println! (INDEX (help)))
!(println! (PROMPT (getSkills)))
!(println! (CHECK (check "(attention-link \\\"a\\\" \\\"b\\\" 0)")))
!(println! (BAD_CHECK (check "(attention-last 1)")))
!(println! (RUN (run-command-batch-once 910001 0
  (quote ((attention-last 1) (attention-last))))))
''')
        env = helper._cetta_env()
        env.update({k: v for k, v in self.env.items() if k.startswith('METTACLAW_')})
        env['METTACLAW_ENGINE_STATE_PATH'] = str(self.home / 'engine')
        env['METTACLAW_EFFECT_RECEIPT_PATH'] = str(self.home / 'receipts.jsonl')
        env['METTACLAW_STIMULUS_FRONTIER_PATH'] = str(self.home / 'frontier')
        (self.home / 'frontier').write_text('1 910001 0 0\n')
        result = subprocess.run([self.engine, '--lang', 'petta', '--import-mode', 'ancestor-walk',
                                 str(fixture.ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                cwd=fixture.ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('(CommandHelp "attention" "attention-use"', result.stdout)
        self.assertIn('valid call, not executed', result.stdout)
        self.assertIn('COMMAND_ERROR: wrong number of arguments. usage: (attention-last)', result.stdout)
        self.assertIn('AttentionQuery none', result.stdout)
        self.assertEqual(result.stdout.count('COMMAND_RETURN:'), 2)


if __name__ == '__main__':
    unittest.main()

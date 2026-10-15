"""Native provider registration and live API discovery on synthetic stores."""
import unittest
import test_semantic_memory_engine as fixture


class SettingsTests(unittest.TestCase):
    setUp = fixture.MemoryTests.setUp
    stop_store = fixture.MemoryTests.stop_store
    run_metta = fixture.MemoryTests.run_metta

    def test_descriptions_values_numeric_strings_and_bank_controls(self):
        output = self.run_metta('''
!(println! (SCHEMA (settings "attention")))
!(println! (SET (setting "attention" "sti-half-life" "900")))
!(println! (GET (setting "attention" "sti-half-life")))
!(println! (SET_MODE (setting "attention" "mode" "similarity")))
!(println! (ZERO_RESTART (setting "attention" "restart" "0")))
!(println! (UNKNOWN (setting "attention" "sti-halflife" 5)))
!(println! (LATER (semantic:query "alpha" 1)))
''')
        self.assertIn('(Default 300) (Unit "seconds") (Domain Nonnegative)', output)
        self.assertIn('(Current 900)', output)
        self.assertIn('(AttentionConfigured "restart" 0 true)', output)
        self.assertIn('(SettingUnknown "attention" "sti-halflife")', output)
        self.assertIn('(LATER (MemoryResults ((MemoryHit "a"', output)
        restored = self.run_metta('!(println! (setting "attention" "sti-half-life"))')
        self.assertIn('(Current 900)', restored)

    def test_importing_a_provider_updates_index_without_an_api_table_edit(self):
        provider = self.home / 'new-provider.metta'
        provider.write_text('''
!(bind! &fixture-gain (new-state 2))
(= (fixture:read $key) (get-state &fixture-gain))
(= (fixture:write $key $value) (change-state! &fixture-gain $value))
(= (fixture:register)
 (settings:register (SettingSpec 1 "fixture" "gain" 2 "multiplier" Nonnegative "session" "A newly imported provider." fixture:read fixture:write)))
(SettingSpec 1 "fixture" "gain" 2 "multiplier" Nonnegative "session" "A newly imported provider." fixture:read fixture:write)
''')
        output = self.run_metta('''
!(println! (BEFORE (settings:context)))
!(import! &self "''' + str(provider) + '''")
!(println! (AFTER (settings:context)))
!(println! (VALUE (setting "fixture" "gain")))
!(println! (REVISION_BEFORE (settings:revision)))
!(fixture:register)
!(println! (REVISION_AFTER (settings:revision)))
!(setting "fixture" "gain" "7")
!(println! (UPDATED (setting "fixture" "gain")))
!(println! (BAD (setting "fixture" "gain" -1)))
!(println! (STILL (setting "fixture" "gain")))
''')
        self.assertNotIn('SettingsGroup "fixture"', output.split('(AFTER ', 1)[0])
        self.assertIn('(SettingsGroup "fixture" ("gain"))', output)
        self.assertIn('(Current 2)', output)
        self.assertIn('(Current 7)', output)
        before = output.split('(REVISION_BEFORE ', 1)[1].split(')', 1)[0]
        after = output.split('(REVISION_AFTER ', 1)[1].split(')', 1)[0]
        self.assertEqual(before, after)
        self.assertIn('(SettingError "fixture" "gain"', output)

    def test_new_attention_key_works_and_survives_absent_provider(self):
        output = self.run_metta('''
!(settings:register (SettingSpec 1 "attention" "future-gain" 2 "multiplier" Nonnegative "restart" "New provider field." attention-policy attention-policy))
!(println! (DEFAULT (attention-policy "future-gain")))
!(println! (SET (setting "attention" "future-gain" 7)))
!(attention-reload)
!(println! (RELOADED (attention-policy "future-gain")))
''')
        self.assertIn('(DEFAULT 2)', output)
        self.assertIn('(RELOADED 7)', output)
        # A temporarily missing provider must not freeze otherwise valid attention.
        restored = self.run_metta('!(println! (semantic:query "alpha" 1))\n!(println! (attention-policy))')
        self.assertIn('(MemoryHit "a"', restored)
        self.assertNotIn('AttentionUnavailable', restored)
        self.assertIn('(AttentionSetting "future-gain" 7)', restored)

    def test_seed_blend_endpoints_and_zero_rounds(self):
        output = self.run_metta('''
!(attention-policy "exposure" 0)
!(attention-policy "spread-rounds" 0)
!(attention-use "b" 100)
!(setting "attention" "seed-similarity-weight" 1)
!(println! (SIM_SEED (semantic:query "alpha" 1 "attention")))
!(setting "attention" "seed-similarity-weight" 0)
!(println! (ATTN_SEED (semantic:query "alpha" 1 "attention")))
!(forget "b")
!(forget "a")
!(println! (EMPTY (semantic:query "alpha" 1 "attention")))
''')
        self.assertIn('(SIM_SEED (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(ATTN_SEED (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(EMPTY (MemoryResults ()', output)

    def test_inspection_does_not_stimulate_or_rewrite_the_snapshot(self):
        self.run_metta('!(attention-use "a")')
        snapshot = self.home / 'attention.metta'
        before = snapshot.read_bytes()
        output = self.run_metta('!(println! (settings))\n!(println! (settings:context))')
        self.assertIn('(Setting "attention" "seed-similarity-weight"', output)
        self.assertEqual(snapshot.read_bytes(), before)

    def test_failed_provider_is_visible_without_losing_other_controls(self):
        output = self.run_metta('''
(= (fixture:failed-provider) (Error fixture offline))
(SettingsProvider "unavailable" fixture:failed-provider)
!(println! (settings "attention"))
!(println! (settings:context))
!(println! (LATER (semantic:query "alpha" 1)))
''')
        self.assertIn('(Setting "attention" "mode"', output)
        self.assertIn('(SettingProviderUnavailable fixture:failed-provider offline)', output)
        self.assertIn('(LATER (MemoryResults ((MemoryHit "a"', output)

    def test_context_provider_and_prompt_index_through_full_bootstrap(self):
        import json
        import subprocess
        from test_command_dispatch_engine import CommandDispatchEngineTest
        helper = CommandDispatchEngineTest()
        self.env['METTACLAW_ENGINE_STATE_PATH'] = str(self.home / 'engine')
        probe = self.home / 'context-catalogue.metta'
        probe.write_text('''
!(println! (INDEX (settings:context)))
!(println! (SETTING_CONTEXT (setting "context" "task_tokens" "24000")))
!(println! (CURRENT (setting "context" "task_tokens")))
!(println! (HELP (help "settings")))
''')
        env = helper._cetta_env()
        env.update({k: v for k, v in self.env.items() if k.startswith('METTACLAW_')})
        result = subprocess.run([self.engine, '--lang', 'petta', '--import-mode', 'ancestor-walk',
                                 str(fixture.ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                cwd=fixture.ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('(SettingsGroup "context" ("task_tokens"))', result.stdout)
        self.assertIn('(PrologAvailable ', result.stdout)
        self.assertIn('(Current 24000)', result.stdout)
        self.assertIn('(ProviderErrors ())', result.stdout)
        self.assertEqual(json.loads((self.home / 'context-policy.json').read_text())['task_tokens'], 24000)

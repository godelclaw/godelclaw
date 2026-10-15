"""Synthetic native-memory integration; never opens an agent's private store."""
import unittest
import test_semantic_memory_engine as fixture


class AttentionTests(unittest.TestCase):
    setUp = fixture.MemoryTests.setUp
    stop_store = fixture.MemoryTests.stop_store
    run_metta = fixture.MemoryTests.run_metta

    def test_modes_persist_and_distance_is_not_relabelled_as_attention(self):
        output = self.run_metta('''
!(attention-policy "exposure" 0)
!(attention-policy "sti-half-life" 0)
!(attention-policy "mti-half-life" 0)
!(attention-policy "lti-half-life" 0)
!(println! (USE (attention-use "b" 100)))
!(println! (SIM (semantic:query "alpha" 1 "similarity")))
!(println! (ATTN (semantic:query "alpha" 1 "attention")))
!(println! (MIX (semantic:query "alpha" 1 "hybrid")))
!(println! (DETAIL (attention-last)))
!(println! (SET (attention-policy "mode" "attention")))
''')
        self.assertIn('(SIM (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(ATTN (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(MIX (MemoryResults ((MemoryHit "a"', output)
        self.assertRegex(output, r'\(ATTN .*MemoryHit "b" [^ ]+ \("original"\) 1(?:\.0)? "beta reference"')
        self.assertIn('(Persistence true)', output)
        restored = self.run_metta('!(println! (RESTORED (semantic:query "alpha" 1)))\n!(println! (attention-policy))')
        self.assertIn('(RESTORED (MemoryResults ((MemoryHit "b"', restored)
        self.assertIn('(AttentionSetting "mode" "attention")', restored)

    def test_weighted_spread_discovers_beyond_both_initial_pools(self):
        self.collection.add(['c', 'd'], [[0., 0., 1.], [-1., 0., 0.]],
                            ['linked bridge', 'linked destination'], [{'time': 'fixture'}, {'time': 'fixture'}])
        output = self.run_metta('''
!(attention-policy "exposure" 0)
!(attention-policy "candidate-multiplier" 1)
!(attention-policy "restart" 0.1)
!(attention-policy "spread-rounds" 3)
!(attention-use "a" 10)
!(attention-link "a" "c" 1)
!(attention-link "a" "c" 1)
!(attention-link "c" "d" 1)
!(println! (POLICY (attention-policy)))
!(println! (SIM (semantic:query "alpha" 1 "similarity")))
!(println! (SPREAD (semantic:query "alpha" 1 "attention")))
!(println! (EXPLAIN (attention-last)))
!(println! (FORGET (forget "d")))
!(println! (AFTER (semantic:query "alpha" 0 "attention")))
!(println! (POLICY_AFTER (attention-policy)))
''')
        self.assertIn('(Links 2)', output)
        self.assertIn('(SIM (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(SPREAD (MemoryResults ((MemoryHit "d"', output)
        after = output.split('(AFTER ', 1)[1]
        self.assertNotIn('(MemoryHit "d"', after)
        self.assertIn('(Links 1)', after)

    def test_exposure_and_explicit_read_are_distinct_and_optional(self):
        output = self.run_metta('''
!(attention-policy "sti-half-life" 0)
!(attention-policy "mti-half-life" 0)
!(attention-policy "lti-half-life" 0)
!(attention-policy "exposure" 0.25)
!(semantic:query "alpha" 1 "similarity")
!(println! (EXPOSED (attention-focus 0)))
!(memory-read "a")
!(println! (READ (attention-focus 0)))
!(attention-policy "exposure" 0)
!(attention-policy "read-stimulus" 0)
!(semantic:query "alpha" 1)
!(memory-read "a")
!(println! (DISABLED (attention-focus 0)))
''')
        self.assertIn('(ECANValue "a" 0.25 0 0)', output)
        self.assertIn('(ECANValue "a" 1.25 0.0625 0.00125)', output)
        # Zero read stimulus must not consolidate the value again.
        read = output.split('(READ ', 1)[1].split('\n', 1)[0]
        disabled = output.split('(DISABLED ', 1)[1].split('\n', 1)[0]
        self.assertEqual(read, disabled)

    def test_bad_arguments_do_not_mutate_policy_or_block_later_queries(self):
        output = self.run_metta('''
!(println! (attention-policy "sti-halflife" 5))
!(println! (attention-policy "sti-half-life" -5))
!(println! (attention-policy "restart" 0))
!(println! (attention-policy "spread-rounds" 1.5))
!(println! (attention-use "b" -1))
!(println! (attention-link "a" "b" -1))
!(println! (attention-policy))
!(println! (STILL_WORKS (semantic:query "alpha" 1)))
''')
        self.assertIn('(AttentionSetting "restart" 0.5)', output)
        self.assertIn('(ECANSetting sti-half-life 300)', output)
        self.assertIn('(STILL_WORKS (MemoryResults ((MemoryHit "a"', output)

    def test_corrupt_snapshot_preserved_with_similarity_fallback(self):
        snapshot = self.home / 'attention.metta'
        snapshot.write_text('(BrokenSnapshot "keep me")')
        output = self.run_metta('!(println! (semantic:query "alpha" 1))\n!(println! (attention-policy))\n')
        self.assertIn('(MemoryHit "a"', output)
        self.assertIn('AttentionUnavailable snapshot', output)
        self.assertEqual(snapshot.read_text(), '(BrokenSnapshot "keep me")')

    def test_health_does_not_train_attention(self):
        output = self.run_metta('!(println! (semantic:health))')
        self.assertIn('(MemoryHealth healthy native', output)
        self.assertFalse((self.home / 'attention.metta').exists())

    def test_co_use_learns_once_per_turn_and_search_does_not_teach_links(self):
        output = self.run_metta('''
!(semantic:query "alpha" 2)
!(println! (SEARCH_ONLY (attention-policy)))
!(am:begin-turn)
!(attention-use "a")
!(attention-use "b")
!(attention-use "a")
!(println! (FIRST (am:links (am:bank))))
!(am:begin-turn)
!(attention-use "a")
!(attention-use "b")
!(println! (SECOND (am:links (am:bank))))
!(attention-policy "co-use-weight" 0)
!(am:begin-turn)
!(attention-use "a")
!(attention-use "b")
!(println! (DISABLED (am:links (am:bank))))
!(forget "a")
!(attention-use "b")
!(println! (FORGOTTEN (am:links (am:bank))))
''')
        self.assertIn('(Links 0)', output)
        self.assertIn('(AttentionLink "a" "b" 1)', output)
        self.assertIn('(AttentionLink "b" "a" 1)', output)
        second = output.split('(SECOND ', 1)[1].split('\n', 1)[0]
        disabled = output.split('(DISABLED ', 1)[1].split('\n', 1)[0]
        self.assertEqual(second, disabled)
        self.assertIn('(AttentionLink "a" "b" 2)', second)
        self.assertIn('(FORGOTTEN ())', output)
        restored = self.run_metta('!(println! (attention-policy))\n!(println! (am:links (am:bank)))')
        self.assertIn('(Links 0)', restored)

    def test_transport_matches_fraction_oracle_and_does_not_change_the_bank(self):
        from fractions import Fraction as F
        import random
        import re
        rng = random.Random(84)
        self.collection.add(['c', 'd'], [[0., 0., 1.], [-1., 0., 0.]],
                            ['bridge', 'destination'], [{}, {}])
        commands = ['!(attention-policy "exposure" 0)',
                    '!(semantic:query "alpha" 1 "similarity")']
        expected = []
        for trial in range(8):
            edges = [[rng.randrange(4) for _ in range(4)] for _ in range(4)]
            for i, src in enumerate('abcd'):
                for j, dst in enumerate('abcd'):
                    commands.append(f'!(attention-link "{src}" "{dst}" {edges[i][j]})')
            x = [F(1), F(0), F(0), F(0)]
            for _ in range(5):
                y = [F(1, 4), F(0), F(0), F(0)]
                for i in range(4):
                    total = sum(edges[i])
                    if total:
                        for j in range(4):
                            y[j] += F(3, 4) * x[i] * F(edges[i][j], total)
                    else:
                        y[i] += F(3, 4) * x[i]
                x = y
            expected.append(x)
            commands.append(f'!(println! (ORACLE {trial} (am:rounds (am:bank) '
                            '((AttentionMass "a" 1)) ((AttentionMass "a" 1)) 5 0.25)))')
        commands.append('!(println! (TOTAL (ecan:total (am:bank))))')
        output = self.run_metta('\n'.join(commands))
        for trial, values in enumerate(expected):
            line = output.split(f'(ORACLE {trial} ', 1)[1].split('\n', 1)[0]
            actual = dict((k, float(v)) for k, v in
                          re.findall(r'\(AttentionMass "(.)" ([^ )]+)\)', line))
            for ident, value in zip('abcd', values):
                self.assertAlmostEqual(actual.get(ident, 0), float(value), places=12)
            self.assertAlmostEqual(sum(actual.values()), 1, places=12)
        self.assertIn('(TOTAL (ECANTotal 0 0 0))', output)

    def test_valid_shape_with_invalid_saved_setting_falls_back_without_overwrite(self):
        self.run_metta('!(attention-policy "mode" "attention")')
        snapshot = self.home / 'attention.metta'
        damaged = snapshot.read_text().replace('(AttentionSetting "restart" 0.5)',
                                               '(AttentionSetting "restart" -1)')
        snapshot.write_text(damaged)
        output = self.run_metta('!(println! (semantic:query "alpha" 1))\n!(println! (attention-policy))')
        self.assertIn('(MemoryHit "a"', output)
        self.assertIn('(AttentionUnavailable snapshot invalid-setting)', output)
        self.assertEqual(snapshot.read_text(), damaged)

    def test_new_tools_through_public_bootstrap_and_prompt_projection(self):
        import subprocess
        from test_command_dispatch_engine import CommandDispatchEngineTest
        helper = CommandDispatchEngineTest()
        probe = self.home / 'public-attention.metta'
        probe.write_text('''
!(println! (PUBLIC_POLICY (attention-policy)))
!(println! (PUBLIC_SET (attention-policy "mode" "attention")))
!(println! (PUBLIC_USE (attention-use "b" 100)))
!(println! (PUBLIC_QUERY (query "alpha" 1 "attention")))
!(println! (PUBLIC_CONTEXT (am:context)))
!(println! (PUBLIC_HELP (help "attention-policy")))
''')
        env = helper._cetta_env()
        env.update({key: value for key, value in self.env.items()
                    if key.startswith('METTACLAW_')})
        result = subprocess.run([self.engine, '--lang', 'petta', '--import-mode', 'ancestor-walk',
                                 str(fixture.ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                cwd=fixture.ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('(PUBLIC_QUERY (MemoryResults ((MemoryHit "b"', result.stdout)
        self.assertIn('(AttentionMemory (Mode "attention")', result.stdout)
        self.assertIn('co-use-weight', result.stdout)
        self.assertNotIn('(Error ', result.stdout)

    def test_default_snapshot_is_beside_engine_state_file(self):
        engine_state = self.home / 'engine'
        engine_state.write_text('cetta\n')
        self.env['METTACLAW_ENGINE_STATE_PATH'] = str(engine_state)
        output = self.run_metta('!(println! (attention-use "a"))\n')
        self.assertIn('(AttentionUsed', output)
        self.assertNotIn('AttentionUnavailable', output)
        self.assertTrue((self.home / 'attention.metta').is_file())
        self.assertEqual(engine_state.read_text(), 'cetta\n')
        restored = self.run_metta('!(println! (attention-focus 0))')
        self.assertIn('(ECANValue "a"', restored)

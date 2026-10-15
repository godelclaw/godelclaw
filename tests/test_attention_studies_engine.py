"""Optional attention experiments on synthetic memories; Python owns fixtures only."""
import json
import re
import unittest

import test_semantic_memory_engine as fixture


QUIET = '''
!(attention-policy "exposure" 0)
!(attention-policy "bootstrap-neighbours" 0)
!(attention-policy "spread-rounds" 0)
!(attention-policy "seed-similarity-weight" 1)
'''


class StudyTests(unittest.TestCase):
    setUp = fixture.MemoryTests.setUp
    stop_store = fixture.MemoryTests.stop_store
    run_metta = fixture.MemoryTests.run_metta

    def test_catalogue_discovery_and_choice_settings_survive_restart(self):
        catalogue = json.loads((fixture.ROOT / 'src/attention_studies.json').read_text())
        self.assertEqual(len(catalogue['threads']), 10)
        self.assertEqual(len({t['id'] for t in catalogue['threads']}), 10)
        self.assertTrue(all(t['tests'] and t['limit'] and t['stage'] for t in catalogue['threads']))
        output = self.run_metta('''
!(println! (CATALOGUE (attention-studies)))
!(println! (HELP (api:help "attention-try")))
!(println! (SET (attention-policy "study-arm" "power-law")))
!(println! (NUMERIC (attention-policy "study-mix" "0.4")))
!(println! (BAD (attention-policy "study-arm" "typo")))
!(println! (CURRENT (setting "attention" "study-arm")))
''')
        self.assertIn('semantic-walk', output)
        self.assertIn('offline-reference', output)
        self.assertIn('(CommandHelp "attention-study" "attention-try"', output)
        self.assertIn('(AttentionConfigured "study-arm" "power-law" true)', output)
        self.assertIn('(AttentionConfigured "study-mix" 0.4 true)', output)
        self.assertIn('(SettingError "attention" "study-arm"', output)
        restored = self.run_metta('!(println! (RESTORED (attention-policy "study-arm")))')
        self.assertIn('(RESTORED "power-law")', restored)

    def test_reference_operators_match_independent_arithmetic(self):
        output = self.run_metta('''
!(import! &self ./experiments/attention/operators.metta)
!(println! (POWER (ax:power 2 60 60 1)))
!(println! (FREQUENCY (ax:power 2 900 0 1)))
!(println! (GAIN (ax:gain 3 1 1)))
!(println! (COLD (ax:gain 0 0 0)))
!(println! (IOR (ax:inhibition 1 60 0.8 60)))
!(println! (DISABLED (ax:inhibition 1 0 0.8 0)))
!(println! (ACTR (study:actr-sum 100 (0 75 99) 0.5 1)))
!(println! (HEBB (study:bounded-hebb 0.25 1 1 0.2)))
!(println! (SOFTMAX (study:softmax ((1000 "a") (1001 "b")) 1)))
''')
        for marker, expected in [('POWER', .5), ('FREQUENCY', 2/3), ('GAIN', 2/3),
                                 ('COLD', .5), ('IOR', .6), ('DISABLED', 1),
                                 ('ACTR', 1.3), ('HEBB', .4)]:
            actual = float(re.search(r'\(' + marker + r' ([^)]+)\)', output).group(1))
            self.assertAlmostEqual(actual, expected, places=5)
        scores = dict((i, float(s)) for s, i in re.findall(r'\(([^ ()]+) "([ab])"\)', output))
        self.assertAlmostEqual(scores['a'] + scores['b'], 1, places=5)
        self.assertAlmostEqual(scores['b'], .7310585786, places=5)

    def test_finite_diffusion_matches_poisson_oracle_and_focus_can_lock_in(self):
        import math
        output = self.run_metta(QUIET + '''
!(import! &self ./experiments/attention/operators.metta)
!(semantic:query "alpha" 0 "similarity")
!(attention-link "a" "b" 1)
!(println! (HEAT (study:heat (am:bank) ((AttentionMass "a" 1)) 1 2)))
!(println! (STICKY (list:retain-top-k-keyed
   (study:focus ((0.8 "a") (0.3 "b")) ("b") 0.6) 1)))
!(println! (RESET (list:retain-top-k-keyed
   (study:focus ((0.8 "a") (0.3 "b")) () 0.6) 1)))
!(println! (BROAD (study:softmax ((0 "a") (1 "b")) 10)))
!(println! (NARROW (study:softmax ((0 "a") (1 "b")) 0.1)))
''')
        heat = output.split('(HEAT ', 1)[1].split('\n', 1)[0]
        values = dict((i, float(v)) for i, v in re.findall(r'\(AttentionMass "([ab])" ([^)]+)\)', heat))
        self.assertAlmostEqual(values['a'], math.exp(-1), places=5)
        self.assertAlmostEqual(values['b'], 1.5 * math.exp(-1), places=5)
        missing = float(re.search(r'\(MissingMass ([^)]+)\)', heat).group(1))
        self.assertAlmostEqual(missing + sum(values.values()), 1, places=5)
        self.assertIn('"b"', output.split('(STICKY ', 1)[1].split('\n', 1)[0])
        self.assertIn('"a"', output.split('(RESET ', 1)[1].split('\n', 1)[0])
        def distribution(marker):
            return dict((i, float(v)) for v, i in re.findall(r'\(([^ ()]+) "([ab])"\)',
                        output.split('(' + marker + ' ', 1)[1].split('\n', 1)[0]))
        broad, narrow = distribution('BROAD'), distribution('NARROW')
        self.assertAlmostEqual(sum(broad.values()), 1, places=5)
        self.assertAlmostEqual(sum(narrow.values()), 1, places=5)
        self.assertGreater(narrow['b'], broad['b'])

    def test_power_arm_is_optional_and_explicit_similarity_keeps_its_order(self):
        output = self.run_metta(QUIET + '''
!(attention-use "b" 1)
!(attention-policy "study-mix" 1)
!(println! (BASE (semantic:query "alpha" 1 "hybrid")))
!(println! (POWER (attention-try "power-law" "alpha" 1)))
!(println! (WHY (attention-last)))
!(println! (UNCHANGED (attention-policy "study-arm")))
!(attention-policy "study-arm" "power-law")
!(println! (SIMILARITY (semantic:query "alpha" 1 "similarity")))
!(attention-policy "study-mix" 0)
!(println! (ZERO_MIX (semantic:query "alpha" 1 "hybrid")))
!(println! (BAD_TRY (attention-try "typo" "alpha")))
''')
        for marker, identifier in [('BASE', 'a'), ('POWER', 'b'), ('SIMILARITY', 'a'), ('ZERO_MIX', 'a')]:
            self.assertIn('(' + marker + ' (MemoryResults ((MemoryHit "' + identifier + '"', output)
        why = output.split('(WHY ', 1)[1].split('\n', 1)[0]
        self.assertIn('ExperimentalUncertified', why)
        self.assertIn('(AttentionStudy "power-law"', why)
        self.assertIn('(UNCHANGED "baseline")', output)
        self.assertIn('(StudyError selector', output)

    def test_experimental_walk_discovers_beyond_initial_similarity_pool(self):
        self.collection.add(['c', 'd'], [[0., 0., 1.], [-1., 0., 0.]],
                            ['synthetic bridge', 'synthetic destination'], [{}, {}])
        output = self.run_metta(QUIET + '''
!(attention-policy "candidate-multiplier" 1)
!(attention-policy "spread-rounds" 3)
!(attention-policy "restart" 0.1)
!(attention-policy "study-mix" 1)
!(attention-link "a" "c" 1)
!(attention-link "c" "d" 1)
!(attention-feedback "d" 1 100)
!(println! (REACHED (attention-try "gain-need" "alpha" 1)))
!(println! (EXPLAIN (attention-last)))
''')
        self.assertIn('(REACHED (MemoryResults ((MemoryHit "d"', output)
        self.assertIn('(Spread (Rounds 3) ExperimentalUncertified)', output)

    def test_bounded_updates_and_history_proxy_limits(self):
        import random
        rng = random.Random(134)
        rows = [(rng.random(), rng.random(), rng.random(), rng.random()) for _ in range(40)]
        forms = ['!(import! &self ./experiments/attention/operators.metta)']
        for index, (old, x, y, rate) in enumerate(rows):
            forms.append(f'!(println! (BOUNDED {index} (study:bounded-hebb {old} {x} {y} {rate})))')
        forms += ['!(println! (OLD_HISTORY (study:actr-sum 100 (0 99) 0.5 1)))',
                  '!(println! (FRESH_HISTORY (study:actr-sum 100 (98 99) 0.5 1)))',
                  '!(println! (SAME_PROXY (ax:power 2 1 60 0.5)))']
        output = self.run_metta('\n'.join(forms))
        values = {int(i): float(v) for i, v in re.findall(r'\(BOUNDED (\d+) ([^)]+)\)', output)}
        self.assertEqual(len(values), len(rows))
        for index, (old, x, y, rate) in enumerate(rows):
            self.assertGreaterEqual(values[index], 0)
            self.assertLessEqual(values[index], 1)
            self.assertAlmostEqual(values[index], (1-rate)*old+rate*x*y, places=5)
        # Two histories have the same count and latest use but different ACT-R sums.
        # The constant-space runtime proxy cannot distinguish these histories.
        older = float(re.search(r'\(OLD_HISTORY ([^)]+)\)', output).group(1))
        fresh = float(re.search(r'\(FRESH_HISTORY ([^)]+)\)', output).group(1))
        self.assertGreater(fresh, older)

    def test_feedback_changes_gain_proxy_and_survives_restart_without_changing_evidence(self):
        original = self.collection.get(include=['documents', 'embeddings', 'metadatas'])
        output = self.run_metta(QUIET + '''
!(attention-policy "study-mix" 1)
!(println! (BAD (attention-feedback "a" 2)))
!(println! (FEEDBACK_A (attention-feedback "a" 0 100)))
!(println! (FEEDBACK_B (attention-feedback "b" 1 100)))
!(println! (GAIN (attention-try "gain-need" "alpha" 1)))
!(println! (ORDINARY (attention-try "baseline" "alpha" 1)))
!(println! (TRACE (ax:trace (am:bank) "b")))
''')
        self.assertIn('(StudyError feedback', output)
        self.assertIn('(FEEDBACK_B (StudyFeedback "b"', output)
        self.assertIn('(GAIN (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(ORDINARY (MemoryResults ((MemoryHit "a"', output)
        actual = self.collection.get(include=['documents', 'embeddings', 'metadatas'])
        self.assertEqual(actual, original)
        restored = self.run_metta('''
!(println! (RESTORED (attention-try "gain-need" "alpha" 1)))
!(println! (TRACE (ax:trace (am:bank) "b")))
!(forget "b")
!(println! (AFTER (ax:state (am:bank))))
!(println! (MISSING (attention-feedback "b" 1)))
''')
        self.assertIn('(RESTORED (MemoryResults ((MemoryHit "b"', restored)
        self.assertNotIn('"b"', restored.split('(AFTER ', 1)[1].split('\n', 1)[0])
        self.assertIn('(StudyError memory', restored)

    def test_inhibition_is_temporary_and_does_not_block_repeated_evidence(self):
        output = self.run_metta(QUIET + '''
!(attention-policy "study-mix" 1)
!(attention-policy "inhibition-strength" 1)
!(println! (FIRST (attention-try "baseline" "alpha" 1)))
!(println! (ALTERNATIVE (attention-try "inhibition" "alpha" 1)))
!(println! (DIRECT (memory-read "a")))
!(println! (SIM (semantic:query "alpha" 1 "similarity")))
!(let* (($bank (am:bank))
        ((StudyTrace $id $n $first $last $shown $latest $pos $neg) (ax:trace $bank "a"))
        ($aged (- (system:wall-time) 1200))
        ($row (StudyTrace $id $n $first $last $shown $aged $pos $neg)))
   (ax:replace $bank $row))
!(println! (RETURN (attention-try "inhibition" "alpha" 1)))
!(attention-policy "inhibition-strength" 0)
!(println! (DISABLE (attention-try "inhibition" "alpha" 1)))
''')
        self.assertIn('(FIRST (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(ALTERNATIVE (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(DIRECT (MemoryCurrent "a"', output)
        self.assertIn('(SIM (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(RETURN (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(DISABLE (MemoryResults ((MemoryHit "a"', output)

    def test_same_pool_comparison_does_not_query_train_or_save(self):
        self.run_metta(QUIET + '''
!(attention-use "b")
!(attention-feedback "a" 0 10)
!(attention-feedback "b" 1 10)
''')
        output = self.run_metta('''
!(semantic:query "alpha" 1 "hybrid")
!(println! (BEFORE (fs:read-text (am:path))))
!(println! (COMPARE (attention-compare 1)))
!(println! (AFTER (fs:read-text (am:path))))
!(println! (AGAIN (attention-compare 1)))
''')
        before = output.split('(BEFORE ', 1)[1].split('\n', 1)[0]
        after = output.split('(AFTER ', 1)[1].split('\n', 1)[0]
        self.assertEqual(before, after)
        comparison = output.split('(COMPARE ', 1)[1].split('\n', 1)[0]
        again = output.split('(AGAIN ', 1)[1].split('\n', 1)[0]
        self.assertEqual(comparison, again)
        self.assertIn('(SameCandidatePool true) (PreQueryObservations true)', comparison)
        for arm in ['baseline', 'power-law', 'gain-need', 'inhibition']:
            self.assertIn('(StudyRanking "' + arm + '"', comparison)

    def test_scoped_queries_do_not_reintroduce_outside_high_scoring_memories(self):
        output = self.run_metta(QUIET + '''
!(workspace "only-a" ("task:a") () "task:a")
!(workspace-tag "a" ("task:a"))
!(attention-use "b" 100)
!(attention-feedback "b" 1 100)
!(workspace "only-a")
!(attention-policy "study-mix" 1)
!(println! (POWER (attention-try "power-law" "alpha" 0)))
!(println! (GAIN (attention-try "gain-need" "alpha" 0)))
!(println! (IOR (attention-try "inhibition" "alpha" 0)))
!(println! (COMPARISON (attention-compare 0)))
''')
        for marker in ['POWER', 'GAIN', 'IOR']:
            line = output.split('(' + marker + ' ', 1)[1].split('\n', 1)[0]
            self.assertIn('(MemoryHit "a"', line)
            self.assertNotIn('(MemoryHit "b"', line)
        comparison = output.split('(COMPARISON ', 1)[1].split('\n', 1)[0]
        self.assertNotIn('"b"', comparison)


if __name__ == '__main__':
    unittest.main()

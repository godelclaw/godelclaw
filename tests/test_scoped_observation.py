"""Scoped Empty reports through real native storage in both extended dialects."""
import hermetic_env  # noqa: F401,E402
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path(os.environ.get('CETTA_CHANNEL_ROOT', ROOT.parent / 'CeTTa-ours'))
sys.path[:0] = [str(BUNDLE / 'tools'), str(ROOT / 'tests')]
from space_service_client import E, S, encode, decode
import test_knowledge_store_cetta as knowledge_reference


def q(value):
    return json.dumps(str(value), ensure_ascii=False)


@unittest.skipUnless(os.environ.get('CETTA_BIN') and os.environ.get('CETTA_VALUE_WORKER'),
                     'set qualified native engine and worker')
class ScopedObservationTests(unittest.TestCase):
    def setUp(self):
        self.seed = knowledge_reference.KnowledgeStoreTests()
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        self.directory = self.seed.directory
        self.sequence = 0

    def guest(self, body, lang):
        self.sequence += 1
        source = self.directory / ('observe-' + str(self.sequence) + '.metta')
        result = self.directory / ('result-' + str(self.sequence) + '.cav1')
        source.write_text('\n'.join([
            '!(import! &self ' + q(ROOT / 'tests/support/scoped_observation_probe.metta') + ')',
            '(= (observation-probe:body $owner $view $epoch $revision) ' + body + ')',
            '!(let (StoreOpened $owner) (store:open ' + q(self.seed.path) + ' "existing")',
            ' (let (StoreSnapshot $view $epoch $revision) (store:snapshot $owner)',
            '  (let* (($out (observation-probe:body $owner $view $epoch $revision))',
            '         ($_ (observation-probe:save ' + q(result) + ' $out))',
            '         ($_ (collapse (store:close $view)))',
            '         ($_ (collapse (store:close $owner))))',
            '    True)))',
            '!(println! "SCOPED_OBSERVATION_OK")',
        ]) + '\n')
        process = subprocess.run([os.environ['CETTA_BIN'], '--lang', lang, '--profile', 'extended',
                                  '--import-mode', 'relative', str(source)],
                                 cwd=ROOT, capture_output=True, timeout=45)
        output = process.stdout.decode('utf-8', 'replace') + process.stderr.decode('utf-8', 'replace')
        self.assertEqual(process.returncode, 0, output[-3000:])
        self.assertTrue(result.is_file(), output[-3000:])
        self.assertIn('SCOPED_OBSERVATION_OK', output, (lang, body, output))
        return decode(result.read_bytes())

    def both(self, body):
        return [self.guest(body, lang) for lang in ('petta', 'he')]

    def projected(self, projection):
        self.assertEqual(projection[0], S('ObservationValuesV1'))
        return projection[3]

    def test_selected_native_library_matches_qualified_provenance(self):
        manifest = json.loads((ROOT / 'config/native-libraries.json').read_text())
        selection = json.loads((ROOT / 'config/runtime-files.json').read_text())
        self.assertEqual(manifest['schema'], 1)
        library, = manifest['libraries']
        path = ROOT / library['runtime']
        self.assertIn(library['runtime'], selection)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), library['sha256'])
        self.assertIn('config/native-libraries.json', selection)
        for name in ('src/knowledge_store_cetta.metta', 'src/knowledge_observation_cetta.metta'):
            self.assertIn(name, selection)

    def test_missing_key_in_nonempty_store_has_scoped_empty_and_no_values(self):
        self.seed.apply([E('KnowledgePutV1', 'other', 'present')])
        body = '(let $r (kb:read-report $view "missing" 1048576) (Results (obs:status $r) (obs:values $r) (obs:scope $r)))'
        for result in self.both(body):
            self.assertEqual(result[1], E('Empty', result[3]))
            self.assertEqual(self.projected(result[2]), ())
            self.assertEqual(result[3][0], S('ObservationIndexV1'))
            self.assertEqual(result[3][2], self.seed.epoch)

    def test_unit_empty_container_error_shaped_and_active_looking_data_are_values(self):
        values = [(), E('Container'), E('Error', 'source', 'ordinary'), E('+', 1, 2), E('Empty', 'nested')]
        for i, value in enumerate(values):
            self.seed.apply([E('KnowledgePutV1', str(i), value)])
        for i, value in enumerate(values):
            for language_index, result in enumerate(self.both('(let $r (kb:read-report $view "' + str(i) +
                                    '" 1048576) (Results (obs:values $r) (obs:diagnostics $r) (obs:coverage $r)))')):
                self.assertEqual(encode(self.projected(result[1])), encode((value,)), (language_index, i, result))
                self.assertEqual(result[2], ())
                self.assertEqual(result[3], S('ObservationClosedV1'))

    def test_materialization_limit_is_incomplete_even_with_zero_byte_allowance(self):
        self.seed.apply([E('KnowledgePutV1', 'present', 'payload')])
        for result in self.both('(let $r (kb:read-report $view "present" 0) (Results (obs:status $r) (obs:coverage $r)))'):
            self.assertEqual(result[1][0], S('Incomplete'))
            self.assertEqual(result[1][2], S('materialization-limit'))
        for result in self.both('(obs:status (kb:read-report $view "absent" 0))'):
            self.assertEqual(result[0], S('Empty'), 'a payload byte cap is not a search-work budget')

    def test_fault_is_structured_and_never_empty(self):
        for result in self.both('(let $r (obs:point-result scope "x" (StoreRefused permission)) '
                                '(Results (obs:status $r) (obs:diagnostics $r)))'):
            self.assertEqual(result[1][0], S('Faulted'))
            self.assertEqual(result[2][0][0], S('ObservationDiagnosticV1'))
            self.assertEqual(result[2][0][2], S('permission'))

    def test_choice_keeps_three_witnesses_and_pending_coverage(self):
        body = '''(let* (($left (ObservationReportV1 a () ObservationClosedV1 (a)))
          ($right (ObservationReportV1 b
           ((ObservationWitnessV1 one (ObservationBytesV1 "x"))
            (ObservationWitnessV1 two (ObservationBytesV1 "x"))
            (ObservationWitnessV1 three (ObservationBytesV1 "y")))
           (ObservationOpenV1 budget token) ()))
          ($r (obs:choice $left $right))) (Results (obs:values $r) (obs:status $r) (obs:dependencies $r)))'''
        for result in self.both(body):
            self.assertEqual(self.projected(result[1]), ('x', 'x', 'y'))
            self.assertEqual(result[1][2][0], S('ObservationOpenV1'))
            self.assertEqual(result[2][0], S('Incomplete'))
            self.assertEqual(result[3], (S('a'),))

    def test_reconciliation_is_not_choice_and_different_scope_is_refused(self):
        body = '''(let* (($a (ObservationReportV1 scope
          ((ObservationWitnessV1 one (ObservationBytesV1 "x"))) (ObservationOpenV1 budget token) ()))
          ($b (ObservationReportV1 scope
          ((ObservationWitnessV1 one (ObservationBytesV1 "x"))
           (ObservationWitnessV1 two (ObservationBytesV1 "x"))) ObservationClosedV1 ()))
          ($other (ObservationReportV1 other () ObservationClosedV1 (other))))
          (Results (obs:values (obs:reconcile $a $b)) (obs:reconcile $a $other)))'''
        for result in self.both(body):
            self.assertEqual(self.projected(result[1]), ('x', 'x'))
            self.assertEqual(result[2][0:2], (S('ObservationRefusedV1'), S('scope-mismatch')))

    def test_bound_revalidation_rejects_forgery_wrong_view_and_unbound_wire_data(self):
        body = '''(let* (($bound (kb:observe-report $view "missing" 1048576))
          ($report (kb:observation-report $bound))
          ($valid (kb:report-scopes-on $view $bound))
          ($wire (kb:report-scopes-on $view $report))
          ((KnowledgeObservationOnV1 $held $identity $budget $old) $bound)
          ($fake (KnowledgeObservationOnV1 $held $identity $budget
             (ObservationReportV1 (obs:scope $old)
               ((ObservationWitnessV1 fake (ObservationBytesV1 "invented"))) ObservationClosedV1 ())))
          ($forged (kb:report-scopes-on $view $fake))
          ((StoreSnapshot $other $other-epoch $other-revision) (store:snapshot $owner))
          ($wrong (kb:report-scopes-on $other $bound))
          ($_ (collapse (store:close $other)))) (Results $valid $wire $forged $wrong))'''
        for result in self.both(body):
            self.assertEqual(result[1][0], S('KnowledgeReadScopesV2'))
            for refused in result[2:]:
                self.assertEqual(refused[0], S('KnowledgeRefusedV1'))

    def test_bad_events_and_value_codecs_are_refusals_not_extra_values(self):
        for bad in ['(Junk 1)', '(ObservationWitnessV1 bad (ObservationCav1V1 "bad"))',
                    '(ObservationWitnessV1 bad (ObservationCav1V1 "CAV1?"))']:
            body = '(obs:values (ObservationReportV1 scope ((ObservationWitnessV1 good (ObservationBytesV1 "x")) ' + bad + ') ObservationClosedV1 ()))'
            for result in self.both(body):
                self.assertEqual(result[0], S('ObservationRefusedV1'))

    def test_choice_keeps_malformed_events_as_distinct_qualified_faults(self):
        body = '''(let* (($a (ObservationReportV1 a ((Junk 1) (Junk 2)) ObservationClosedV1 ()))
          ($b (ObservationReportV1 b ((ObservationWitnessV1 id (ObservationBytesV1 "x")))
                 (ObservationOpenV1 invalidated no-resume) ())) ($r (obs:choice $a $b)))
          (Results (obs:values $r) (obs:diagnostics $r) (obs:events $r) (obs:coverage $r)))'''
        for result in self.both(body):
            self.assertEqual(self.projected(result[1]), ('x',))
            self.assertEqual(len(result[2]), 2)
            self.assertNotEqual(result[3][0][1], result[3][1][1])
            self.assertEqual(result[4][2][2], E('ObservationOpenV1', S('invalidated'), S('no-resume')))

    def test_no_or_ambiguous_native_reply_is_a_fault(self):
        for answers in ['()', '(StoreAbsent StoreAbsent)']:
            for result in self.both('(let $r (obs:point-answers scope "x" ' + answers + ') (Results (obs:status $r) (obs:diagnostics $r)))'):
                self.assertEqual(result[1][0], S('Faulted'))
                self.assertEqual(len(result[2]), 1)

    def test_owner_handle_and_closed_snapshot_do_not_manufacture_absence(self):
        body = '''(let* (($wrong (obs:lookup $owner "missing" 1024))
          ($_ (collapse (store:close $view))) ($closed (obs:lookup $view "missing" 1024)))
          (Results (obs:status $wrong) (obs:status $closed)))'''
        for result in self.both(body):
            self.assertEqual([r[0] for r in result[1:]], [S('Faulted'), S('Faulted')])

    def test_failed_descriptor_encoding_and_decoding_stay_explicit(self):
        body = '''(Results (obs:index $view query region $view "id")
          (obs:describe-index (ObservationIndexV1 domain epoch 0 "bad" "bad" "bad" run)))'''
        for result in self.both(body):
            self.assertEqual(result[1][0], S('ObservationRefusedV1'))
            self.assertIn('unavailable descriptor', result[2])

    def test_insertion_invalidates_absence_dependent_publication(self):
        body = '''(let* (($id (system:uuid4-hex))
          ($bound (kb:observe-report $view $id 1048576))
          ($before (obs:status (kb:observation-report $bound)))
          ((KnowledgeReadScopesV2 $reads) (kb:report-scopes-on $view $bound))
          ($first (kb:publish $owner $view (reads) (WriterReceipt $id) write done
                    ((KnowledgePutV1 $id "present")) 1048576))
          ($late (kb:publish $owner $view $reads (LateReceipt $id) derive done
                    ((KnowledgePutV1 (Derived $id) "must not land")) 1048576)))
          (Results $before $first $late))'''
        for result in self.both(body):
            self.assertEqual(result[1][0], S('Empty'))
            self.assertEqual(result[2][0], S('KnowledgeCommittedV1'), result)
            self.assertEqual(result[3], E('StoreRefused', S('conflict')))



if __name__ == '__main__':
    unittest.main()

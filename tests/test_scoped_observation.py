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
            '!(import! &self ' + q(getattr(self, 'probe', ROOT / 'tests/support/scoped_observation_probe.metta')) + ')',
            '(= (observation-probe:body $owner $view $epoch $revision) ' + body + ')',
            '!(let (StoreOpened $owner) (store:open ' + q(self.seed.path) + ' "existing")',
            ' (let (StoreSnapshot $view $epoch $revision) (store:snapshot $owner)',
            '  (let* (($out (observation-probe:body $owner $view $epoch $revision))',
            '         ($discarded1 (observation-probe:save ' + q(result) + ' $out))',
            '         ($discarded2 (collapse (store:close $view)))',
            '         ($discarded3 (collapse (store:close $owner))))',
            '    (println! "SCOPED_OBSERVATION_OK"))))',
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
        for library in manifest['libraries']:
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
            self.assertIsInstance(result[1][2], tuple)
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
          ($discarded4 (collapse (store:close $other)))) (Results $valid $wire $forged $wrong))'''
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
          ($discarded5 (collapse (store:close $view))) ($closed (obs:lookup $view "missing" 1024)))
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


    def test_computational_zero_empty_unit_syntax_and_not_observed_stay_distinct(self):
        body = """(let* (($report (kb:read-report $view "missing" 1024))
          ($index (obs:scope $report)) ($syntax (obs:syntax (+ 1 2))))
          (Results (collapse (superpose ())) (collapse (obs:status $report))
           (collapse (function (return ()))) $syntax
           (obs:status (obs:undemanded $index))
           (obs:negative-grade ($report $report))))"""
        for result in self.both(body):
            self.assertEqual(result[1], ())
            self.assertEqual(len(result[2]), 1)
            self.assertEqual(result[2][0][0], S('Empty'))
            self.assertEqual(result[3], ((),))
            self.assertEqual(decode(result[4][1].encode('utf-8')), E('+', 1, 2))
            self.assertEqual(result[5][0], S('NotObserved'))
            self.assertEqual(len(result[6][1]), 2)
            self.assertEqual(result[6][1][0], result[6][1][1])

    def test_epsilon_is_one_success_and_no_match_is_scoped_empty(self):
        body = """(let* (($yes (obs:regex-find (re:pattern "") ""))
          ($no (obs:regex-find (re:pattern "x") ""))
          ($fault (obs:regex-find (re:pattern "[") "")))
          (Results (obs:status $yes) (obs:values $yes) (obs:status $no)
            (obs:status $fault) (obs:negative-grade ($yes))))"""
        for result in self.both(body):
            self.assertEqual(result[1][0], S('Observed'))
            values = self.projected(result[2])
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0][0:3], (S('RegexOccurrenceV1'), 0, 0))
            self.assertEqual(result[3][0], S('Empty'))
            self.assertEqual(result[4][0], S('Faulted'))
            self.assertEqual(result[5][0], S('ObservationRefusedV1'))


    def test_native_range_zero_work_and_resumption_keep_duplicate_occurrences(self):
        body = """(let* (((StoreSnapshot $updated $e $r)
          (store:commit $owner $view (ops (put "a" "A") (put "b" "A") (put "c" "B"))))
          ((ObservationStepV1 $initial $c0) (obs:open-range $updated NoBound NoBound 4))
          ($discarded6 (collapse (store:close $updated)))
          ((ObservationStepV1 $zero $c1) (obs:step $c0 0 100))
          ((ObservationStepV1 $first $c2) (obs:step $c1 2 100))
          ((ObservationStepV1 $finished $none) (obs:step $c2 8 100))
          ((ObservationStepV1 $reused $nothing) (obs:step $c1 8 100)))
          (Results (obs:status $initial) (obs:status $zero) (obs:values $first)
            (obs:values $finished) (obs:reconcile $first $finished)
            $none (obs:status $reused)))"""
        for result in self.both(body):
            self.assertEqual(result[1][0], S('Incomplete'))
            self.assertEqual(result[2][0], S('Incomplete'))
            self.assertEqual(self.projected(result[3]), ('A', 'A'))
            self.assertEqual(self.projected(result[4]), ('A', 'A', 'B'))
            self.assertEqual(result[4][2], S('ObservationClosedV1'))
            self.assertEqual(result[5][0], S('ObservationReportV1'))
            self.assertEqual(result[6], S('NoObservationContinuationV1'))
            self.assertEqual(result[7][0], S('Faulted'))

    def test_native_range_byte_limit_retry_and_cancel_keep_the_prefix(self):
        body = """(let* (((StoreSnapshot $updated $e $r)
          (store:commit $owner $view (ops (put "a" "long payload") (put "b" "B"))))
          ((ObservationStepV1 $initial $c0) (obs:open-range $updated NoBound NoBound 2))
          ((ObservationStepV1 $limited $c1) (obs:step $c0 1 0))
          ((ObservationStepV1 $partial $c2) (obs:step $c1 1 100))
          ((ObservationStepV1 $cancelled $none) (obs:cancel $c2))
          ((ObservationStepV1 $late $no) (obs:step $c2 5 100))
          ($discarded7 (collapse (store:close $updated))))
          (Results (obs:status $limited) (obs:values $partial)
            (obs:values $cancelled) (obs:coverage $cancelled) (obs:status $late) (obs:coverage $limited)))"""
        for result in self.both(body):
            self.assertEqual(result[1][2], S('materialization-limit'))
            self.assertEqual(self.projected(result[2]), ('long payload',))
            self.assertEqual(self.projected(result[3]), ('long payload',))
            self.assertEqual(result[4][1], S('cancelled'))
            self.assertEqual(result[5][0], S('Faulted'))
            self.assertEqual(result[6][2][3], E('RequiredBytesV1', len('long payload')))

    def test_native_range_resource_limit_and_unissued_work_refuse(self):
        body = """(let* (((ObservationStepV1 $initial $c0) (obs:open-range $view NoBound NoBound 1))
          ($refused (obs:open-range $view NoBound NoBound 1))
          ($zero (obs:open-range $view NoBound NoBound 0))
          ($fake (work-new (FabricatedState would-execute-if-forced)))
          ((ObservationStepV1 $bad $none) (obs:step (ObservationCursorV2 $fake) 1 100))
          ($untouched (work-take $fake 1)) ($discarded8 (collapse (work-cancel $fake)))
          ($discarded9 (obs:cancel $c0))
          ((ObservationStepV1 $after $c1) (obs:open-range $view NoBound NoBound 1))
          ((ObservationStepV1 $empty $end) (obs:step $c1 1 100)))
          (Results $refused $zero (obs:status $bad) $untouched (obs:status $empty)))"""
        for result in self.both(body):
            self.assertEqual(result[1][0:2], (S('ObservationRefusedV1'), S('cursor-capacity')))
            self.assertEqual(result[2][0:2], (S('ObservationRefusedV1'), S('cursor-capacity')))
            self.assertEqual(result[3][0], S('Faulted'))
            self.assertEqual(result[4][0], S('taken'))
            self.assertEqual(len(result[4][1]), 1)
            self.assertEqual(result[5][0], S('Empty'))


    def test_reconciliation_keeps_negative_premises_and_refuses_backward_progress(self):
        body = """(let* (($a (ObservationReportV1 scope () (ObservationOpenV1 budget residual) (first)))
          ($lost (ObservationReportV1 scope () (ObservationOpenV1 budget residual) ()))
          ($regressed (ObservationReportV1 scope () ObservationNotStartedV1 (first)))
          ($bad (ObservationReportV1 scope ((ObservationWitnessV1 id (ObservationBytesV1 "x")))
                 ObservationNotStartedV1 ())))
          (Results (obs:reconcile $a $lost) (obs:reconcile $a $regressed) (obs:status $bad)))"""
        for result in self.both(body):
            self.assertEqual(result[1][1], S('negative-dependency-conflict'))
            self.assertEqual(result[2][1], S('progress-regressed'))
            self.assertEqual(result[3][1], S('not-started-with-occurrences'))

    def test_many_ordered_values_roundtrip_without_container_header_truncation(self):
        events = ' '.join('(ObservationWitnessV1 (id ' + str(i) + ') (ObservationBytesV1 "v"))' for i in range(129))
        body = '(obs:values (ObservationReportV1 scope (' + events + ') ObservationClosedV1 ()))'
        for result in self.both(body):
            self.assertEqual(self.projected(result), ('v',) * 129)

    def test_range_boundaries_and_captured_values_survive_later_writes_and_compaction(self):
        body = """(let* (((StoreSnapshot $before $e1 $r1)
          (store:commit $owner $view (ops (put "a" "outside") (put "b" "old") (put "c" "upper"))))
          ((ObservationStepV1 $initial $cursor) (obs:open-range $before "b" "c" 2))
          ((StoreSnapshot $after $e2 $r2) (store:commit $owner $before (ops (put "b" "new"))))
          ($closed1 (collapse (store:close $before))) ($closed2 (collapse (store:close $after)))
          ((StoreSnapshot $compacted $compact-epoch $compact-revision) (store:compact $owner))
          ($compact-closed (collapse (store:close $compacted)))
          ((ObservationStepV1 $finished $none) (obs:step $cursor 4 100)))
          (Results (obs:values $finished) (obs:scope $initial) (obs:scope $finished) (Compacted $compact-epoch $compact-revision)))"""
        for result in self.both(body):
            self.assertEqual(self.projected(result[1]), ('old',))
            self.assertEqual(result[1][2], S('ObservationClosedV1'))
            self.assertEqual(result[2], result[3])
            self.assertEqual(result[4][0], S('Compacted'))


    def test_invalid_budgets_do_not_consume_the_owned_residual(self):
        body = """(let* (((ObservationStepV1 $initial $cursor) (obs:open-range $view NoBound NoBound 1))
          ($negative (obs:step $cursor -1 100)) ($fraction (obs:step $cursor 0.5 100))
          ($text (obs:step $cursor "zero" 100))
          ($bad-limit (obs:open-range $view NoBound NoBound "one"))
          ((ObservationStepV1 $finished $none) (obs:step $cursor 1 100)))
          (Results $negative $fraction $text $bad-limit (obs:status $finished)))"""
        for result in self.both(body):
            for refused in result[1:5]:
                self.assertEqual(refused[0], S('ObservationRefusedV1'))
            self.assertEqual(result[5][0], S('Empty'))

    def test_inert_indices_roundtrip_through_codec_and_parser_without_query_execution(self):
        body = """(let* (($a (obs:lookup-at $view "(NotAnExecutedQuery 1)" 1024 "fixed"))
          ($b (obs:lookup-at $view "different" 1024 "fixed"))
          ($wire (obs:encode (Envelope (obs:status $a) $a)))
          ((StoreDecoded $copy) (store:decode $wire)) ($parsed (parse (repr $copy))))
          (Results (obs:same $copy $parsed) (obs:reconcile $a $b)))"""
        for result in self.both(body):
            self.assertIn(result[1], (True, S('true'), S('True')))
            self.assertEqual(result[2][1], S('scope-mismatch'))


    def test_overlapping_fragments_reconcile_replays_but_keep_distinct_equal_values(self):
        body = """(let* (($one (ObservationWitnessV1 one (ObservationBytesV1 "equal")))
          ($two (ObservationWitnessV1 two (ObservationBytesV1 "equal")))
          ($three (ObservationWitnessV1 three (ObservationBytesV1 "last")))
          ($a (ObservationReportV1 scope ($one $two) (ObservationOpenV1 budget a-residual) (a)))
          ($b (ObservationReportV1 scope ($two $three) (ObservationOpenV1 budget b-residual) (b)))
          ($merged (obs:reconcile-overlap $a $b)))
          (Results (obs:values $merged) (obs:dependencies $merged) (obs:status $merged)))"""
        for result in self.both(body):
            self.assertEqual(self.projected(result[1]), ('equal', 'equal', 'last'))
            self.assertEqual(result[2], (S('a'), S('b')))
            self.assertEqual(result[3][0], S('Incomplete'))

    def test_overlapping_fragments_refuse_conflicting_records_order_and_closed_coverage(self):
        body = """(let* (($one (ObservationWitnessV1 one (ObservationBytesV1 "one")))
          ($two (ObservationWitnessV1 two (ObservationBytesV1 "two")))
          ($changed (ObservationWitnessV1 one (ObservationBytesV1 "changed")))
          ($a (ObservationReportV1 scope ($one $two) (ObservationOpenV1 budget residual) ()))
          ($b (ObservationReportV1 scope ($changed) (ObservationOpenV1 budget residual) ()))
          ($order (ObservationReportV1 scope ($two $one) (ObservationOpenV1 budget residual) ()))
          ($closed (ObservationReportV1 scope ($one) ObservationClosedV1 ())))
          (Results (obs:reconcile-overlap $a $b) (obs:reconcile-overlap $a $order)
           (obs:reconcile-overlap $a $closed)))"""
        for result in self.both(body):
            self.assertEqual([v[0] for v in result[1:]], [S('ObservationRefusedV1')] * 3)
            self.assertEqual(result[1][1], S('occurrence-conflict'))
            self.assertEqual(result[2][1], S('occurrence-order-conflict'))
            self.assertEqual(result[3][1], S('closed-observation-changed'))


    def test_declared_empty_report_preserves_each_dialects_bare_empty(self):
        for lang, expected in [('petta', (S('Empty'),)), ('he', ())]:
            result = self.guest('(Results (collapse Empty) (collapse (Empty index)))', lang)
            self.assertEqual(result[1], expected)
            self.assertEqual(result[2], (E('Empty', S('index')),))


    def test_explanation_counts_occurrences_without_materializing_payloads(self):
        body = """(obs:explain (ObservationReportV1 scope
          ((ObservationWitnessV1 one (ObservationCav1V1 "not-decoded-for-counting"))
           (ObservationWitnessV1 two (ObservationBytesV1 "large-value-placeholder"))
           (ObservationFaultV1 fail (obs:diagnostic search io "unavailable")))
          (ObservationOpenV1 fault NoObservationContinuationV1) ()))"""
        for result in self.both(body):
            self.assertIn('witnesses=2', result)
            self.assertIn('faults=1', result)


    def test_regex_refuses_unencodable_query_owners_and_records_native_unicode_policy(self):
        body = """(let* (($prepared (re:prepare (re:pattern "x")))
          ($unbound (obs:regex-find $prepared ""))
          ($closed (collapse (re:close $prepared)))
          ($complete (obs:regex-find (re:pattern "x") ""))
          ($version (str:unicode-version)))
          (Results (obs:status $unbound) (obs:diagnostics $unbound)
            (obs:scope $complete) $version))"""
        for result in self.both(body):
            self.assertEqual(result[1][0], S('Faulted'))
            self.assertEqual(result[2][0][2], S('descriptor-codec'))
            self.assertEqual(result[3][3], E('NativeRegexFindV1', E('UnicodeBaseV1', result[4])))



if __name__ == '__main__':
    unittest.main()

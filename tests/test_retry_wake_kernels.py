"""Finite model coverage, nontrivial kernel proofs and exact evidence checks."""
import os
import json
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import check_retry_wake as checker
import retry_wake_compiler as compiler
from generate_retry_wake import artifacts


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'configure CETTA_BIN')
class RetryWakeKernelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary=Path(os.environ['CETTA_BIN']).resolve()
        base=ROOT/'.runtime/contract-tests';base.mkdir(parents=True,exist_ok=True)
        cls.temporary=tempfile.TemporaryDirectory(dir=base)
        cls.work=Path(cls.temporary.name)
        cls.symbol,cls.string,parse=compiler.tool_modules(cls.binary)
        cls.parse=staticmethod(parse)
        cls.model=compiler.read_module(cls.binary,ROOT/'verification/RetryWake.module',cls.work)
        cls.receipt=checker.check(cls.binary,cls.work)
        cls.cases=checker.claims(cls.model)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def test_generated_views_match_elaborated_rules(self):
        for path,body in artifacts(self.model).items():
            self.assertEqual(path.read_text(),body)
        self.assertEqual(len(self.model['transitions']),24)
        self.assertEqual(len(self.model['active']),6)
        self.assertEqual(len(self.model['terminal']),4)

    def test_real_kernel_computations_and_exact_module_results(self):
        for lane, counts in self.receipt['lanes'].items():
            self.assertEqual(counts['transition_proofs'],24)
            self.assertEqual(counts['inactive_proofs'],18)
            self.assertEqual(counts['module_executions'],34)
            self.assertTrue(counts['wrong_result_refused'])
        self.assertEqual(self.receipt['lanes']['he']['megalodon_proofs'],42)
        self.assertEqual(self.receipt['lanes']['petta']['megalodon_proofs'],42)
        self.assertEqual(self.receipt['host_cases'],48)
        self.assertFalse(self.receipt['live_policy_changed'])
        self.assertTrue(self.receipt['mm0'].startswith('unsupported'))

    def test_overlapping_and_missing_source_rules_are_rejected(self):
        text=(ROOT/'verification/RetryWake.module').read_text()
        marker='    Rewrites {'
        mutations=[text.replace(marker,marker+'\n      Duplicate:\n        (Reconcile (RequestFailed) (CallSubmitted)) ~> (CallSubmitted);'),
                   text.replace('      FailedSubmitted:\n        (Reconcile (RequestFailed) (CallSubmitted)) ~> (CallUncertain);\n','')]
        for i,source in enumerate(mutations):
            with self.subTest(mutation=i):
                path=self.work/f'mutant-{i}.module';path.write_text(source)
                with self.assertRaises(ValueError): compiler.read_module(self.binary,path,self.work)

    def test_exact_authentication_rejects_forged_evidence(self):
        text=(self.work/'kernels.petta.stdout').read_text()
        for forged in (text.replace('(Axioms ())','(Axioms (user-axiom))',1),
                       text.replace('(thm ()','(thm ((v "assumed" (T "bool")))',1)):
            with self.assertRaises(ValueError):
                checker.authenticate(checker.output_values(forged,self.parse),self.cases,
                                     self.symbol,self.string,self.parse)
        records=checker.output_values(text,self.parse)
        with self.assertRaises(ValueError):
            checker.authenticate(records+[records[0]],self.cases,self.symbol,self.string,self.parse)

    def test_reflexivity_on_an_answer_is_not_a_transition_proof(self):
        case=next(c for c in self.cases if c[:3]==('transition','RequestFailed','CallSubmitted'))
        left,right=case[-2:]
        rows=f'((HolNode 0 (REFL {compiler.hol(right)}) () (thm () {compiler.equality(left,right)})))'
        source='!(import! &self '+json.dumps(str(ROOT/'verification/RetryWakeKernels.metta'))+')\n'
        source+=f'!(println! (gc:hol-check {rows} 0))\n'
        out=checker.execute(self.binary,'petta',source,self.work,'reflexivity-must-fail')
        self.assertEqual(checker.output_values(out,self.parse),[(self.symbol('GcHolRefusedV2'),)])

    def test_lean_comparison_when_source_is_supplied(self):
        supplied=os.environ.get('METTAPEDIA_RETRY_WAKE')
        if not supplied: self.skipTest('set METTAPEDIA_RETRY_WAKE to the canonical Lean model')
        models=Path(supplied);models=models.parent if models.is_file() else models
        result=checker.check_lean(self.model,models,os.environ.get('LEAN_TOOLCHAIN','v4.33.1'),self.work)
        self.assertEqual(result['compared_observations'],34)
        self.assertTrue(set(result['invariant_axioms']).issubset({'propext'}))


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'configure CETTA_BIN')
class ReadOnlyInspectionTest(unittest.TestCase):
    """The MeTTa inspector reads through sqlite:observe and changes nothing."""
    def ledger(self, directory, rows):
        import sqlite3
        path=Path(directory)/'ledger.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE requests(id TEXT,status TEXT);'
                'CREATE TABLE native_calls(id TEXT,state TEXT,updated REAL,created REAL);')
            for identity,status,call,created in rows:
                if status is not None:
                    db.execute('INSERT INTO requests VALUES (?,?)',(identity,status))
                db.execute('INSERT INTO native_calls VALUES (?,?,0,?)',(identity,call,created))
            db.commit()
        return path

    def inspect(self, path, *moments):
        import subprocess
        out=Path(path).parent/'inspection.json'
        program=ROOT/('.inspect_probe_%d.metta'%os.getpid())
        program.write_text('!(import! &self (library lib_import))\n!(import! &self (library fs))\n'
            '!(import! &self (library json))\n!(import! &self ./verification/RetryWakeInspect.metta)\n'
            '!(fs:update-text %s "replace" (json-stringify (JsonArray (%s))) "" "")\n'
            % (json.dumps(str(out)), ' '.join('(case (gc:inspect %s %s) (((GcInspection $j%d) $j%d) ((GcInspectionError $e%d) (JsonString $e%d))))'
                                                  % (json.dumps(str(path)), m, i, i, i, i) for i, m in enumerate(moments))))
        try:
            result=subprocess.run([os.environ['CETTA_BIN'],'--lang','petta','--import-mode','ancestor-walk',str(program)],
                                  cwd=ROOT,capture_output=True,text=True,timeout=60)
        finally:
            program.unlink()
        self.assertTrue(out.is_file(),result.stdout[-2000:]+result.stderr[-2000:])
        return json.loads(out.read_text())

    def test_missing_ledger_is_not_created(self):
        import subprocess
        base=ROOT/'.runtime/contract-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path=Path(directory)/'absent.sqlite3'
            result=subprocess.run([os.environ['CETTA_BIN'],'--lang','petta','--import-mode','ancestor-walk',
                                   'scripts/inspect_retry_wake.metta',str(path)],cwd=ROOT,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('inspect:',result.stdout)
            self.assertFalse(path.exists())

    def test_observation_does_not_reconcile_or_change_database(self):
        base=ROOT/'.runtime/contract-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path=self.ledger(directory,[('x','failed','submitted',0)])
            before=path.read_bytes()
            settling,later=self.inspect(path,1,10)
            self.assertEqual(settling['scope'],'read-only terminal-request diagnostics; no action authority')
            row=settling['observations'][0]
            self.assertTrue(row['settlement_window'])
            self.assertFalse(row['contradiction'])
            row=later['observations'][0]
            self.assertEqual((row['model_result'],row['contradiction'],row['age_seconds']),('uncertain',True,10.0))
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(Path(str(path)+'-journal').exists())

    def test_unrecorded_and_pending_requests_are_reported_as_found(self):
        base=ROOT/'.runtime/contract-tests';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path=self.ledger(directory,[('a',None,'claimed',1),('b','pending','claimed',2),('c','unknown','claimed',3),
                                        ('d','completed','completed',4)])
            rows=self.inspect(path,100)[0]['observations']
            self.assertEqual([r['id'] for r in rows],['a','b','c'])
            self.assertEqual((rows[0]['request'],rows[0]['model_result'],rows[0]['modelled'],rows[0]['terminal_request']),
                             (None,None,False,False))
            self.assertEqual((rows[1]['model_result'],rows[1]['terminal_request'],rows[1]['contradiction']),('claimed',False,False))
            self.assertEqual((rows[2]['model_result'],rows[2]['terminal_request'],rows[2]['contradiction']),('uncertain',True,True))

    def test_the_grace_window_is_the_runtime_s(self):
        import re
        native=re.search(r'^TERMINAL_RECONCILE_GRACE_SECONDS = (\d+)$',(ROOT/'src/native_lifetime.py').read_text(),re.M)
        metta=re.search(r'^\(= \(gc:grace-seconds\) (\d+)\)$',(ROOT/'verification/RetryWakeInspect.metta').read_text(),re.M)
        self.assertEqual(native.group(1),metta.group(1))


if __name__=='__main__': unittest.main(verbosity=2)

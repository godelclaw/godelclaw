"""Constraints on already-bound rows, independent of input-affinity conversion.

SQLite remains the test oracle. The checked module is inactive and does not
replace the transition kernel or its complete observation/receipt boundary.
"""
import os
import hashlib
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
CETTA_ROOT=Path(os.environ.get('CETTA_CHANNEL_ROOT',ROOT.parent/'CeTTa-ours'))
WORKER=os.environ.get('CETTA_VALUE_WORKER')
sys.path.insert(0,str(ROOT/'src'))
if WORKER:
    sys.path.insert(0,str(CETTA_ROOT/'tools'))
    from value_worker_client import Worker
    from space_service_client import E,S,decode,encode

FIELDS={
    'ProviderRequestV1':('requests',('id','started','reserved','status','output','provider','mode','finish','owner','fingerprint')),
    'ProviderCooldownV1':('cooldowns',('provider','until')),
    'ProviderCallV1':('native_calls',('id','epoch','pid','process_start','created','updated','state','guard','transport',
        'soft_elapsed','bytes','quiet_ms','result','reason','activity','lane','purpose','notified')),
    'ProviderEventV1':('llm_call_history',('call_id','event','observed','detail')),
    'ProviderAdmissionRecoveryV1':('admission_recovery',('id','recovered_at','reason','cutoff')),
}
INDEX_COLUMNS={
    'requests':('id','status','provider','mode','owner'),
    'cooldowns':('provider',),
    'native_calls':('id','epoch','state','activity','lane','purpose','notified'),
    'llm_call_history':('call_id','event','identity'),
    'admission_recovery':('id',),
}


class InvalidSqlText(bytes):
    """Retain SQLite TEXT kind when its bytes are not valid UTF-8."""


def text_factory(data):
    try:return data.decode('utf-8')
    except UnicodeDecodeError:return InvalidSqlText(data)


@unittest.skipUnless(WORKER,'set CETTA_VALUE_WORKER to the native disk-store worker')
class ProviderConstraintTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='provider-constraint-')
        self.addCleanup(self.temp.cleanup)
        directory=Path(self.temp.name);self.path=directory/'native';self.path.mkdir(mode=0o700)
        database=directory/'oracle.sqlite3'
        environment={'HOME':str(directory),'PATH':os.environ.get('PATH','/usr/bin:/bin'),
                     'METTACLAW_LLM_LEDGER_PATH':str(database)}
        with patch.dict(os.environ,environment,clear=True):
            # Build the current primary application's schema in a disposable
            # ledger. No provider transport or live agent state is imported.
            import native_lifetime
            with native_lifetime.transaction() as connection:
                connection.execute('DELETE FROM llm_call_history')
                # Optional repair evidence is retained by the checkpoint ABI.
                connection.execute('CREATE TABLE admission_recovery(id TEXT PRIMARY KEY,'
                    'recovered_at REAL NOT NULL,reason TEXT NOT NULL,cutoff REAL NOT NULL)')
            self.writer=Worker(WORKER,ROOT/'src/provider_index_store_cetta.metta',
                               'provider-index-store:call',language='petta')
            self.probe=Worker(WORKER,ROOT/'tests/support/provider_constraint_probe.metta',
                              'provider-constraint-probe:call',language='petta',import_mode='relative')
        for worker in (self.writer,self.probe):self.addCleanup(worker.close)
        ready=self.writer.call(E('Create',str(self.path)))
        self.assertEqual(ready[0],S('ProviderIndexedReadyV1'))
        self.epoch,self.revision=ready[1:]
        self.db=sqlite3.connect(database);self.db.text_factory=text_factory;self.addCleanup(self.db.close)

    @staticmethod
    def request(ordinal,identity='id',status='pending'):
        return E('ProviderRequestV1',identity,ordinal,1.0,20,status,S('JsonNull'),'provider','mode',
                 S('JsonNull'),'owner','fingerprint')

    @staticmethod
    def row(kind,ordinal,identity='id'):
        if kind=='ProviderRequestV1':return ProviderConstraintTests.request(ordinal,identity)
        if kind=='ProviderCooldownV1':return E(kind,identity,ordinal,1.0)
        if kind=='ProviderCallV1':return E(kind,identity,ordinal,'epoch',17,'born',1.0,2.0,'claimed',
            S('JsonNull'),S('JsonNull'),0,0,0,S('JsonNull'),S('JsonNull'),'activity','lane','turn',0)
        if kind=='ProviderEventV1':return E(kind,identity,'event',ordinal,1.0,'{}')
        return E(kind,identity,ordinal,1.0,'fixture',0.0)

    @staticmethod
    def parts(row):
        kind=row[0].name;table,fields=FIELDS[kind]
        if kind=='ProviderEventV1':return table,fields,row[3],tuple(row[1:3])+tuple(row[4:])
        return table,fields,row[2],(row[1],)+tuple(row[3:])

    @staticmethod
    def parameter(value):
        if value==S('JsonNull'):return None
        if isinstance(value,tuple) and value[0]==S('SqlBlobV1'):
            return value[1].encode('utf-8') if isinstance(value[1],str) else value[1]
        return value

    def reference(self,mutations):
        try:
            self.db.execute('BEGIN')
            for verb,row in mutations:
                table,fields,ordinal,values=self.parts(row)
                if verb.name in ('put','remove'):
                    self.db.execute('DELETE FROM '+table+' WHERE rowid=?',(ordinal,))
                if verb.name in ('put','add'):
                    names=('sequence',)+fields if row[0]==S('ProviderEventV1') else ('rowid',)+fields
                    # An unboxed CAV1 byte string is already-stored TEXT, not
                    # a Python bytes parameter. CAST constructs that exact
                    # stored kind; SqlBlobV1 continues to bind as BLOB.
                    slots=['?']+['CAST(? AS TEXT)' if isinstance(value,bytes) else '?' for value in values]
                    self.db.execute('INSERT INTO '+table+'('+','.join(names)+') VALUES('+','.join(slots)+')',
                        (ordinal,)+tuple(map(self.parameter,values)))
            self.db.commit();return True
        except sqlite3.IntegrityError:
            self.db.rollback();return False

    def audit(self):
        answer=self.probe.call(E('Audit',str(self.path)))
        self.assertEqual(answer[0],S('ProviderIndexAuditV1'))
        return {key.encode('utf-8') if isinstance(key,str) else key:
                value.encode('utf-8') if isinstance(value,str) else value for key,value in answer[1]}

    def assert_rows(self):
        physical=self.audit()
        actual={}
        for key,value in physical.items():
            if key.startswith((b'requests/',b'cooldowns/',b'native_calls/',b'llm_call_history/',b'admission_recovery/')):
                row=decode(value);actual[(row[0].name,self.parts(row)[2])]=encode(row)
        expected={}
        expected_physical={b'layout/provider-index/v1':encode(E('ProviderIndexLayoutV1',1))}
        for kind,(table,fields) in FIELDS.items():
            for values in self.db.execute('SELECT rowid,'+','.join(fields)+' FROM '+table+' ORDER BY rowid'):
                ordinal=values[0]
                cells=[S('JsonNull') if x is None else bytes(x) if isinstance(x,InvalidSqlText)
                       else E('SqlBlobV1',x) if isinstance(x,bytes) else x for x in values[1:]]
                row=E(kind,*cells[:2],ordinal,*cells[2:]) if kind=='ProviderEventV1' else E(kind,cells[0],ordinal,*cells[1:])
                expected[(kind,ordinal)]=encode(row)
                suffix=((ordinal&((1<<64)-1))^(1<<63)).to_bytes(8,'big')
                primary=table.encode()+b'/'+suffix
                expected_physical[primary]=encode(row)
                columns=dict(zip(fields,cells))
                if kind=='ProviderEventV1':columns['identity']=E('ProviderEventIdentityV1',cells[0],cells[1])
                for column in INDEX_COLUMNS[table]:
                    prefix=b'index/v1/'+table.encode()+b'/'+column.encode()+b'/'+\
                        hashlib.sha256(encode(columns[column])).hexdigest().encode()+b'/'
                    expected_physical[prefix+suffix]=encode(E('ProviderIndexV1',primary))
        self.assertEqual(actual,expected)
        self.assertEqual(physical,expected_physical,'primary/index publication differs from the complete reference')

    def apply(self,mutations,page=2):
        before=self.audit();accepted=self.reference(mutations)
        answer=self.probe.call(E('Apply',str(self.path),tuple(mutations),page,E('reads')))
        self.assertEqual(answer[0]==S('ProviderConstraintCommittedV1'),accepted,answer)
        if not accepted:self.assertEqual(self.audit(),before,'rejected batch changed primary/index bytes')
        self.assert_rows();return answer

    def test_nullable_unique_and_not_null_constraints_match_the_primary_schema(self):
        for kind,(table,fields) in FIELDS.items():
            with self.subTest(kind=kind):
                self.apply([E('add',self.row(kind,1))])
                self.apply([E('add',self.row(kind,2))])
                # Ordinary TEXT primary keys remain nullable; event columns do not.
                self.apply([E('add',self.row(kind,3,S('JsonNull'))),E('add',self.row(kind,4,S('JsonNull')))])
                required=[r[1] for r in self.db.execute('PRAGMA table_info('+table+')') if r[3]]
                for column in required:
                    values=list(self.row(kind,10,'fresh-'+column))
                    positions=[1,2,4,5] if kind=='ProviderEventV1' else [1]+list(range(3,len(values)))
                    values[positions[fields.index(column)]]=S('JsonNull')
                    self.apply([E('add',tuple(values))])

    def test_prior_mutations_shadow_the_snapshot_and_only_latest_versions_count(self):
        first=self.request(1,'a');second=self.request(2,'b')
        self.apply([E('add',first),E('add',second)])
        self.apply([E('remove',first),E('add',self.request(3,'a'))])
        self.apply([E('add',self.request(4,'b')),E('remove',second)])
        self.apply([E('put',self.request(2,'c')),E('put',self.request(2,'d')),E('add',self.request(4,'c'))])
        self.apply([E('put',self.request(2,'a'))])

    def test_typed_text_and_blob_keys_and_embedded_nul_are_distinct(self):
        keys=['bytes',E('SqlBlobV1',b'bytes'),'a\0b','a\0c',E('SqlBlobV1',b'\x00\xff'),
              b'\xff',E('SqlBlobV1',b'\xff'),'','JsonNull','0','1','é','e\u0301','🙂']
        for ordinal,key in enumerate(keys,1):self.apply([E('add',self.request(ordinal,key))])
        for ordinal,key in enumerate(keys,100):self.apply([E('add',self.request(ordinal,key))])

    def inject_candidate(self,identity,ordinal):
        suffix=((ordinal&((1<<64)-1))^(1<<63)).to_bytes(8,'big')
        prefix=b'index/v1/requests/id/'+hashlib.sha256(encode(identity)).hexdigest().encode()+b'/'
        result=self.probe.call(E('RawPut',str(self.path),prefix+suffix,encode(E('ProviderIndexV1',b'requests/'+suffix))))
        self.assertEqual(result[0],S('ConstraintAcceptedV1'))

    def test_an_empty_filtered_page_does_not_hide_a_later_duplicate(self):
        self.apply([E('add',self.request(1,'other')),E('add',self.request(2,'wanted'))])
        self.inject_candidate('wanted',1)
        before=self.audit()
        result=self.probe.call(E('Apply',str(self.path),(E('add',self.request(3,'wanted')),),1,E('reads')))
        self.assertEqual(result[0],S('ProviderConstraintViolationV1'))
        self.assertEqual(self.audit(),before)
        self.assertFalse(self.reference([E('add',self.request(3,'wanted'))]))

    def test_filtered_candidate_body_is_a_required_read_dependency(self):
        other=self.request(1,'other');wanted=self.request(1,'wanted');next_row=self.request(2,'wanted')
        self.apply([E('add',other)]);self.inject_candidate('wanted',1)
        result=self.probe.call(E('Race',str(self.path),(E('put',wanted),),(E('add',next_row),)))
        self.assertEqual(result[1][0],S('ConstraintAcceptedV1'))
        self.assertEqual(result[2],E('StoreRefused',S('conflict')))
        self.assertTrue(self.reference([E('put',wanted)]));self.assert_rows()
        self.apply([E('put',other)]);self.inject_candidate('wanted',1)
        result=self.probe.call(E('BucketRace',str(self.path),(E('put',wanted),),(E('add',next_row),)))
        self.assertEqual([item[0] for item in result[1:]],[S('ConstraintAcceptedV1'),S('ConstraintAcceptedV1')])
        self.assertTrue(self.reference([E('put',wanted)]))
        self.assertFalse(self.reference([E('add',next_row)]))
        rows=[decode(value) for key,value in self.audit().items() if key.startswith(b'requests/')]
        self.assertEqual([self.parts(row)[3][0] for row in rows],['wanted','wanted'])

    def test_same_snapshot_unique_absence_conflicts_and_disjoint_values_do_not(self):
        result=self.probe.call(E('Race',str(self.path),(E('add',self.request(1,'a')),),
                                 (E('add',self.request(2,'b')),)))
        self.assertEqual([item[0] for item in result[1:]],[S('ConstraintAcceptedV1'),S('ConstraintAcceptedV1')])
        self.assertTrue(self.reference([E('add',self.request(1,'a')),E('add',self.request(2,'b'))]))
        result=self.probe.call(E('Race',str(self.path),(E('add',self.request(3,'c')),),
                                 (E('add',self.request(4,'c')),)))
        self.assertEqual(result[1][0],S('ConstraintAcceptedV1'))
        self.assertEqual(result[2],E('StoreRefused',S('conflict')))
        self.assertTrue(self.reference([E('add',self.request(3,'c'))]));self.assert_rows()

    def test_generated_ordered_batches_match_full_reference_state(self):
        rng=random.Random(271044)
        for _ in range(100):
            current={r[0]:self.request(r[0],S('JsonNull') if r[1] is None else r[1])
                     for r in self.db.execute('SELECT rowid,id FROM requests')}
            mutations=[]
            for _ in range(rng.randrange(1,5)):
                ordinal=rng.randrange(-3,18)
                verb=rng.choice(['add','put','remove']) if ordinal in current else rng.choice(['add','put'])
                row=current[ordinal] if verb=='remove' else self.request(ordinal,rng.choice(['a','b','c','d',S('JsonNull')]))
                mutations.append(E(verb,row))
                if verb=='remove':current.pop(ordinal,None)
                else:current[ordinal]=row
            self.apply(mutations,page=1)

    def test_callers_transition_reads_are_retained_through_publication(self):
        original=self.request(1,'a');changed=self.request(1,'a','completed')
        self.apply([E('add',original)])
        key=b'requests/'+((1<<63)+1).to_bytes(8,'big')
        result=self.probe.call(E('GuardRace',str(self.path),(E('put',changed),),
            (E('add',self.request(2,'b')),),E('reads',E('point',key))))
        self.assertEqual(result[1][0],S('ConstraintAcceptedV1'))
        self.assertEqual(result[2],E('StoreRefused',S('conflict')))
        self.assertTrue(self.reference([E('put',changed)]));self.assert_rows()
        # Without that caller observation, the two independent constraint
        # operations are allowed. The gateway must supply its complete reads.
        result=self.probe.call(E('GuardRace',str(self.path),(E('put',original),),
            (E('add',self.request(2,'b')),),E('reads')))
        self.assertEqual(result[1][0],S('ConstraintAcceptedV1'))
        self.assertEqual(result[2][0],S('ProviderConstraintCommittedV1'))
        self.assertTrue(self.reference([E('put',original),E('add',self.request(2,'b'))]));self.assert_rows()

    def test_dropping_negative_reads_admits_a_duplicate_in_the_control(self):
        result=self.probe.call(E('WeakRace',str(self.path),(E('add',self.request(1,'same')),),
                                (E('add',self.request(2,'same')),)))
        self.assertEqual([item[0] for item in result[1:]],[S('ConstraintAcceptedV1'),S('ConstraintAcceptedV1')])
        rows=[decode(value) for key,value in self.audit().items() if key.startswith(b'requests/')]
        self.assertEqual([self.parts(row)[3][0] for row in rows],['same','same'])
        self.assertFalse(self.reference([E('add',self.request(1,'same')),E('add',self.request(2,'same'))]))

    def test_bad_page_and_transition_read_shapes_are_explicit_and_change_nothing(self):
        before=self.audit();mutations=(E('add',self.request(1)),)
        for page in (0,-1,1.5):
            self.assertEqual(self.probe.call(E('Apply',str(self.path),mutations,page,E('reads'))),
                             E('ProviderConstraintRefusedV1',S('page-size')))
        self.assertEqual(self.probe.call(E('Apply',str(self.path),mutations,2,S('missing'))),
                         E('ProviderConstraintRefusedV1',S('transition-reads')))
        self.assertEqual(self.audit(),before)


if __name__=='__main__':unittest.main()

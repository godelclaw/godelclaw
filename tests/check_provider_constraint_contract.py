#!/usr/bin/env python3
"""Couple checked constraint witnesses to real MeTTa commits and SQLite reads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=Path,required=True)
    parser.add_argument('--cetta-root',type=Path,required=True)
    parser.add_argument('--lean',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    names=['null_identity_has_no_conflict','remove_preserves_unique','write_preserves_unique',
           'accepted_plan_preserves_constraints','latest_overlay_write_shadows_history',
           'tombstone_is_not_an_absent_observation','invalid_intermediate_is_not_final_validation',
           'retained_negative_witness']
    namespace='Mettapedia.Machines.NativeProviderConstraints.'
    source=args.model.read_text()
    audit=source+'\n'+''.join('#print axioms '+namespace+name+'\n' for name in names)
    with tempfile.TemporaryDirectory(prefix='constraint-contract-') as directory:
        model=Path(directory)/'ConstraintContract.lean';model.write_text(audit)
        checked=subprocess.run([str(args.lean.resolve()),'-j1','-DwarningAsError=true',str(model)],
                               capture_output=True,text=True,timeout=120)
    if args.output:args.output.with_suffix('.lean.log').write_text(checked.stdout+checked.stderr)
    if checked.returncode:raise RuntimeError(checked.stdout+checked.stderr)
    for name in names:
        head="'"+namespace+name+"' "
        if checked.stdout.count(head)!=1:raise AssertionError(('missing declaration audit',name))
        if head+'does not depend on any axioms' not in checked.stdout:
            match=re.search(re.escape(head)+r'depends on axioms:\s*\[([^]]*)\]',checked.stdout)
            if not match or not {a.strip() for a in match.group(1).split(',')}<={'propext','Quot.sound','Classical.choice'}:
                raise AssertionError(('nonstandard proof assumptions',name))
    trace=next(line.split()[1:] for line in checked.stdout.splitlines() if line.startswith('PROVIDER_CONSTRAINT_TRACE '))
    os.environ['CETTA_VALUE_WORKER']=str(args.worker.resolve())
    os.environ['CETTA_CHANNEL_ROOT']=str(args.cetta_root.resolve())
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'tests'))
    from test_provider_constraint_store_cetta import ProviderConstraintTests,E,S
    native=[];sql=[]
    def state(answer):
        if answer[0] in (S('ConstraintAcceptedV1'),S('ProviderConstraintCommittedV1')):return 'accepted'
        if answer==E('StoreRefused',S('conflict')):return 'conflict'
        if answer[0]==S('ProviderConstraintViolationV1'):return 'duplicate'
        raise AssertionError(('unexpected outcome',answer))
    def fixture():
        case=ProviderConstraintTests();case.setUp();return case
    def capture(case,identity,ordinal):
        # SQL is the semantic reference for the captured negative range and
        # the destination point. It does not claim SQLite uses this protocol.
        bucket=None if identity==S('JsonNull') else case.db.execute(
            'SELECT rowid,* FROM requests WHERE id=? ORDER BY rowid',(case.parameter(identity),)).fetchall()
        point=case.db.execute('SELECT rowid,* FROM requests WHERE rowid=?',(ordinal,)).fetchall()
        return bucket,point
    for other in ('b','a',S('JsonNull')):
        case=fixture()
        try:
            identity=S('JsonNull') if other==S('JsonNull') else 'a'
            first=case.request(1,identity);second=case.request(2,other)
            snapshot=capture(case,other,2)
            result=case.probe.call(E('Race',str(case.path),(E('add',first),),(E('add',second),)))
            if other=='b':native.extend((state(result[1]),state(result[2])))
            elif other=='a':native.append(state(result[2]))
            first_sql=case.reference([E('add',first)])
            if not first_sql:raise AssertionError('first SQL insert failed')
            second_sql='conflict' if snapshot!=capture(case,other,2) else (
                'accepted' if case.reference([E('add',second)]) else 'duplicate')
            if other=='b':sql.extend(('accepted',second_sql))
            elif other=='a':
                sql.append(second_sql)
                duplicate=case.probe.call(E('Apply',str(case.path),(E('add',second),),1,E('reads')))
                native.append(state(duplicate))
                sql.append('accepted' if case.reference([E('add',second)]) else 'duplicate')
            else:
                native.append(state(result[2]));sql.append(second_sql)
            case.assert_rows()
        finally:
            if not case.doCleanups():raise AssertionError('fixture cleanup failed')
    if native!=trace or sql!=trace:raise AssertionError(('trace mismatch',trace,native,sql))
    programs=['src/provider_constraint_store_cetta.metta','src/provider_index_query_cetta.metta',
        'src/provider_index_store_cetta.metta','src/provider_indexes_cetta.metta','src/provider_store_cetta.metta',
        'tests/support/provider_constraint_probe.metta','tests/support/provider_index_probe.metta']
    receipt=dict(trace=trace,lean_declarations=len(names),program_sha256={name:digest(root/name) for name in programs},
        model_sha256=digest(args.model),worker_sha256=digest(args.worker),
        scope='canonical-row constraints and captured-view witnesses; input affinity, full interpreter/C/index/I/O refinement and application integration remain open')
    if args.output:args.output.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()

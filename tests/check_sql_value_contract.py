#!/usr/bin/env python3
"""Replay checked integer power values through the actual MeTTa formatter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import re


def digest(path):
    with path.open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',required=True,type=Path)
    parser.add_argument('--cetta-root',required=True,type=Path)
    parser.add_argument('--lean',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    declarations=['required_power_bounds','scaling_shift_bounds','normalization_preserves_scaled_value',
        'conversion_half_step','decimal_lookahead_rounds_half_up','required_power_values_are_normalized',
        'power_high_normalized','conversion_power_normalized']
    source=args.model.read_text()
    audit=source+'\n'+''.join(
        '#print axioms Mettapedia.Machines.NativeScalarText.'+name+'\n' for name in declarations)
    # Compile the exact source plus its audit, so cached project artifacts cannot
    # silently substitute a different model for the source identity below.
    with tempfile.TemporaryDirectory(prefix='scalar-contract-') as temporary:
        model=Path(temporary)/'ScalarContract.lean';model.write_text(audit)
        compiled=subprocess.run([str(args.lean.resolve()),'-j1','-DwarningAsError=true',str(model)],
                                capture_output=True,text=True,timeout=120)
    if args.output:
        args.output.with_suffix('.lean.log').write_text(compiled.stdout+compiled.stderr)
    if compiled.returncode:
        raise RuntimeError('\n'.join(line for line in (compiled.stdout+compiled.stderr).splitlines()
                                     if not line.startswith('NUMERIC_POWER ')))
    for name in declarations:
        prefix="'Mettapedia.Machines.NativeScalarText."+name+"' "
        lines=[line for line in compiled.stdout.splitlines() if line.startswith(prefix)]
        if len(lines)!=1:raise AssertionError(('missing declaration audit',name,lines))
        if 'does not depend on any axioms' not in lines[0]:
            axioms=re.search(re.escape(prefix)+r'depends on axioms:\s*\[([^]]*)\]',compiled.stdout)
            if not axioms:raise AssertionError(('unrecognized axiom audit',lines[0]))
            if not {axiom.strip() for axiom in axioms.group(1).split(',')}<={'propext','Quot.sound','Classical.choice'}:
                raise AssertionError(('nonstandard proof assumption',axioms.group(0)))
    expected=[]
    for line in compiled.stdout.splitlines():
        if line.startswith('NUMERIC_POWER '):
            _,power,value=line.split();expected.append((int(power),int(value)))
    if [p for p,_ in expected]!=list(range(-348,348)):raise AssertionError('model trace incomplete')
    sys.path.insert(0,str(args.cetta_root/'tools'))
    from space_service_client import E,BigInt
    from value_worker_client import Worker
    root=Path(__file__).resolve().parents[1]
    worker=Worker(args.worker.resolve(),root/'src/sql_value_cetta.metta','sql-value:call',language='petta')
    try:
        for power,value in expected:
            actual=worker.call(E('PowerHigh',power))
            if not isinstance(actual,BigInt) or int(actual.decimal)!=value:
                raise AssertionError(('power differs',power,actual,value))
    finally:worker.close()
    receipt=dict(power_values=len(expected),audited_declarations=len(declarations),
        program_sha256=digest(root/'src/sql_value_cetta.metta'),
        model_sha256=digest(args.model),worker_sha256=digest(args.worker),
        scope='checked integer helper/rounding laws and actual power trace; complete binary64/C refinement remains open')
    if args.output:args.output.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()

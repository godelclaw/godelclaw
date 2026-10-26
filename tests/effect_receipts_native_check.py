"""Scoped native receipt values, durable bytes, serial publication and view."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import mock

from effect_receipts_primitives2 import text
from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


HEADER = IMPORTS + '!(import! &self ./src/effect_receipts_cetta.metta)\n'
WIRE = '''
(= (rt:wire $value)
 (case $value
  (((ErFailure $class $message $detail) (JsonArray ("error" (str:symbol->string $class) $message)))
   ((Error $source $detail) (JsonArray ("native-error" (repr $source) (repr $detail))))
   ($other (JsonArray ("value" $other))))))
'''


def value(v):
    return '(json:parse-python %s)' % text(json.dumps(v, ensure_ascii=False))


def outcome(fn, *args):
    try:
        return ['value', fn(*args)]
    except Exception as exc:
        return ['error', type(exc).__name__, str(exc)]


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import effect_receipts as oracle
    cases = []
    for command in (['shell', 'generated-private-arg'], '["shell", "\\ud800"]',
                    '["shell", NaN]', 'shell arg', ['unknown', 'x'], [], [3],
                    ['read-file', 'fixture'], ['send', 'private-body']):
        cases.append(('(er:tool %s)' % value(command), outcome(oracle._tool_name, command)))
        for disposition in ('returned', 'withheld', 'deferred', 'failed', 'unlisted'):
            for occurrence in (None, '', 0, 'fixture-occurrence'):
                args = (3, disposition, command, 'result🙂\0', 1704067200.125, occurrence)
                expression = '(er:event %s)' % ' '.join(value(x) for x in args)
                cases.append((expression, outcome(oracle.public_event, *args)))
    for event in ({}, {'tool':'shell'}, {'tool':'shell','disposition':'returned'},
        {'tool':['x',True],'disposition':None,'receipt_id':3},
        {'tool':'shell','disposition':'returned','receipt_id':'fixture'},None,[],1,'x'):
        cases.append(('(er:public-text %s)'%value(event),outcome(oracle.public_text,event)))
    huge=int('9'*4300)+1
    huge_expr='(+ (json:parse-python %s) 1)'%q('9'*4300)
    cases.append(('(er:event 3 %s (JsonArray ("unknown")) "r" 0 JsonNull)'%huge_expr,
                  outcome(oracle.public_event,3,huge,['unknown'],'r',0)))
    for args in ((7,'returned',['shell','private-'*1001],'🙂'*6001,1704067200.125,None),
                 (7,'returned','["shell", "fixture"]','result',1704067200.125,'occ🙂'*6001)):
        cases.append(('(er:event %s)'%' '.join(value(x) for x in args),outcome(oracle.public_event,*args)))
    identity_args=(7,'d'*81,['shell','fixture'],'🙂'*6001)
    cases.append(('(er:receipt-id %s)'%' '.join(value(x) for x in identity_args),
                  outcome(oracle._receipt_id,*identity_args)))
    source = HEADER + WIRE + ''.join(emit('(rt:wire %s)' % expr) for expr, _ in cases)
    actual = rows(c.run('receipt-projection', source))
    expected = [item for _, item in cases]
    c.save('projection-comparison.json', {'actual': actual, 'expected': expected})
    assert actual == expected, next(((i,a,e) for i,(a,e) in enumerate(zip(actual,expected)) if a!=e), (len(actual),len(expected)))

    # Serial guard, in-flight, failed sink retry and oldest-first bounds. The
    # native fixture replaces only the sink crossing, not persistence/cleaning.
    source = HEADER + WIRE + '''
!(match &self (= (er:sink-add $text $epoch) $body)
  (remove-atom &self (= (er:sink-add $text $epoch) $body)))
!(bind! &rt-mode (new-state normal))
!(bind! &rt-calls (new-state 0))
!(bind! &rt-nested (new-state -1))
(= (er:sink-add $text $epoch)
 (let $_ (change-state! &rt-calls (+ (get-state &rt-calls) 1))
  (case (get-state &rt-mode)
   ((failure (ErFailure RuntimeError "fixture sink failed" ()))
    (error (Error fixture thrown))
    (hard (/ 1 0))
    (recursive (let $_ (change-state! &rt-nested
       (er:publish 3 "returned" (JsonArray ("shell" "nested")) "r" 0 "nested")) 1))
    ($other 1)))))
!(change-state! &er-captured ((3 7)))
'''
    publish = '(er:publish 3 "returned" (JsonArray ("shell" "private")) "result" 0 %s)'
    expected = []
    for mode, occurrence, result in [('recursive','one',1), ('normal','two',1),
            ('normal','one',0), ('failure','retry',0), ('normal','retry',1),
            ('error','after-error',0), ('normal','after-error',1),
            ('hard','after-hard',0), ('normal','after-hard',1)]:
        source += '!(change-state! &rt-mode %s)\n' % mode
        source += emit('(rt:wire %s)' % (publish % q(occurrence)))
        expected.append(['value',result])
        source += emit('(JsonArray ((get-state &er-reporting) (JsonArray (get-state &er-in-flight))))')
        expected.append([False, []])
    source += emit('(JsonArray ((get-state &rt-calls) (get-state &rt-nested) (JsonArray (get-state &er-published))))')
    raw = c.run('serial-publication-guard', source, {'METTACLAW_PUBLIC_TOOL_RECEIPTS': '1'})
    actual = rows(raw)
    c.save('serial-guard-results.json', {'actual':actual,'expected_prefix':expected})
    assert actual[:-1] == expected, actual
    assert actual[-1][0:2] == [8,0] and len(actual[-1][2]) == 5, actual[-1]

    # Exact append bytes and unchanged Python reader/view. Clocks/occurrences
    # here are generated fixture inputs, not a native clock qualification.
    expected_lines = []
    source = HEADER + WIRE
    for i, command in enumerate((['shell', 'fixture'], '["read-file", "fixture"]',
                                  ['shell', '🙂\0'], 'plain command')):
        result = 'result🙂\0' if i != 2 else '🙂' * 6001
        args = (i,'returned',command,result,'occ-%d'%i,1704067200.125+i)
        source += emit('(rt:wire (er:commit %s))' % ' '.join(value(x) for x in args))
        entry = {'schema':1,'recorded_at':args[-1],'turn':i,'occurrence':args[-2],
                 'disposition':'returned','command':oracle._text(command),'result':oracle._text(result)}
        expected_lines.append(json.dumps(entry,ensure_ascii=False,sort_keys=True)+'\n')
    source += emit('(rt:wire (er:view 18))')
    raw = c.run('receipt-append-and-view', source)
    path = c.directory / 'receipts.jsonl'
    c.save('receipt-file-bytes.json', {'hex':path.read_bytes().hex() if path.exists() else None,
                                    'expected_hex':''.join(expected_lines).encode().hex()})
    actual = rows(raw)
    with mock.patch.dict(os.environ,c.environment(),clear=True):
        expected_view = oracle.view()
    c.save('receipt-append-results.json', {'actual':actual,'expected_view':expected_view})
    assert path.read_bytes() == ''.join(expected_lines).encode()
    assert actual[:4] == [['value',1]]*4 and actual[4] == ['value',expected_view], actual[:4]
    suffix=HEADER+WIRE+emit('(rt:wire (er:suffix 9 withheld (quote ((shell "fixture") (read-file "fixture"))) %s))'%text('🙂'*241))
    raw=c.run('native-suffix',suffix)
    after=path.read_bytes();entry=json.loads(after.splitlines()[-1])
    c.save('suffix.json',{'actual':rows(raw),'entry':entry,'receipt_hex':after.hex()})
    assert rows(raw)==[['value',1]] and entry['reason']==oracle._text('🙂'*241,240)
    assert entry['commands']==str([['shell','fixture'],['read-file','fixture']])
    assert after==''.join(expected_lines).encode()+(json.dumps(entry,ensure_ascii=False,sort_keys=True)+'\n').encode()
    c.save('result.json', {'projection_cases':len(cases), 'serial_guard_cases':9,
                          'receipt_lines':5, 'view_matched':True,'suffix':True})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();limit()
    assert sys.version_info[:3] == (3,12,13)
    args.evidence.mkdir(parents=True,exist_ok=False)
    (args.evidence/'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-native-') as temporary:
        c=Qualification(args.evidence.resolve(),Path(temporary))
        with c.phase('native-receipts'):
            c.snapshot_sources('src/effect_receipts_cetta.metta','src/effect_receipts_plugin.py',
                'src/public_worklog_cetta.metta','src/effect_receipt_values_cetta.metta',
                'src/effect_receipt_io_cetta.metta')
            check(c)


if __name__=='__main__':main()

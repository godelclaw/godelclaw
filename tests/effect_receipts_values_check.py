"""Oracle qualification of the unactivated project05 Python-value helpers."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unicodedata

from effect_receipts_primitives2 import text
from effect_receipts_support import IMPORTS, Qualification, emit, limit, rows


WIRE = '''
(= (val:wire $value)
 (case $value
  (((ErFailure $class $message (EncodingRange $start $end))
     (JsonArray ("error" (str:symbol->string $class) $message (JsonArray ($start $end)))))
   ((ErFailure $class $message $detail)
     (JsonArray ("error" (str:symbol->string $class) $message JsonNull)))
   ($other (if (== (get-type $other) String)
       (let $points (str:python-codepoints $other) (JsonArray ("text" (JsonArray $points))))
       (JsonArray ("number" (json:stringify-python $other))))))))
'''


def outcome(function, value):
    try:
        result = function(value)
        if isinstance(result, str):
            return ['text', [ord(c) for c in result]]
        return ['number', json.dumps(result)]
    except Exception as exc:
        return ['error', type(exc).__name__, str(exc),
                [exc.start, exc.end] if isinstance(exc, UnicodeEncodeError) else None]


def check(c):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import public_worklog
    values = [None, True, False, 0, 1, -8, 3.9, -0.0, 1e-7, float('nan'), float('inf'),
              [], {}, ['shell', 'arg', 3, True], {'z': [1, None], 'a': '日🙂\0'},
              '', 'simple', "one'quote", 'one"quote', 'both\'"', '\t\n\r\v\f\x1c\x85',
              '\u00a0\u200d\ue000\u0378\u2028\u2029', '\ud800\udfff🙂e\u0301',
              '🙂' * 6001, 'A' * 5998 + '\ud800\udfff🙂']
    cases = []
    for value in values:
        # Raw Python text retains adjacent surrogate code points. ASCII escape
        # pairs intentionally combine on json.loads and would change fixtures.
        encoded = text(json.dumps(value, ensure_ascii=False))
        expression = '(json:parse-python %s)' % encoded
        for name, function in [('str', str), ('repr', repr)]:
            cases.append(('(erv:%s %s)' % (name, expression), outcome(function, value)))
        for size in (80, 240, 6000):
            function = lambda x, size=size: str(x) if len(str(x)) <= size else str(x)[:size-1] + '…'
            cases.append(('(erv:text %s %d)' % (expression, size), outcome(function, value)))
        cases.append(('(erv:clean (erv:str %s))' % expression, outcome(public_worklog.clean, value)))
    texts = ['', 'A🙂\0B', 'A\ud800B', 'A\ud800\udfffB\ud800', '\ud800\ud801\udc00',
             '🙂e\u0301\udfff', 'x' * 6000 + '\ud800']
    for value in texts:
        cases.append(('(erv:encode %s)' % text(value), outcome(lambda x: x.encode('utf-8').decode('utf-8'), value)))
    integers = [0, 1, -1, 3.9, -3.9, True, False, None, [], {}, float('nan'), float('inf'),
                '', ' ', ' +12 ', ' -٠_١ ', '1__2', '_1', '1_', '1x', '\ud800', 'x' * 300,
                '9' * 4301, '0' * 4301]
    integers += [chr(cp) for cp in range(0x110000) if unicodedata.category(chr(cp)) == 'Nd']
    for value in integers:
        cases.append(('(erv:int (json:parse-python %s))' % text(json.dumps(value)), outcome(int, value)))
    for expr, value in [('shell', 'shell'), ('(quote (shell "arg" 3 True))', ['shell', 'arg', 3, 'true']),
                        ('True', 'true'), ('(json:parse-python "[1, 2]")', ['JsonArray', [1, 2]])]:
        cases.append(('(erv:str (erv:cross %s))' % expr, outcome(str, value)))
    c.save('value-fixtures.json', [{'expression': expr, 'expected': expected} for expr, expected in cases])
    program = IMPORTS + '!(import! &self ./src/effect_receipt_values_cetta.metta)\n' + WIRE
    program += ''.join(emit('(val:wire %s)' % expr) for expr, _ in cases)
    actual = rows(c.run('python-values', program))
    expected = [v for _, v in cases]
    c.save('value-results.json', {'expected': expected, 'actual': actual})
    mismatches = [{'index': i, 'expression': cases[i][0], 'expected': e, 'actual': a}
                  for i, (a, e) in enumerate(zip(actual, expected)) if a != e]
    c.save('value-mismatches.json', {'expected_count': len(expected), 'actual_count': len(actual), 'mismatches': mismatches})
    assert len(actual) == len(expected) and not mismatches, (len(actual), len(expected), mismatches[:4])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='effect05-values-') as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        with c.phase('native-values'):
            check(c)


if __name__ == '__main__':
    main()

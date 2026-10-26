"""Unactivated native IO/clock helpers on disposable paths, real fsync calls."""
import argparse
import datetime
import json
from pathlib import Path, PurePosixPath
import random
import sys
import tempfile
from zoneinfo import ZoneInfo

from effect_receipts_primitives2 import text
from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows
from effect_receipts_values_check import WIRE, outcome


def check(c):
    cases = []
    for path in ('', '.', '..', '/', '//', '///', '//a/b/', 'a//./b/../c', 'a/..', 'a/', './x', '日/🙂'):
        cases.append(('(erio:path %s)' % q(path), outcome(lambda x: str(PurePosixPath(x)), path)))
        cases.append(('(erio:parent %s)' % q(path), outcome(lambda x: str(PurePosixPath(x).parent), path)))
    stamps = [-0.1, -1.1, -1e-7, -0.0000005, 0, 0.9999999, 1700000000.9999998,
              -62135596800, 253402300799, 1711846800, 1729990800, float('nan'), float('inf')]
    rng = random.Random(892)
    stamps += [rng.uniform(-2208988800, 4102444800) for _ in range(64)]
    for stamp in stamps:
        expression = '(json:parse-python %s)' % q(json.dumps(stamp))
        for zone in ('utc', 'local'):
            tz = datetime.timezone.utc if zone == 'utc' else ZoneInfo('Europe/Paris')
            cases.append(('(erio:iso %s "%s")' % (expression, zone),
                          outcome(lambda x, tz=tz: datetime.datetime.fromtimestamp(x, tz).isoformat(timespec='seconds'), stamp)))
    source = IMPORTS + '!(import! &self ./src/effect_receipt_io_cetta.metta)\n' + WIRE
    source += ''.join(emit('(val:wire %s)' % expression) for expression, _ in cases)
    actual = rows(c.run('paths-and-iso', source))
    expected = [expected for _, expected in cases]
    mismatches = [{'index': i, 'expression': cases[i][0], 'actual': a, 'expected': e}
                  for i, (a, e) in enumerate(zip(actual, expected)) if a != e]
    c.save('paths-and-iso-results.json', {'actual': actual, 'expected': expected, 'mismatches': mismatches})
    assert len(actual) == len(expected) and not mismatches, (len(actual), len(expected), mismatches[:3])
    receipt, temporary, state, lock, note = [c.directory / name for name in ('receipts', 'state.tmp.fixture', 'state.json', 'lock', 'notes')]
    source = IMPORTS + '!(import! &self ./src/effect_receipt_io_cetta.metta)\n' + WIRE
    source += '(= (iot:body $path) (erio:append $path "note\\n" False))\n'
    source += '(= (iot:failure) (erv:fail ValueError "fixture failure"))\n'
    source += '''
(= (iot:locked $path $note $fail)
 (let $owner (erio:open $path ("append" "create") 438)
  (if (erv:failed? $owner) $owner
   (let* (($admitted (erio:lock $owner))
          ($result (if (erv:failed? $admitted) $admitted
            (erio:single (collapse (catch (if $fail (iot:failure) (iot:body $note))))))))
    (erio:finish $owner True $result)))))
'''
    expressions = [
        '(erio:append %s "first\\n" True)' % q(receipt),
        '(erio:append %s "second\\n" True)' % q(receipt),
        '(erio:write-state %s %s "{\\\"fixture\\\": true}")' % (q(temporary), q(state)),
        '(iot:locked %s %s False)' % (q(lock), q(note)),
        '(iot:locked %s %s True)' % (q(lock), q(note)),
        '(erio:append %s "unwritten" True)' % q(c.directory / 'missing' / 'receipt'),
    ]
    for expression in expressions:
        source += emit('(val:wire %s)' % expression)
    raw = c.run('real-owned-io', source)
    c.save('real-io-file-effects.json', {
        str(p.name): p.read_bytes().hex() if p.exists() else None
        for p in (receipt, temporary, state, lock, note)})
    actual = rows(raw)
    c.save('real-io-results.json', {'actual': actual,
        'files': {str(p.name): p.read_bytes().hex() if p.exists() else None for p in (receipt, temporary, state, lock, note)}})
    assert actual[:4] == [['number', '1']] * 4, actual
    assert actual[4] == ['error', 'ValueError', 'fixture failure', None], actual
    assert actual[5][0:2] == ['error', 'FileNotFoundError'], actual
    assert receipt.read_bytes() == b'first\nsecond\n' and note.read_bytes() == b'note\n'
    assert state.read_bytes() == b'{"fixture": true}' and not temporary.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='effect05-io-') as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        with c.phase('native-io'):
            check(c)


if __name__ == '__main__':
    main()

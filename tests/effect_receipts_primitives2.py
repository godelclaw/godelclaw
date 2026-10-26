"""Lossless Python-JSON/text capability gate; no runtime activation or py-call."""
import argparse
import json
from pathlib import Path
import random
import sys
import tempfile

from effect_receipts_support import IMPORTS, Qualification, emit, limit, q, rows


def text(value):
    return '(str:from-python-codepoints (%s))' % ' '.join(str(ord(c)) for c in value)


HELPERS = '''
(= (p2:outcome $value)
 (case $value
  (((JsonPythonErrorV1 JSONDecodeError $message $msg $pos $line $col)
     (JsonArray ("error" "JSONDecodeError" $message $msg $pos $line $col)))
   ((JsonPythonErrorV1 ValueError $message)
     (JsonArray ("error" "ValueError" $message)))
   ((JsonFailureV1 $phase $reason $source $detail)
     (JsonArray ("native-failure" (str:symbol->string $phase) (str:symbol->string $reason))))
   ($other (JsonArray ("value" (json:stringify-python $other)))))))
'''


def oracle(doc):
    try:
        value = json.loads(doc)
        return ["value", json.dumps(value)]
    except json.JSONDecodeError as exc:
        return ["error", "JSONDecodeError", str(exc), exc.msg, exc.pos, exc.lineno, exc.colno]
    except ValueError as exc:
        return ["error", "ValueError", str(exc)]


def gate(c):
    documents = ['["shell", "\\ud800"]', '["shell", NaN]',
        '{"enabled": true, "seq": 4, "epoch": 7, "extra": "\\ud800"}',
        '{"enabled": false, "seq": 0, "epoch": 0, "extra": NaN}',
        '"\\ud800"', '"\\udfff"', '"\\ud800\\udc00"',
        '"\\ud800\\ud801\\udc00\\udfff"', '[NaN, Infinity, -Infinity]',
        '{"a": 1, "b": 2, "a": 3}', '{"a": 1, "\\u0061": 2}',
        '[]', '{}', 'null', 'true', 'false', '-0.0', '1e-7', '5e-324',
        '1e400', '-1e400', '9' * 4300, '9' * 4301, '-' + '9' * 4301,
        '', ' ', '{', '[1,]', '{"a":}', 'true false', '01', '"bad\\x"',
        '"\\ud80x"', '"unclosed', '"raw\x00control"', '[\n 1,\n bad]',
        '["🙂",\n "日",]', '"\ud800"', '"A\udfffB"']
    rng = random.Random(505892)
    for _ in range(96):
        value = {"first": rng.randrange(-10**18, 10**18),
                 "text": ''.join(chr(rng.choice([0, 10, 0x301, 0x65e5, 0x1f642, 0xd800, 0xdfff])) for _ in range(8)),
                 "values": [rng.random(), None, bool(rng.randrange(2))]}
        documents.append(json.dumps(value, ensure_ascii=bool(rng.randrange(2))))
    expected = [oracle(doc) for doc in documents]
    c.save('json-python-fixtures.json', {"documents": documents, "expected": expected})
    program = IMPORTS + HELPERS + ''.join(emit('(p2:outcome (json:parse-python %s))' % text(doc)) for doc in documents)
    actual = rows(c.run('python-json-witnesses-and-errors', program))
    c.save('json-python-results.json', {"actual": actual, "expected": expected})
    assert actual == expected, next(((i, documents[i], a, e) for i, (a, e) in enumerate(zip(actual, expected)) if a != e), (len(actual), len(expected)))
    values = ['', '\n', 'A\0🙂e\u0301B', '\ud800', '\udfff',
              '\ud800\udc00', '\ud800\ud801\udc00\udfff',
              '🙂' * 6001 + '\ud800', 'A' * 5998 + '\ud800\udfff🙂']
    program = IMPORTS
    expected = []
    for value in values:
        points = [ord(c) for c in value]
        # Integer observations remain safe even for surrogatepass text.
        program += '!(let $value %s (let $points (str:python-codepoints $value) (println! (Wire (json:stringify-python (JsonArray $points))))))\n' % text(value)
        expected.append(points)
        for options, kwargs in [('()', {}), ('((ensure-ascii False))', {'ensure_ascii': False})]:
            program += '!(let $value %s (let $wire (json:stringify-python-with $value %s) (let $points (str:python-codepoints $wire) (println! (Wire (json:stringify-python (JsonArray $points)))))))\n' % (text(value), options)
            expected.append([ord(c) for c in json.dumps(value, **kwargs)])
    actual = rows(c.run('python-codepoints-and-stringify', program))
    c.save('python-text-results.json', {"actual": actual, "expected": expected})
    assert actual == expected, (len(actual), len(expected), next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b), None))
    invalid = [b'\xff', b'\xed\xa0\x80', b'A\xf0\x9f', b'"\xed\xbf\xbf"']
    program = IMPORTS + HELPERS
    for index, data in enumerate(invalid):
        path = c.directory / ('invalid-%d' % index)
        path.write_bytes(data)
        program += '!(let ("ok" $raw) (fs:read-text-result %s 0 "raw") (println! (Wire (json:stringify-python (p2:outcome (json:parse-python $raw))))))\n' % q(path)
    actual = rows(c.run('invalid-utf8-remains-invalid', program))
    expected = []
    for data in invalid:
        try:
            decoded = data.decode('utf-8', 'surrogatepass')
        except UnicodeDecodeError:
            expected.append(["native-failure", "JsonParseV1", "JsonInvalidUtf8V1"])
        else:
            expected.append(oracle(decoded))
    c.save('invalid-byte-results.json', {"inputs_hex": [v.hex() for v in invalid], "actual": actual, "expected": expected})
    assert actual == expected, actual
    # Files use strict text decoding BEFORE JSON parsing, so raw surrogatepass
    # bytes are not mistaken for escaped surrogate JSON strings in state.json.
    program = IMPORTS
    for index in range(len(invalid)):
        program += '!(let ("ok" $raw) (fs:read-text-result %s 0 "raw") (println! (StrictFileUtf8 (str:utf8-error $raw))))\n' % q(c.directory / ('invalid-%d' % index))
    output = c.run('strict-state-file-utf8-gate', program)
    assert output.count('(StrUtf8ErrorV1 ') == len(invalid), output
    c.save('gate.json', {"status": "CAPABILITY_GATE_PASSED", "python_json_cases": len(documents),
                          "python_text_observations": len(values) * 3,
                          "invalid_byte_cases": len(invalid), "runtime_activated": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    assert sys.version_info[:3] == (3, 12, 13), sys.version
    limit()
    args.evidence.mkdir(parents=True, exist_ok=False)
    (args.evidence / 'runner.py').write_bytes(Path(__file__).read_bytes())
    with tempfile.TemporaryDirectory(prefix='effect05-python-primitives-') as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        with c.phase('python-primitives-gate'):
            gate(c)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Compile the recovery model and compare its finite cases with native fixtures."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--lean', default='lean')
    parser.add_argument('--toolchain')
    parser.add_argument('--update', action='store_true')
    args = parser.parse_args()
    model = args.model.resolve()
    command = [args.lean] + (['+' + args.toolchain] if args.toolchain else []) + [str(model)]
    result = subprocess.run(command, cwd=model.parent, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        return result.returncode
    cases = []
    for line in result.stdout.splitlines():
        if line.startswith('RECOVERY_CASE '):
            _, request, call, owner, answer, expected = line.split()
            cases.append(dict(request=request, call=call, same_owner=owner == 'true',
                              same_answer=answer == 'true', expected=expected))
    keys = ('request', 'call', 'same_owner', 'same_answer')
    if len(cases) != 36 or len({tuple(c[k] for k in keys) for c in cases}) != 36:
        raise ValueError('the model must emit every receipt observation exactly once')
    path = Path(__file__).resolve().parents[1] / 'tests/fixtures/completion_recovery_cases.json'
    if args.update:
        path.write_text(json.dumps(cases, indent=2) + '\n')
    elif json.loads(path.read_text()) != cases:
        raise ValueError('native fixture differs from the compiled recovery model')
    print('RECOVERY_MODEL_OK: 36 compiled cases match the native query fixture')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate the small executable model from its elaborated .module rules."""
import argparse
import json
from pathlib import Path
import pprint

from retry_wake_compiler import ROOT, read_module, short


def artifacts(model):
    transitions = model["transitions"]
    table = {(short(r), short(c)): short(v) for r, c, v, _ in transitions}
    python = ('"""Generated from verification/RetryWake.module; do not edit."""\n'
              'MODULE_SHA256 = ' + repr(model['module_sha256']) + '\n'
              'ACTIVE = ' + repr({short(k): v for k, v in model['active'].items()}) + '\n'
              'TERMINAL = ' + repr({short(k): v for k, v in model['terminal'].items()}) + '\n'
              'TRANSITIONS = ' + pprint.pformat(table, sort_dicts=True) + '\n\n'
              'def reconcile(request, call):\n'
              '    """Return the model result, or None for an unmodelled state."""\n'
              '    return TRANSITIONS.get((request, call))\n')
    metta = '; Generated from RetryWake.module; every head is disjoint.\n'
    metta += '; No default transition; gc:decide reports unmodelled states explicitly.\n'
    metta += f'(= (gc:module-digest) "{model["module_sha256"]}")\n'
    for request, call, result, rule in transitions:
        metta += f'(= (gc:reconcile {short(request)} {short(call)}) {short(result)})\n'
    for name, states in [('request', model['requests']), ('call', model['calls'])]:
        guard = 'False'
        for state in reversed(states):
            guard = f'(or (== $state {short(state)}) {guard})'
        metta += f'(= (gc:known-{name} $state) {guard})\n'
    metta += ('(= (gc:decide $request $call) '
              '(if (and (gc:known-request $request) (gc:known-call $call)) '
              '(GcModelDecisionV2 $request $call (gc:reconcile $request $call)) '
              '(GcModelInapplicableV2 $request $call)))\n')
    for state, value in model['active'].items():
        metta += f'(= (gc:active {short(state)}) {"active" if value else "inactive"})\n'
    for state, value in model['terminal'].items():
        metta += f'(= (gc:terminal {short(state)}) {"terminal" if value else "pending"})\n'
    return {ROOT / 'src/retry_wake_model.py': python,
            ROOT / 'verification/RetryWakeModel.metta': metta,
            ROOT / 'verification/RetryWake.model.json': json.dumps(model, sort_keys=True, indent=2) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cetta', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    model = read_module(args.cetta.resolve(), ROOT/'verification/RetryWake.module', args.output)
    for path, body in artifacts(model).items():
        if args.check:
            if not path.is_file() or path.read_text() != body:
                raise ValueError('generated model is stale: ' + path.name)
        else:
            path.write_text(body)
    print(json.dumps({'transitions': len(model['transitions']), 'module_sha256': model['module_sha256'], 'checked': args.check}))


if __name__ == '__main__': main()

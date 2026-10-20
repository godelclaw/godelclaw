#!/usr/bin/env python3
"""Authenticate the small HOL model's exact sequents and test finite correspondence.

This is not a refinement proof of the JSON parser, command broker, or native
kernel. No provider request or command execution occurs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import model_commentary


def tokens(text):
    # Retain quoted strings as tokens, so whitespace inside names is significant.
    pattern = r'\s*("(?:[^"\\]|\\.)*"|[()]|[^\s()"]+)'
    result, offset = [], 0
    while offset < len(text):
        if not text[offset:].strip():
            break
        match = re.match(pattern, text[offset:])
        if not match:
            raise ValueError('invalid theorem rendering')
        result.append(match.group(1))
        offset += match.end()
    return result


def expected_result():
    commentary, commands, boolean = '(V "Commentary")', '(V "Commands")', '(T "bool")'
    def arrow(a, b): return f'(T "fun" {a} {b})'
    def var(name, ty): return f'(v {json.dumps(name)} {ty})'
    def app(f, x): return f'(A {f} {x})'
    def lam(x, body): return f'(L {x} {body})'
    def equal(a, b):
        eq = f'(c "=" {arrow(commands, arrow(commands, boolean))})'
        return app(app(eq, a), b)
    selector_type = arrow(commentary, arrow(commands, commands))
    ignored, selected = var('ignored', commentary), var('selected', commands)
    selector = lam(ignored, lam(selected, selected))
    pick = var('pick', selector_type)
    packet = var('packet', arrow(selector_type, commands))
    dispatcher = lam(packet, app(packet, selector))
    batch = var('command-batch', commands)
    def dispatch(note):
        envelope = lam(pick, app(app(pick, var(note, commentary)), batch))
        return app(dispatcher, envelope)
    selection = equal(dispatch('public-note'), batch)
    independence = equal(dispatch('public-note'), dispatch('other-note'))
    return ('(CommentaryBoundary '
            f'(Selection (thm () {selection}) (Axioms ())) '
            f'(Noninterference (thm () {independence}) (Axioms ())))')


def authenticate(output):
    actual = tokens(output)
    # PeTTa prints the import result before the one authenticated result.
    if actual[:1] == ['true']:
        actual = actual[1:]
    if actual != tokens(expected_result()):
        raise AssertionError('exact model conclusion, hypotheses or axioms differ')


def correspondence():
    notes = ['', 'ordinary public note', '((shell "never execute"))',
             'private-looking prose is still authored output', 'ž 🐈\nnext',
             '{"commands":"((send \\"not authority\\"))"}']
    batches = ['()', '((nop))', '((help "query"))']
    checked = 0
    with patch.dict(os.environ, METTACLAW_MODEL_COMMENTARY='1'):
        for commands in batches:
            for note in notes:
                plan = SimpleNamespace(kind='command', commentary=None)
                envelope = json.dumps({'commentary': note, 'commands': commands})
                assert model_commentary.finish(plan, envelope, 'stop') == commands
                checked += 1
                for reason in ('length', 'refusal', 'content_filter', 'tool_calls'):
                    try:
                        model_commentary.finish(plan, envelope, reason)
                    except model_commentary.OutputError:
                        checked += 1
                    else:
                        raise AssertionError('unfinished output accepted')
                try:
                    model_commentary.finish(plan, envelope + ' trailing', 'stop')
                except model_commentary.OutputError:
                    checked += 1
                else:
                    raise AssertionError('invalid envelope accepted')
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cetta', required=True)
    args = parser.parse_args()
    model = ROOT / 'verification/CommentaryBoundary.metta'
    run = subprocess.run([args.cetta, '--lang', 'petta', '--quiet', str(model)],
                         cwd=ROOT, capture_output=True, text=True, timeout=30)
    if run.returncode:
        raise RuntimeError(run.stderr)
    authenticate(run.stdout)
    # Negative controls ensure result presence alone is insufficient.
    rejected = 0
    for forged in (run.stdout.replace('command-batch', 'different-command'),
                   run.stdout.replace('(Axioms ())', '(Axioms (untrusted))'),
                   run.stdout.replace('(thm ()', '(thm (extra-hypothesis)')):
        try:
            authenticate(forged)
        except AssertionError:
            rejected += 1
        else:
            raise AssertionError('forged result authenticated')
    print(json.dumps({'model_sha256': hashlib.sha256(model.read_bytes()).hexdigest(),
                      'implementation_sha256': hashlib.sha256(
                          (ROOT / 'src/model_commentary.py').read_bytes()).hexdigest(),
                      'exact_sequents_checked': 2, 'hypotheses': 0,
                      'axiom_dependencies': 0, 'forged_results_rejected': rejected,
                      'finite_correspondence_cases': correspondence(),
                      'refinement_proof': False}, sort_keys=True))


if __name__ == '__main__':
    main()

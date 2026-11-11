#!/usr/bin/env python3
"""Inventory potential foreign runtime boundaries; never infer a live-turn count.

Tests and vendored comparisons stay useful. A native milestone requires both
this static gate and Python-disabled functional qualification of the release.
"""
import argparse
from collections import Counter
import fnmatch
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOKENS = re.compile(r';[^\n]*|"(?:\\.|[^"\\])*"|[()]|[^\s();]+')
BRIDGES = {'py-call', 'py-dot', 'py-eval', 'py:import', 'py:call'}


def boundaries(text):
    tokens = [m for m in TOKENS.finditer(text) if not m.group().startswith(';')]
    imports, calls = [], []
    for i, match in enumerate(tokens):
        token = match.group()
        if i == 0 or tokens[i - 1].group() != '(':
            continue
        if token == 'import!' and i + 2 < len(tokens):
            target = tokens[i + 2].group()
            if target.startswith('"'):
                target = json.loads(target)
            dynamic = target.startswith('$') or (target == '(' and i + 3 < len(tokens)
                                                 and tokens[i + 3].group() != 'library')
            if target.endswith('.py') or dynamic:
                imports.append(dict(target=target, dynamic=dynamic,
                                    line=text.count('\n', 0, match.start()) + 1))
        if token in BRIDGES or token.startswith('py:'):
            at = i + 2 if i + 1 < len(tokens) and tokens[i + 1].group() == '(' else i + 1
            target = tokens[at].group() if at < len(tokens) else '<missing>'
            calls.append(dict(bridge=token, target=target,
                              line=text.count('\n', 0, match.start()) + 1))
    return imports, calls


def inventory(root):
    selection = json.loads((root / 'config/runtime-files.json').read_text())
    files = selection['files'] if isinstance(selection, dict) else selection
    plan = json.loads((root / 'config/native-migration.json').read_text())
    references = plan['reference_patterns']
    reference = lambda path: any(fnmatch.fnmatchcase(path, p) for p in references)
    python, unknown, overlaps, imports, calls = [], [], [], [], []
    for name in files:
        if name.endswith('.py'):
            if reference(name):
                python.append(dict(file=name, package='retained-reference'))
            else:
                owners = [p['id'] for p in plan['packages'] if name in p['python_files']]
                if not owners:
                    unknown.append(name)
                if len(owners) > 1:
                    overlaps.append(name)
                python.append(dict(file=name, package=owners[0] if len(owners) == 1 else 'unassigned'))
        elif name.endswith('.metta') and not reference(name):
            found, crossed = boundaries((root / name).read_text())
            imports.extend(dict(file=name, **item) for item in found)
            calls.extend(dict(file=name, **item) for item in crossed)
    launchers = []
    for name in plan['launchers']:
        for line, text in enumerate((root / name).read_text().splitlines(), 1):
            if not text.lstrip().startswith('#') and re.search(r'python3|METTACLAW_MODE_PYTHON', text):
                launchers.append(dict(file=name, line=line))
    return dict(schema=1, milestone=plan['milestone'],
                measurement='potential selected-source boundaries; includes inactive alternatives',
                python_files=python, package_counts=dict(Counter(p['package'] for p in python)),
                python_imports=imports, foreign_calls=calls, python_launcher_sites=launchers,
                unassigned=unknown, overlapping=overlaps,
                statically_closed=not (imports or calls or launchers or unknown or overlaps or
                                       any(p['package'] != 'retained-reference' for p in python)),
                runtime_python_independence_proven=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-closed', action='store_true')
    args = parser.parse_args()
    result = inventory(args.root)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(python_files=len(result['python_files']),
                         package_counts=result['package_counts'],
                         python_imports=len(result['python_imports']),
                         foreign_calls=len(result['foreign_calls']),
                         python_launcher_sites=len(result['python_launcher_sites']),
                         unassigned=result['unassigned'], overlapping=result['overlapping'],
                         statically_closed=result['statically_closed'])))
    return int(bool(result['unassigned'] or result['overlapping'] or
                    args.require_closed and not result['statically_closed']))


if __name__ == '__main__':
    raise SystemExit(main())

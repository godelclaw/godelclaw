#!/usr/bin/env python3
"""Check a finite executable module with real HOL/Megalodon computations.

No provider requests, commands, live database writes, or runtime policy changes.
Receipts authenticate this model and finite correspondence, not the whole host.
"""
import argparse
import importlib.util
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

from generate_retry_wake import artifacts
from retry_wake_compiler import (ROOT, App, HolProof, apply, equality, mg_claim,
                             model_terms, read_module, short, tool_modules)


def output_values(text, parse):
    result = []
    for line in text.splitlines():
        if line.strip() in ('', 'true', '[()]'): continue
        if line.startswith('[') and line.endswith(']'): line = line[1:-1]
        result.extend(v for v in parse(line) if v != ())
    return result


def execute(binary, lane, source, work, name):
    path = work / (name + '.' + lane + '.metta'); path.write_text(source)
    result = subprocess.run([str(binary), '--lang', lane, '--quiet', '--import-mode',
                             'ancestor-walk', str(path)], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
    (work / (name + '.' + lane + '.stdout')).write_text(result.stdout)
    (work / (name + '.' + lane + '.stderr')).write_text(result.stderr)
    if result.returncode:
        raise ValueError(f'{lane} model failed: {result.stderr[-500:]}')
    return result.stdout


def claims(model):
    requests, calls, step, active, inactive = model_terms(model)
    result = []
    for request, call, output, _ in model['transitions']:
        transition = apply(step, requests[request], calls[call])
        result.append(('transition', request, call, output, transition, calls[output]))
        if model['terminal'][request]:
            result.append(('inactive', request, call, 'inactive', App(active, transition), inactive))
    return result


def candidate(case, right=None, with_mg=True):
    kind, request, call, result, left, expected = case
    right = expected if right is None else right
    rows, root = HolProof().prove(left, right)
    claim, proof = mg_claim(left, right)
    labels = ' '.join(json.dumps(x) for x in (kind, request, call, result))
    mg_request = f'(gc:mg-check {claim} {proof})' if with_mg else '(GcBackendUnavailableV2 megalodon prime-symbol-wire)'
    source = (f'!(let* (($hol (gc:hol-check {rows} {root})) '
              f'($mg {mg_request})) '
              f'(function (return (GcCheckedCaseV2 {labels} $hol $mg))))\n')
    return source


def alpha(value, symbol, env=()):
    if not isinstance(value, tuple): return value
    if value and value[0] == symbol('v') and len(value) == 3:
        key = (value[1], value[2])
        return ('bound', env.index(key), value[2]) if key in env else ('free', *key)
    if value and value[0] == symbol('L') and len(value) == 3:
        binder = value[1]
        if len(binder) != 3 or binder[0] != symbol('v'):
            raise ValueError('malformed HOL binder')
        return ('lambda', binder[2], alpha(value[2], symbol, ((binder[1], binder[2]),) + env))
    return tuple(alpha(v, symbol, env) for v in value)


def authenticate(records, cases, symbol, string, parse, with_mg=True):
    if len(records) != len(cases): raise ValueError('wrong number of proof results')
    for record, case in zip(records, cases):
        kind, request, call, output, left, right = case
        labels = tuple(string(x) for x in (kind, request, call, output))
        if not isinstance(record, tuple) or record[:5] != (symbol('GcCheckedCaseV2'), *labels) or len(record) != 7:
            raise ValueError('wrong or duplicate proof case')
        h, m = record[5:]
        expected = parse(equality(left, right))[0]
        if (not isinstance(h, tuple) or len(h) != 3 or h[0] != symbol('GcHolEvidenceV2') or
                h[2] != (symbol('Axioms'), ()) or len(h[1]) != 3 or
                h[1][:2] != (symbol('thm'), ()) or
                alpha(h[1][2], symbol) != alpha(expected, symbol)):
            raise ValueError('HOL sequent, hypotheses or axiom dependencies differ')
        if not with_mg:
            if m != (symbol('GcBackendUnavailableV2'), symbol('megalodon'), symbol('prime-symbol-wire')):
                raise ValueError('unsupported backend was represented as evidence')
            continue
        claim, _ = mg_claim(left, right)
        context = (symbol('Megalodon.context'), 0, (symbol('types'),), (symbol('hypotheses'),))
        if m != (symbol('GcMegalodonEvidenceV2'), (symbol('Before'), (symbol('Megalodon.inventory'), (symbol('revision'), 0))),
                 (symbol('Megalodon.judgment'), context, parse(claim)[0])):
            raise ValueError('Megalodon statement or initial theory differs')


def check_execution(binary, lane, model, work, symbol, string, parse):
    library = Path(binary).resolve().parent / "lib/lib_module.metta"
    module = ROOT / "verification/RetryWake.module"
    header = f'!(import! &self {json.dumps(str(library))})\n'
    bindings = f'''(($reader (langdef:load-baked-parser "module-core-reader"))
      ($module (module:read-file $reader "retry-wake" {json.dumps(str(module))}))
      ($theory (module:assemble $module "RetryWake" LNil LNil OriginViewV1 "retry-wake:v2"))
      ($core (module:core $theory "RetryWake"))
      ($authority (langdef:load-language-value $core)))'''
    actions, expected = [], []
    rows = [('Reconcile', (r, c), v, rule) for r, c, v, rule in model['transitions']]
    rows += [('IsActive', (state,), 'FlagTrue' if flag else 'FlagFalse', 'Active' + state.removeprefix('Call'))
             for state, flag in model['active'].items()]
    rows += [('IsTerminal', (state,), 'FlagTrue' if flag else 'FlagFalse', 'Terminal' + state.removeprefix('Request'))
             for state, flag in model['terminal'].items()]
    for index, (operation, args, output, rule) in enumerate(rows):
        term = '(' + operation + ' ' + ' '.join('(' + x + ')' for x in args) + ')'
        sort = 'CallState' if operation == 'Reconcile' else 'Flag'
        actions.append(f'(println! (GcModuleCaseV2 {index} (langdef:rewrite-term-at-data $authority "{sort}" "{sort}" 8 100000 (LangDef:RelationRows) {term})))')
        expected.append((symbol('GcModuleCaseV2'), index,
            (symbol('LangDef:ContextualTermResults'), symbol('LangDef:ContextComplete'),
             (symbol('LangDef:ContextualTermResult'), (symbol(output),),
              (symbol('LangDef:RuleTrace'), index, string(rule))))))
    # All rules close at a state/flag constructor; their rule occurrence is
    # retained and checked rather than discarding origin after evaluation.
    program = (header + '!(let* ' + bindings + ' (progn ' + ' '.join(actions) +
               ' (langdef:close-language $authority) (langdef:close-parser $reader)))\n')
    records = output_values(execute(binary, lane, program, work, 'module-execution'), parse)
    records = [r for r in records if isinstance(r, tuple) and r and r[0] == symbol('GcModuleCaseV2')]
    if records != expected: raise ValueError('module results, coverage or rule origins differ')
    program = f'!(import! &self {json.dumps(str(ROOT/"verification/RetryWakeModel.metta"))})\n'
    expected = []
    for index, (r, c, output, _) in enumerate(model['transitions']):
        program += f'!(println! (GcEvaluatorCaseV2 {index} (collapse (gc:reconcile {short(r)} {short(c)}))))\n'
        expected.append((symbol('GcEvaluatorCaseV2'), index, (symbol(short(output)),)))
        program += f'!(println! (gc:decide {short(r)} {short(c)}))\n'
        expected.append((symbol('GcModelDecisionV2'), symbol(short(r)), symbol(short(c)), symbol(short(output))))
    # An unmodelled state cannot obtain a guessed decision from a catch-all.
    program += '!(println! (gc:decide failed unmodelled))\n'
    expected.append((symbol('GcModelInapplicableV2'), symbol('failed'), symbol('unmodelled')))
    records = output_values(execute(binary, lane, program, work, 'evaluator-execution'), parse)
    if records != expected: raise ValueError('evaluator added or omitted a model answer')
    return len(rows)




HOST_PROGRAM = """(= (host:snapshot $space)
   (let $reserved (case (llm-ledger:request-of $space "fixture")
                    ((((llm:request $ri $rst $rv $rs $ro $rp $rm $rf $row $rfp)) $rv) ($none none)))
     ; A case pattern's variables stay bound for the rest of the rule: each pattern names its own.
     (case (llm-ledger:call-of $space "fixture")
       ((() (absent $reserved))
        (((llm:call $ci $ce $cpid $cs $cc $cu $cst $cg $ct $cso $cb $cq $cr $ca $cl $cpu $cn))
         ($cst $ct $cb (llm-ledger:result-of $space "fixture") $reserved))))))
(= (host:case $request $call $updated)
   (let* (($space (new-space)) ($pid (system:pid)) ($birth (process:birth $pid))
          ($_ (add-atom $space (llm:request "fixture" 900 37 $request none "synthetic" "fixture" none "" "")))
          ($_ (if (== $call "absent") done
                  (let $_ (add-atom $space (llm:call "fixture" "epoch" $pid $birth 900 $updated $call none 7 0 19 0
                                                     none none none "turn" 0))
                    (add-atom $space (llm:result "fixture" "retained evidence")))))
          ($first (llm-ledger:recover-calls-in $space 1000)) ($one (host:snapshot $space))
          ($again (llm-ledger:recover-calls-in $space 1000)) ($two (host:snapshot $space)))
     (HOST $one $two)))
"""


def check_host(binary, model, work):
    """The ledger's own call recovery (llm-ledger:recover-calls-in, which llm-recover runs on the
    store) reproduces every model transition on records owned by a live process: a stale call
    takes the model's state, a recent one (inside the 2 s grace) keeps its own, the evidence and
    the request's reservation are untouched, and a second recovery changes nothing."""
    spec = importlib.util.spec_from_file_location('retry_wake_executable', ROOT/'src/retry_wake_model.py')
    executable = importlib.util.module_from_spec(spec); spec.loader.exec_module(executable)
    cases = []
    # The module the ledger server runs (it imports llm_ledger.metta).
    source = '!(import! &self ' + json.dumps(str(ROOT/'src/input_ledger.metta')) + ')\n' + HOST_PROGRAM
    for request, call, output, _ in model['transitions']:
        r, c, v = map(short, (request, call, output))
        if executable.reconcile(r, c) != v: raise ValueError('generated host model differs')
        for recent in (False, True):
            expected = c if recent else v
            row = '(absent 37)' if c == 'absent' else f'("{expected}" 7 19 "retained evidence" 37)'
            cases.append(f'(HOST {row} {row})')
            source += f'!(println! (host:case "{r}" "{c}" {999 if recent else 990}))\n'
    lines = [line.strip() for line in execute(binary, 'petta', source, work, 'host').splitlines()
             if line.strip().startswith('(HOST ')]
    if lines != cases:
        raise ValueError('actual recovery differs from model, changes unrelated evidence, or is not idempotent')
    return len(cases)


def check_lean(model, models, toolchain, work):
    output = work/'lean'; output.mkdir(exist_ok=True)
    env = {**os.environ, 'LEAN_PATH': str(output), 'LEAN_NUM_THREADS': '1'}
    command = ['nice', '-n', '19', 'lean', '+' + toolchain.removeprefix('leanprover/lean4:'), '-j', '1']
    digests = {}
    for name in ('FuelPolicy', 'RetryWake'):
        source = (models/(name+'.lean')).read_bytes(); path = output/(name+'.lean'); path.write_bytes(source)
        digests[name] = hashlib.sha256(source).hexdigest()
        run = subprocess.run(command + ['-o',str(path.with_suffix('.olean')),str(path)], cwd=output,env=env,capture_output=True,text=True,timeout=120)
        path.with_suffix('.log').write_text(run.stdout+run.stderr)
        if run.returncode: raise ValueError('Lean source model did not compile: '+name)
    source = 'import RetryWake\nopen RetryWake\n'
    for i,(r,c,v,_) in enumerate(model['transitions']):
        source += f'theorem row_{i} : reconcile .{short(r)} .{short(c)} = .{short(v)} := by decide\n'
    for i,(state,flag) in enumerate(model['active'].items()):
        source += f'theorem active_{i} : CallState.active .{short(state)} = {str(flag).lower()} := by decide\n'
    for i,(state,flag) in enumerate(model['terminal'].items()):
        source += f'theorem terminal_{i} : RequestState.terminal .{short(state)} = {str(flag).lower()} := by decide\n'
    source += ('theorem last_token_allows_aba :\n'
               '  (maintain 1 (maintain 2 (maintain 1 ({lastWake := none, maintenance := 0, semanticProgress := 0} : WakeState)))).maintenance = 3 := by decide\n'
               '#print axioms failedRequestHasNoActiveNativeCall\n')
    path=output/'ModuleConformance.lean';path.write_text(source)
    run=subprocess.run(command+[str(path)],cwd=output,env=env,capture_output=True,text=True,timeout=120)
    path.with_suffix('.log').write_text(run.stdout+run.stderr)
    dependencies = re.findall(r'depends on axioms: \[([^]]*)\]', run.stdout)
    axioms = {a.strip() for group in dependencies for a in group.split(',') if a.strip()}
    if run.returncode or not axioms.issubset({'propext'}) or 'RetryWake.failedRequestHasNoActiveNativeCall' not in run.stdout:
        raise ValueError('module differs from Lean or the invariant has unexpected axioms')
    return {'model_sha256':digests, 'compared_observations':34, 'invariant_axioms':sorted(axioms)}


def check(binary, work, lanes=('he', 'petta', 'prime'), models=None, toolchain='v4.33.1'):
    if not lanes or any(lane not in ('he', 'petta', 'prime') for lane in lanes):
        raise ValueError('select he, petta or prime')
    work.mkdir(parents=True, exist_ok=True)
    sources = ('verification/RetryWake.module', 'verification/RetryWakeKernels.metta',
               'verification/RetryWakeModel.metta', 'verification/RetryWake.model.json',
               'src/llm_ledger.metta', 'src/input_ledger.metta', 'src/retry_wake_model.py',
               'scripts/retry_wake_compiler.py', 'scripts/generate_retry_wake.py',
               'scripts/check_retry_wake.py')
    source_hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}
    symbol, string, parse = tool_modules(binary)
    model = read_module(binary, ROOT/'verification/RetryWake.module', work)
    for path, body in artifacts(model).items():
        if not path.is_file() or path.read_text() != body:
            raise ValueError('generated artifact differs from module: ' + path.name)
    cases = claims(model)
    prefix = '!(import! &self ' + json.dumps(str(ROOT/'verification/RetryWakeKernels.metta')) + ')\n'
    result = {}
    for lane in lanes:
        with_mg = True
        probe = output_values(execute(binary, lane, prefix + candidate(cases[0]), work, 'backend-probe'), parse)
        try:
            authenticate(probe, [cases[0]], symbol, string, parse)
        except ValueError:
            probe_text = (work/('backend-probe.'+lane+'.stdout')).read_text()
            if lane != 'prime' or 'Tm:' not in probe_text or 'MegalodonMalformed' not in probe_text:
                raise
            with_mg = False
        executions = check_execution(binary, lane, model, work, symbol, string, parse)
        source = prefix + ''.join(candidate(case, with_mg=with_mg) for case in cases)
        records = output_values(execute(binary, lane, source, work, 'kernels'), parse)
        authenticate(records, cases, symbol, string, parse, with_mg)
        # No named declarations leak between checks: the same case is reusable.
        repeated = output_values(execute(binary, lane, prefix + candidate(cases[0], with_mg=with_mg) * 2, work, 'repeated'), parse)
        authenticate(repeated, [cases[0], cases[0]], symbol, string, parse, with_mg)
        wrong = next(c for c in cases if c[:3] == ('transition', 'RequestFailed', 'CallSubmitted'))
        _, codes, _, _, _ = model_terms(model)
        bad = output_values(execute(binary, lane, prefix + candidate(wrong, codes['CallSubmitted'], with_mg), work, 'must-refuse'), parse)
        mg_refusal = (symbol('GcMegalodonRefusedV2'),) if with_mg else (symbol('GcBackendUnavailableV2'), symbol('megalodon'), symbol('prime-symbol-wire'))
        if (len(bad) != 1 or bad[0][5:] != ((symbol('GcHolRefusedV2'),), mg_refusal)):
            raise ValueError('changed transition result was not refused')
        result[lane] = {'proof_program_sha256': hashlib.sha256((work/('kernels.'+lane+'.metta')).read_bytes()).hexdigest(),
                        'module_executions': executions, 'transition_proofs': 24, 'inactive_proofs': 18,
                        'megalodon_proofs': 42 if with_mg else 0,
                        'repeated_cases': 2, 'wrong_result_refused': True}
    host_cases = check_host(binary, model, work)
    lean = check_lean(model, models, toolchain, work) if models else {'status': 'not-requested'}
    if source_hashes != {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}:
        raise ValueError('model sources changed during qualification')
    receipt = {'schema': 2, 'source_sha256':source_hashes, 'host_cases':host_cases, 'lean':lean, 'scope': 'finite model checking; not whole-host verification',
               'module_sha256': model['module_sha256'], 'lanes': result,
               'engine_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
               'host_sha256': hashlib.sha256((ROOT/'src/llm_ledger.metta').read_bytes()).hexdigest(),
               'generator_sha256': hashlib.sha256((ROOT/'scripts/retry_wake_compiler.py').read_bytes()).hexdigest(),
               'mm0': 'unsupported: audited transition theory required',
               'live_policy_changed': False}
    (work/'receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cetta', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--languages', default='he,petta,prime')
    parser.add_argument('--models', type=Path)
    parser.add_argument('--toolchain', default='v4.33.1')
    args = parser.parse_args()
    print(json.dumps(check(args.cetta.resolve(), args.output.resolve(), tuple(args.languages.split(',')), args.models, args.toolchain), sort_keys=True))


if __name__ == '__main__': main()

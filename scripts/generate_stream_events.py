#!/usr/bin/env python3
"""Compile the finite display lifecycle from the native .module elaboration."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import pprint
import subprocess
import tempfile

from retry_wake_compiler import tool_modules

ROOT = Path(__file__).resolve().parents[1]
STATES = ("Forming", "Streaming", "Running", "Finished")
EVENTS = ("ProviderText", "ProviderTool", "ExecutorStart", "ExecutorFinish", "TerminalFinish", "Interrupt")


def compile_module(binary, work):
    symbol, string, parse = tool_modules(binary)
    source = ROOT / "modes/protocol/StreamEvents.module"
    library = Path(binary).resolve().parent / "lib/lib_module.metta"
    program = f'''!(import! &self {json.dumps(str(library))})
!(let* (($p (langdef:load-baked-parser "module-core-reader"))
 ($m (module:read-file $p "stream-events" {json.dumps(str(source))}))
 ($t (module:assemble $m "Lifecycle" LNil LNil OriginViewV1 "stream-events:v1"))
 ($r (module:rewrite-rules $t "Lifecycle")) ($_ (langdef:close-parser $p)))
 (function (return $r)))'''
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        entry = Path(temporary)/"read.metta"; entry.write_text(program)
        result = subprocess.run([str(binary), "--lang", "petta", str(entry)],
                                capture_output=True, text=True, check=True, timeout=60)
    bundles = [v for v in parse(result.stdout) if isinstance(v, tuple) and v[0] == symbol("ModuleRewriteRulesV1")]
    if len(bundles) != 1: raise ValueError("expected exactly one elaborated module")
    def rows(value):
        out = []
        while isinstance(value, tuple) and len(value)==3 and value[0]==symbol("LCons"):
            out.append(value[1]); value=value[2]
        if value != symbol("LNil"): raise ValueError("expected finite list")
        return out
    def constructor(value):
        if len(value)!=3 or value[0]!=symbol("PApp") or value[2]!=symbol("LNil"):
            raise ValueError("expected ground constructor")
        return value[1].text
    core, origins = bundles[0][1:]
    rules = rows(core[5])
    if [row[2] for row in rows(origins)] != rules: raise ValueError("missing source provenance")
    table = {}
    for rule in rules:
        if rule[0]!=symbol("RewriteRule") or rule[2:4]!=(symbol("LNil"),symbol("LNil")):
            raise ValueError("only ground premise-free transitions are admitted")
        left, right = rule[4:6]
        if left[0]!=symbol("PApp") or left[1]!=string("Step"): raise ValueError("expected Step")
        key = tuple(map(constructor,rows(left[2])))
        target = constructor(right)
        if len(key)!=2 or key[0] not in STATES or key[1] not in EVENTS or target not in STATES or key in table:
            raise ValueError("overlapping or ill-sorted transition")
        table[key]=target
    if set(table)!=set(itertools.product(STATES,EVENTS)): raise ValueError("incomplete transition table")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return table, digest


def artifacts(table, digest):
    py = ('"""Generated from StreamEvents.module; do not edit."""\n'
          f'MODULE_SHA256 = {digest!r}\nTRANSITIONS = '+pprint.pformat(table,sort_dicts=True)+'\n')
    metta = '; Generated from StreamEvents.module: disjoint and exhaustive.\n'
    metta += f'(= (display:module-digest) "{digest}")\n'
    for (s,e),t in table.items(): metta+=f'(= (display:step {s} {e}) {t})\n'
    lean = 'import Std\n\n/- Generated transition function from StreamEvents.module.\n'
    lean += '   Theorems check lifecycle laws; adapter correspondence is tested separately. -/\n'
    lean += 'namespace StreamingIO\ninductive State where\n'
    for s in STATES: lean+='  | '+s+'\n'
    lean += '  deriving DecidableEq, Repr\ninductive Event where\n'
    for e in EVENTS: lean+='  | '+e+'\n'
    lean += '  deriving DecidableEq, Repr\ndef step : State → Event → State\n'
    for (s,e),t in table.items(): lean+=f'  | .{s}, .{e} => .{t}\n'
    lean+='''
theorem finished_is_absorbing (e : Event) : step .Finished e = .Finished := by
  cases e <;> rfl
theorem running_requires_start (s : State) (e : Event) (h : step s e = .Running) :
    s = .Running ∨ e = .ExecutorStart := by
  cases s <;> cases e <;> simp_all [step]
structure Cursor where
  sequence : Nat
  state : State
  deriving DecidableEq
def observe (c : Cursor) (seq : Nat) (e : Event) : Cursor :=
  if seq ≤ c.sequence then c else ⟨seq, step c.state e⟩
theorem replay_idempotent (c : Cursor) (seq : Nat) (e : Event) :
    observe (observe c seq e) seq e = observe c seq e := by
  unfold observe
  split <;> simp_all
theorem cursor_never_regresses (c : Cursor) (seq : Nat) (e : Event) :
    c.sequence ≤ (observe c seq e).sequence := by
  unfold observe
  split <;> simp_all <;> omega
theorem closed_trace_stays_closed (events : List Event) :
    events.foldl step .Finished = .Finished := by
  induction events with
  | nil => rfl
  | cons e es ih => simpa [List.foldl, finished_is_absorbing] using ih
end StreamingIO
'''
    return {ROOT/'src/stream_lifecycle.py':py,ROOT/'src/stream_lifecycle.metta':metta,
            ROOT/'verification/StreamingIO.lean':lean}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cetta',type=Path,required=True);p.add_argument('--work',type=Path,required=True)
    p.add_argument('--check',action='store_true');args=p.parse_args();args.work.mkdir(parents=True,exist_ok=True)
    table,digest=compile_module(args.cetta,args.work)
    for path,body in artifacts(table,digest).items():
        if args.check:
            if path.read_text()!=body: raise ValueError('stale artifact: '+path.name)
        else: path.write_text(body)
    print(json.dumps(dict(transitions=len(table),module_sha256=digest,checked=args.check)))


if __name__=='__main__':main()

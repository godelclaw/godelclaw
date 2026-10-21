"""Read the finite RetryWake module and produce checked-model encodings.

The native module reader owns parsing and elaboration. This adapter admits
only ground, premise-free, total, single-valued finite transitions. Python
produces proof candidates; the HOL and Megalodon kernels check them.
"""
from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def tool_modules(binary):
    directory = Path(binary).resolve().parent / "tools"
    if not (directory / "gslt2parse_schema_v1.py").is_file():
        raise ValueError("the engine bundle must include its schema tools")
    sys.path.insert(0, str(directory))
    from gslt2parse_schema_v1 import Symbol, StringLiteral, parse_sexprs
    return Symbol, StringLiteral, parse_sexprs


def read_module(binary, source, work):
    symbol, string, parse = tool_modules(binary)
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    library = Path(binary).resolve().parent / "lib" / "lib_module.metta"
    body = f'''!(import! &self {json.dumps(str(library))})
!(let* (($reader (langdef:load-baked-parser "module-core-reader"))
 ($module (module:read-file $reader "retry-wake" {json.dumps(str(Path(source).resolve()))}))
 ($theory (module:assemble $module "RetryWake" LNil LNil OriginViewV1 "godelclaw-retry-wake:v2"))
 ($view (module:rewrite-rules $theory "RetryWake"))
 ($_ (langdef:close-parser $reader))) (function (return $view)))
'''
    with tempfile.TemporaryDirectory(dir=work, prefix="module-") as temporary:
        path = Path(temporary) / "export.metta"; path.write_text(body)
        run = subprocess.run([str(binary), "--lang", "petta", "--import-mode",
                              "ancestor-walk", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=45)
    if run.returncode:
        raise ValueError("module reader failed: " + run.stderr[-500:])
    values = parse(run.stdout)
    def headed(value, name, size):
        return isinstance(value, tuple) and len(value) == size + 1 and value[0] == symbol(name)
    bundles = [v for v in values if headed(v, "ModuleRewriteRulesV1", 2)]
    if len(bundles) != 1 or any(headed(v, "Error", 2) for v in values):
        raise ValueError("expected one elaborated rule bundle")
    def rows(value):
        result = []
        while headed(value, "LCons", 2):
            result.append(value[1]); value = value[2]
        if value != symbol("LNil"):
            raise ValueError("expected a finite rule list")
        return result
    core, origins = bundles[0][1:]
    if not headed(core, "GSLTLanguageDefWireV1", 5):
        raise ValueError("unsupported language core")
    if {(row[1].text, row[2]) for row in rows(core[2]) if headed(row, "TypeDecl", 2)} != {
            ('RequestState', symbol('CarrierAst')), ('CallState', symbol('CarrierAst')), ('Flag', symbol('CarrierAst'))}:
        raise ValueError("unsupported state carriers")
    if rows(core[4]):
        raise ValueError("context declarations require a separate adapter")
    requests, calls, flags = [], [], []
    for rule in rows(core[3]):
        if not headed(rule, "GrammarRule", 5):
            raise ValueError("malformed grammar rule")
        name, category, parameters = rule[1:4]
        if not isinstance(name, string) or not isinstance(category, string):
            raise ValueError("constructor names must be text")
        if name.text in ("Reconcile", "IsActive", "IsTerminal"):
            expected = {'Reconcile': ('CallState', ['RequestState', 'CallState']),
                        'IsActive': ('Flag', ['CallState']), 'IsTerminal': ('Flag', ['RequestState'])}[name.text]
            params = rows(parameters)
            if (category.text != expected[0] or len(params) != len(expected[1]) or
                    any(not headed(p, 'TermSimple', 2) or p[2] != (symbol('TBase'), string(t))
                        for p, t in zip(params, expected[1]))):
                raise ValueError("unsupported operation signature")
            continue
        if category.text not in ('RequestState', 'CallState', 'Flag') or parameters != symbol("LNil"):
            raise ValueError("unsupported or non-nullary finite state constructor")
        (requests if category.text == "RequestState" else
         calls if category.text == "CallState" else
         flags if category.text == "Flag" else []).append(name.text)
    if (requests != ['RequestPending', 'RequestCompleted', 'RequestFailed', 'RequestUnknown'] or
            calls != ['CallAbsent', 'CallClaimed', 'CallSubmitted', 'CallCompleted', 'CallFailed', 'CallUncertain']):
        raise ValueError("unsupported finite request/call vocabulary or constructor order")
    if flags != ["FlagFalse", "FlagTrue"]:
        raise ValueError("expected the two declared flags")
    def constructor(pattern):
        if not headed(pattern, "PApp", 2) or pattern[2] != symbol("LNil"):
            raise ValueError("expected a nullary constructor pattern")
        return pattern[1].text
    transitions, active_rows, terminal_rows = [], {}, {}
    source_rules = rows(core[5]); occurrences = rows(origins)
    if [o[2] for o in occurrences] != source_rules:
        raise ValueError("rule origins do not cover the exact source rules")
    seen = set()
    for rule in source_rules:
        if not headed(rule, "RewriteRule", 5) or rule[2:4] != (symbol("LNil"), symbol("LNil")):
            raise ValueError("only ground rules without premises are supported")
        left, right = rule[4:6]
        if not headed(left, "PApp", 2):
            raise ValueError("expected a constructor application")
        args = rows(left[2]); result = constructor(right)
        if left[1] in (string("IsActive"), string("IsTerminal")):
            active = left[1] == string("IsActive")
            destination, domain = (active_rows, calls) if active else (terminal_rows, requests)
            if len(args) != 1 or result not in flags:
                raise ValueError("invalid finite classification")
            state = constructor(args[0])
            if state not in domain or state in destination:
                raise ValueError("wrongly sorted or overlapping classification")
            destination[state] = result == "FlagTrue"
            continue
        if left[1] != string("Reconcile") or len(args) != 2:
            raise ValueError("expected a reconciliation rule")
        request, call = map(constructor, args)
        if request not in requests or call not in calls or result not in calls:
            raise ValueError("wrongly sorted state")
        if (request, call) in seen:
            raise ValueError("overlapping reconciliation rules")
        seen.add((request, call))
        transitions.append((request, call, result, rule[1].text))
    if seen != set(itertools.product(requests, calls)):
        raise ValueError("reconciliation must cover every state pair")
    if set(active_rows) != set(calls) or set(terminal_rows) != set(requests):
        raise ValueError("finite classifications must cover every state")
    return {"active": active_rows, "terminal": terminal_rows, "module_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "requests": requests, "calls": calls, "transitions": transitions}


def short(name):
    return name.removeprefix("Request").removeprefix("Call").lower()


BOOL = ("bool",)

def arrow(left, right): return ("fun", left, right)

def arrows(count):
    result = BOOL
    for _ in range(count): result = arrow(BOOL, result)
    return result


@dataclass(frozen=True)
class Var:
    name: str
    ty: tuple

@dataclass(frozen=True)
class App:
    fn: object
    arg: object

@dataclass(frozen=True)
class Lam:
    var: Var
    body: object


def apply(fn, *args):
    for arg in args: fn = App(fn, arg)
    return fn


def lambdas(variables, body):
    for var in reversed(variables): body = Lam(var, body)
    return body


def selector(size, selected, prefix):
    args = [Var(prefix + str(i), BOOL) for i in range(size)]
    return lambdas(args, args[selected])


def model_terms(model):
    requests, calls = model["requests"], model["calls"]
    request_codes = {r: selector(len(requests), i, "r") for i, r in enumerate(requests)}
    call_codes = {c: selector(len(calls), i, "c") for i, c in enumerate(calls)}
    r, c = Var("request", arrows(4)), Var("call", arrows(6))
    outputs = [Var("out" + str(i), BOOL) for i in range(6)]
    table = {(r, c): result for r, c, result, _ in model["transitions"]}
    rows = [apply(c, *(outputs[calls.index(table[(request, call)])] for call in calls))
            for request in requests]
    reconcile = lambdas([r, c, *outputs], apply(r, *rows))
    off, on = Var("off", BOOL), Var("on", BOOL)
    active = lambdas([c, off, on], apply(c, *(on if model["active"][call] else off for call in calls)))
    inactive = lambdas([off, on], off)
    return request_codes, call_codes, reconcile, active, inactive


def type_of(term):
    if isinstance(term, Var): return term.ty
    if isinstance(term, Lam): return arrow(term.var.ty, type_of(term.body))
    fn = type_of(term.fn)
    if fn[0] != "fun" or fn[1] != type_of(term.arg):
        raise ValueError("ill-typed proof candidate")
    return fn[2]


def hol_type(ty):
    return '(T "bool")' if ty == BOOL else f'(T "fun" {hol_type(ty[1])} {hol_type(ty[2])})'


def hol(term):
    if isinstance(term, Var): return f'(v {json.dumps(term.name)} {hol_type(term.ty)})'
    if isinstance(term, App): return f'(A {hol(term.fn)} {hol(term.arg)})'
    return f'(L {hol(term.var)} {hol(term.body)})'


def equality(left, right):
    ty = type_of(left)
    if ty != type_of(right): raise ValueError("equality type mismatch")
    eq = f'(c "=" {hol_type(arrow(ty, arrow(ty, BOOL)))})'
    return f'(A (A {eq} {hol(left)}) {hol(right)})'


def free(term):
    if isinstance(term, Var): return {term}
    if isinstance(term, App): return free(term.fn) | free(term.arg)
    return free(term.body) - {term.var}


def substitute(term, var, value):
    if isinstance(term, Var): return value if term == var else term
    if isinstance(term, App): return App(substitute(term.fn, var, value), substitute(term.arg, var, value))
    if term.var == var: return term
    binder, body = term.var, term.body
    if binder in free(value):
        occupied = free(body) | free(value) | {var}
        replacement = Var(binder.name + "_fresh", binder.ty)
        while replacement in occupied: replacement = Var(replacement.name + "_", replacement.ty)
        body = substitute(body, binder, replacement); binder = replacement
    return Lam(binder, substitute(body, var, value))


class HolProof:
    """Untrusted beta proof producer; every inference is kernel checked."""
    def __init__(self):
        self.rows = []; self.cache = {}

    def add(self, request, parents=(), claim=None):
        index = len(self.rows)
        tail = "" if claim is None else f" (thm () {claim})"
        self.rows.append(f'(HolNode {index} {request} ({" ".join(map(str, parents))}){tail})')
        return index

    def norm(self, term):
        if term in self.cache: return self.cache[term]
        if isinstance(term, Var):
            result = term, self.add(f'(REFL {hol(term)})')
        elif isinstance(term, Lam):
            normal, proof = self.norm(term.body)
            result = Lam(term.var, normal), self.add(f'(ABS {hol(term.var)})', (proof,))
        else:
            fn, pf = self.norm(term.fn); arg, pa = self.norm(term.arg)
            proof = self.add('(MK_COMB)', (pf, pa))
            if isinstance(fn, Lam):
                # HOL Light's primitive BETA is the variable instance; INST
                # checks the general capture-avoiding substitution separately.
                beta = self.add(f'(BETA {hol(App(fn, fn.var))})')
                inst = self.add(f'(INST (({hol(arg)} {hol(fn.var)})))', (beta,))
                first = self.add('(TRANS)', (proof, inst))
                normal, rest = self.norm(substitute(fn.body, fn.var, arg))
                result = normal, self.add('(TRANS)', (first, rest))
            else:
                result = App(fn, arg), proof
        self.cache[term] = result
        return result

    def prove(self, left, right):
        normal, proof = self.norm(left)
        reflexive = self.add(f'(REFL {hol(normal)})')
        root = self.add('(TRANS)', (proof, reflexive), equality(left, right))
        return '(' + ' '.join(self.rows) + ')', root


def mg_type(ty):
    return '(Tp.prop)' if ty == BOOL else f'(Tp.arr {mg_type(ty[1])} {mg_type(ty[2])})'


def mg(term, context=()):
    if isinstance(term, Var):
        return f'(Tm.db {context.index(term)})'
    if isinstance(term, App): return f'(Tm.app {mg(term.fn, context)} {mg(term.arg, context)})'
    return f'(Tm.lam {mg_type(term.var.ty)} {mg(term.body, (term.var,) + context)})'


def mg_claim(left, right):
    predicate = f'(Tp.arr {mg_type(type_of(left))} (Tp.prop))'
    lhs, rhs = f'(Tm.app (Tm.db 0) {mg(left)})', f'(Tm.app (Tm.db 0) {mg(right)})'
    claim = f'(Tm.all {predicate} (Tm.imp {lhs} {rhs}))'
    proof = f'(Pf.termLam {predicate} (Pf.proofLam {lhs} (Pf.hyp 0)))'
    return claim, proof

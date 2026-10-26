"""Project06 isolated native/oracle checks, with artifact-specific receipts.

Supply CETTA_BIN and optional MODE_STATE_* pins. This runner never chooses a
release, downloads a tool, touches live state, or makes provider/channel calls.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from effect_receipts_capability_checkpoint import LIMIT
from effect_receipts_support import IMPORTS, ROOT, emit, limit, q, rows
from mode_state_support import Qualification


CAPTURE = '''
(= (mode-test:capture $value)
   (case $value
     (((Error $_ (PythonException $class $message $_))
       (JsonObject ((JsonPair "error" (str:symbol->string $class)) (JsonPair "message" $message))))
      ((Error $where $why)
       (JsonObject ((JsonPair "unexpected_native_error" (repr ($where $why))))))
      ($other $other))))
'''

SNAPSHOT = '''def snapshot(root):
    import os, stat
    from pathlib import Path
    result = {}
    for p in sorted(Path(root).rglob("*")):
        mode = p.lstat().st_mode
        row = {"mode": oct(stat.S_IMODE(mode))}
        if stat.S_ISLNK(mode):
            row.update(kind="symlink", target=os.readlink(p))
        elif stat.S_ISDIR(mode):
            row.update(kind="directory")
        else:
            row.update(kind="file", hex=p.read_bytes().hex())
        result[str(p.relative_to(root))] = row
    return result
'''

FIXTURE = '''import json, os
import engine_modes, fuel_modes, attention
from pathlib import Path
counts = {"configure": 0, "snapshot": 0, "blocked": 0}
def blocked(*args, **kwargs):
    counts["blocked"] += 1
    raise AssertionError("ported Python function reached in native qualification")
for module, names in ((engine_modes, ("active_engine", "selected_engine", "engine_view", "set_engine", "engine_command", "request_recycle")),
                      (fuel_modes, ("current_fuel", "refuel", "ceiling", "spend", "fuel_view", "context_view", "fuels_view", "set_fuel")),
                      (attention, ("description",))):
    for name in names:
        setattr(module, name, blocked)
def configure(text):
    counts["configure"] += 1
    for key, value in json.loads(text).items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return 1
''' + SNAPSHOT + '''
def state_snapshot(root):
    counts["snapshot"] += 1
    return json.dumps(snapshot(root), ensure_ascii=True)
def counters():
    return json.dumps(counts, sort_keys=True)
'''


def literal(value):
    if isinstance(value, str):
        # Python JSON strings deliberately include surrogatepass strings.
        return '(json:parse-python %s)' % json.dumps(json.dumps(value, ensure_ascii=True))
    if value is True:
        return "True"
    if value is False:
        return "False"
    if isinstance(value, (int, float)):
        return '(json:parse-python %s)' % json.dumps(json.dumps(value))
    if isinstance(value, list):
        return "(" + " ".join(literal(v) for v in value) + ")"
    raise TypeError(type(value))


def bridge(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return [bridge(v) for v in value]
    return value


def python(c, name, source, settings=None, cwd=ROOT):
    c.index += 1
    stem = "%03d-%s" % (c.index, name)
    path = c.directory / (stem + ".py")
    path.write_text(source, encoding="utf-8")
    (c.evidence / path.name).write_bytes(path.read_bytes())
    env = c.environment()
    env.update(settings or {})
    command = [sys.executable, str(path)]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            timeout=90, preexec_fn=limit)
    c.record_process(stem, command, result, env, cwd=cwd)
    assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
    return json.loads(result.stdout)


def fixture(c):
    path = c.directory / "mode_state_fixture.py"
    path.write_text(FIXTURE, encoding="utf-8")
    (c.evidence / "mode_state_fixture.py").write_bytes(path.read_bytes())
    return ('!(import! &self %s)\n' % path +
            '(= (mode-test:host-text $value) (if (== (get-type $value) String) $value (str:symbol->string $value)))\n',
            {"PYTHONPATH": str(c.directory) + os.pathsep + c.environment()["PYTHONPATH"]})


def captured(expression):
    return "(mode-test:capture (catch %s))" % expression


def active(c, args):
    spaces = [chr(n) for n in range(sys.maxunicode + 1) if chr(n).isspace()]
    cases = [{"METTACLAW_ACTIVE_ENGINE": v, "METTACLAW_ENGINE": fallback}
             for fallback in (None, "cetta", "swi", "unknown")
             for v in (None, "", "cetta", "petta", "CETTA", "swi", "swi-petta", "pleatta", "unknown", " ceTTa ")]
    cases += [{"METTACLAW_ACTIVE_ENGINE": s + "CeTTa" + s, "METTACLAW_ENGINE": "petta"} for s in spaces]
    cases += [{"METTACLAW_ACTIVE_ENGINE": "cetta" + s + "x", "METTACLAW_ENGINE": "cetta"} for s in spaces]
    cases += [{"METTACLAW_ACTIVE_ENGINE": value, "METTACLAW_ENGINE": "cetta"}
              for value in ("\u200bCETTA\u200b", "ＣＥＴＴＡ", "cettK", "cetta\udcff", "\udc80cetta")]
    with c.phase("active-engine"):
        expected = python(c, "active-oracle", '''import json, os, engine_modes
cases = ''' + repr(cases) + '''
out = []
for case in cases:
    for key, value in case.items():
        if value is None: os.environ.pop(key, None)
        else: os.environ[key] = value
    out.append(engine_modes.active_engine())
print(json.dumps(out))
''')
        setup, settings = fixture(c)
        source = IMPORTS + setup + ('!(import! &self ./src/engine_modes.metta)\n' * 2)
        for case in cases:
            source += emit('(let $_ (py-call (mode_state_fixture.configure %s)) (engine:active))'
                           % q(json.dumps(case, ensure_ascii=True)))
        source += emit('(json:parse-python (mode-test:host-text (py-call (mode_state_fixture.counters))))')
        actual = rows(c.run("active-native-poisoned", source, settings))
        counters = actual.pop()
        assert counters == {"configure": len(cases), "snapshot": 0, "blocked": 0}, counters
        assert actual == expected, [(i, cases[i], a, e) for i, (a, e) in enumerate(zip(actual, expected)) if a != e]
        c.save("active-results.json", {"cases": cases, "actual": actual, "expected": expected,
                                      "python_control_counts": counters, "whitespace_count": len(spaces)})
        if args.swipl and args.swi_root:
            helper = c.directory / "mode_swi_fixture.py"
            helper.write_text('''import engine_modes
original = engine_modes.active_engine
count = 0
def observed():
    global count
    count += 1
    return original()
engine_modes.active_engine = observed
def calls():
    return count
''')
            (c.evidence / helper.name).write_bytes(helper.read_bytes())
            source = c.directory / "swi-active.metta"
            source.write_text('!(import! &self %s)\n' % helper +
                              ('!(import! &self %s)\n' % (ROOT / "src/engine_modes.metta")) * 2 +
                              '!(println! (SwiActive (engine:active)))\n'
                              '!(println! (SwiCalls (py-call (mode_swi_fixture.calls))))\n'
                              '!(println! (SwiOwners (collapse (match &self (engine-modes:loaded) True))))\n')
            (c.evidence / source.name).write_bytes(source.read_bytes())
            env = c.environment()
            env.update(settings, METTACLAW_ACTIVE_ENGINE="petta")
            command = [str(args.swipl), "--stack_limit=1g", "-q", "-s",
                       str(args.swi_root / "src/main.pl"), "--", str(source)]
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                    timeout=60, preexec_fn=limit)
            c.record_process("actual-swi-active", command, result, env, cwd=ROOT)
            assert result.returncode == 0, result.stderr.decode(errors="backslashreplace")
            assert b"(SwiActive petta)" in result.stdout or b'(SwiActive "petta")' in result.stdout, result.stdout[-5000:]
            assert b"(SwiCalls 1)" in result.stdout and b"(SwiOwners (true))" in result.stdout, result.stdout[-5000:]


def fuel_reads(c):
    states = {"missing": None, "corrupt": b"{bad", "nonobject": b"[]", "null": b"null",
              "invalid-utf8": b'{"fuel":"carry"}\xff', "bom": b'\xef\xbb\xbf{"fuel":"carry"}',
              "cr": b'{\r"fuel": "carry"\r}\r', "duplicate": b'{"fuel":"carry","fuel":"unlimited"}',
              "duplicate-invalid": b'{"fuel":"carry","fuel":7}',
              "extra": b'{"extra": 1, "fuel":"accumulate"}',
              "nan": b'{"fuel":NaN}', "infinity": b'{"fuel":Infinity}',
              "surrogate": b'{"fuel":"\\ud800"}', "huge-int": ('{"fuel":' + '9' * 4301 + '}').encode()}
    for name in ("saturate", "accumulate", "carry", "decay", "unlimited"):
        states[name] = json.dumps({"fuel": name}).encode()
    for index, char in enumerate(chr(n) for n in range(sys.maxunicode + 1) if chr(n).isspace()):
        states["whitespace-%02d" % index] = json.dumps({"fuel": char + "CaRrY" + char}).encode()
    values = [0, 1, 49, 50, 51, 199, 200, 201, 1000, -3, 2.9, 9007199254740993,
              9223372036854775809, "1_000", "\u3000+١_٢\u3000", "\x1c7\x1c", "7.0", "", "x"]
    grants = [0, 1, 50, -1, "+50", "١_٠", 0.9]
    with c.phase("fuel-read-and-arithmetic"):
        setup, settings = fixture(c)
        path = c.directory / "fuel-mode.json"
        results = []
        for label, raw in states.items():
            path.unlink(missing_ok=True)
            if raw is not None: path.write_bytes(raw)
            small = label not in ("saturate", "accumulate", "carry", "decay", "unlimited")
            loops = values[:3] if small else values
            offered = grants[:3] if small else grants
            calls = [("current_fuel", []), ("fuel_view", []), ("fuels_view", [])]
            native = ["(fuel:current)", "(fuel:view)", "(fuel:list-view)"]
            for value in loops:
                calls += [("spend", [bridge(value)]), ("context_view", [bridge(value)])]
                native += ["(fuel:spend %s)" % literal(value), "(fuel:context-view %s)" % literal(value)]
                for grant in offered:
                    calls.append(("refuel", [bridge(value), bridge(grant)]))
                    native.append("(fuel:refuel %s %s)" % (literal(value), literal(grant)))
            for grant in offered:
                calls.append(("ceiling", [bridge(grant)])); native.append("(fuel:ceiling %s)" % literal(grant))
            expected = python(c, "fuel-oracle-" + label, '''import json, fuel_modes
calls = ''' + repr(calls) + '''
out = []
for name, args in calls:
    try: out.append(getattr(fuel_modes, name)(*args))
    except Exception as exc: out.append({"error": type(exc).__name__, "message": str(exc)})
print(json.dumps(out, ensure_ascii=True))
''')
            source = IMPORTS + setup + '!(import! &self ./src/fuel_cetta.metta)\n' + CAPTURE
            actual = rows(c.run("fuel-native-" + label, source + ''.join(emit(captured(e)) for e in native), settings))
            assert actual == expected, (label, [(native[i], a, e) for i, (a, e) in enumerate(zip(actual, expected)) if a != e][:10])
            assert len(actual) == len(expected), (label, len(actual), len(expected))
            assert (path.read_bytes() if path.exists() else None) == raw, label
            results.append({"state": label, "input_hex": None if raw is None else raw.hex(),
                            "cases": len(actual), "actual": actual, "expected": expected})
        c.save("fuel-read-results.json", {"states": results, "cases": sum(r["cases"] for r in results)})


@contextmanager
def umask(value):
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def fuel_sequences(c):
    steps = [" carry ", "UNLIMITED", "garbage", "\x1cDECAY\u3000", "saturate", 0, [], True, "accumulate", "carry"]
    root = c.directory / "sequence-state"
    path = root / "fuel.json"
    setup, settings = fixture(c)
    settings = {**settings, "METTACLAW_FUEL_MODE_PATH": str(path)}
    def seed(case):
        if root.exists():
            root.chmod(0o700)
            for p in root.rglob("*"):
                if p.is_dir(): p.chmod(0o700)
            shutil.rmtree(root)
        root.mkdir()
        path.write_bytes(b'{"fuel":"decay","extra":"strip me"}\n')
        path.chmod(0o600)
        (root / "fuel.json.tmp").write_bytes(b"stale\n")
        (root / "fuel.json.tmp").chmod(0o604)
        if case == "target-directory":
            path.unlink(); path.mkdir()
        elif case == "temporary-directory":
            (root / "fuel.json.tmp").unlink(); (root / "fuel.json.tmp").mkdir()
        elif case == "unwritable-directory":
            root.chmod(0o500)
        elif case == "unwritable-temporary":
            (root / "fuel.json.tmp").chmod(0o400)
        elif case == "blocked-parent":
            path.unlink(); (root / "fuel.json.tmp").unlink()
            (root / "blocker").write_text("not a directory")
    with c.phase("fuel-sequences-and-faults"), umask(0o027):
        summary = []
        cases = ["normal", "target-directory", "temporary-directory", "unwritable-directory",
                 "unwritable-temporary", "blocked-parent"]
        for case in cases:
            if os.geteuid() == 0 and case.startswith("unwritable"):
                summary.append({"case": case, "skipped": "uid 0 does not witness owner permission denial"}); continue
            seed(case)
            selected = root / "blocker/fuel.json" if case == "blocked-parent" else path
            env = {**settings, "METTACLAW_FUEL_MODE_PATH": str(selected)}
            source = IMPORTS + setup + '!(import! &self ./src/fuel_cetta.metta)\n' + CAPTURE
            for i, value in enumerate(steps):
                source += '!(println! (ModeStep %d))\n' % i
                source += emit('(JsonObject ((JsonPair "reply" %s) (JsonPair "view" (fuel:view)) '
                               '(JsonPair "list" (fuel:list-view)) (JsonPair "current" (fuel:current)) '
                               '(JsonPair "files" (json:parse-python (mode-test:host-text (py-call (mode_state_fixture.state_snapshot %s)))))))'
                               % (captured("(fuel:set %s)" % literal(value)), q(root)))
            source += emit('(json:parse-python (mode-test:host-text (py-call (mode_state_fixture.counters))))')
            output = c.run("fuel-sequence-native-" + case, source, env)
            actual = rows(output); counters = actual.pop()
            assert counters == {"configure": 0, "snapshot": len(steps), "blocked": 0}, counters
            printed = [[] for _ in steps]
            index = None
            for line in output.splitlines():
                if line.startswith("(ModeStep "): index = int(line[len("(ModeStep "):-1])
                elif line.startswith("[fuel-mode]"):
                    assert index is not None; printed[index].append(line)
            seed(case)
            expected = python(c, "fuel-sequence-oracle-" + case, '''import json, io, contextlib, fuel_modes
''' + SNAPSHOT + '''
out = []
for value in ''' + repr([bridge(v) for v in steps]) + ''':
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        try: reply = fuel_modes.set_fuel(value)
        except Exception as exc: reply = {"error":type(exc).__name__,"message":str(exc)}
    out.append({"values":{"reply":reply,"view":fuel_modes.fuel_view(),"list":fuel_modes.fuels_view(),
                          "current":fuel_modes.current_fuel(),"files":snapshot(''' + repr(str(root)) + ''')},
                "printed":printed.getvalue().splitlines()})
print(json.dumps(out, ensure_ascii=True))
''', {"METTACLAW_FUEL_MODE_PATH": str(selected)})
            assert len(actual) == len(expected), (case, len(actual), len(expected))
            for i, (a, e) in enumerate(zip(actual, expected)):
                assert a == e["values"] and printed[i] == e["printed"], (case, i, a, e, printed[i])
            summary.append({"case": case, "steps": len(steps), "actual": actual, "printed": printed,
                            "expected": expected, "python_control_counts": counters})
        c.save("fuel-sequence-results.json", summary)
        if root.exists(): root.chmod(0o700)


def fuel_numeric_boundaries(c):
    values = [0, -1, 1.9, 1e20, 1e300, float("nan"), float("inf"), -float("inf"),
              "١_٢", "１２３", "+1_000", "1__0", "1_", "_1", "−3", "7.0"]
    values += [chr(cp) + "7" + chr(cp) for cp in (28, 29, 30, 31, 133, 160, 8192, 12288)]
    values += ["1" * 4300, "1" * 4301, "0" * 4301]
    with c.phase("fuel-count-boundaries"):
        expected = python(c, "fuel-count-oracle", '''import json
nan, inf = float("nan"), float("inf")
values = ''' + repr(values) + '''
out = []
for value in values:
    try: out.append(max(0, int(value)))
    except (TypeError, ValueError): out.append(0)
    except Exception as exc: out.append({"error":type(exc).__name__,"message":str(exc)})
print(json.dumps(out))
''')
        setup, settings = fixture(c)
        source = IMPORTS + setup + '!(import! &self ./src/fuel_cetta.metta)\n' + CAPTURE
        source += ''.join(emit(captured('(fuel:count %s)' % literal(value))) for value in values)
        source += emit('(json:parse-python (mode-test:host-text (py-call (mode_state_fixture.counters))))')
        actual = rows(c.run("fuel-count-native", source, settings))
        counters = actual.pop()
        assert actual == expected, [(i, a, e) for i, (a, e) in enumerate(zip(actual, expected)) if a != e]
        assert len(actual) == len(expected)
        assert counters == {"configure": 0, "snapshot": 0, "blocked": 0}, counters
        c.save("fuel-count-boundaries.json", {"inputs": values, "actual": actual, "expected": expected,
                                               "python_control_counts": counters,
                                               "domain": "observed integer/number and text crossings; no arbitrary Python-object parity claim"})


def unicode_domain(c):
    """A required capability counterexample, NOT a port parity pass."""
    values = ["İ", "ΟΣ", "Σ", "K", "Ⱥ", "['ΟΣ']", "Unknown-İ", "\ud800A", "CETTA"]
    with c.phase("unchecked-active-mode-unicode-domain"):
        source = IMPORTS + ''.join(emit('(str:lower %s)' % literal(value)) for value in values)
        source += emit('(system:signal-probe 0)')
        observed = rows(c.run("native-unicode-lower-probe", source))
        native, pid_zero = observed[:-1], observed[-1]
        path = c.directory / "active-loop.json"
        path.write_text(json.dumps({"pid": 0, "mode": "İ"}), encoding="utf-8")
        (c.directory / "loop-mode.json").write_text('{"mode":"coding"}\n')
        expected = python(c, "oracle-unchecked-active-mode", '''import json, loop_modes
values = ''' + repr(values) + '''
print(json.dumps({"canonical":[loop_modes.canonical(value) for value in values],
                  "active":loop_modes.active_mode(), "view":loop_modes.mode_view()}, ensure_ascii=True))
''')
        assert pid_zero is True
        assert native[0] == "İ" and expected["canonical"][0] == "i\u0307", (native, expected)
        assert native[1] == "ΟΣ" and expected["canonical"][1] == "ος", (native, expected)
        assert expected["active"] == "i\u0307" and expected["view"] == "active mode: i\u0307; requested coding — recycling", expected
        inventory = []
        for library in sorted((c.bundle / "lib").rglob("*.metta")):
            lines = library.read_text().splitlines()
            for number, line in enumerate(lines, 1):
                if re.search(r"(?i)casefold|case.fold|unicode.*lower|lower.*unicode|str:lower", line):
                    inventory.append({"file": str(library.relative_to(c.bundle)), "line": number,
                                      "context": lines[max(0, number - 2):number + 2]})
        c.save("unicode-domain-counterexample.json", {
            "status": "BLOCKED_UNICODE_LOWER_CAPABILITY", "inputs": values,
            "native_str_lower": native, "native_pid_zero": pid_zero, "python": expected,
            "active_loop_hex": path.read_bytes().hex(), "active_loop_mode": oct(path.stat().st_mode & 0o777),
            "native_lower_api_inventory": inventory,
            "request": "CPython 3.12 full Unicode str.lower text semantics, including one-to-many mappings, context-sensitive final sigma and surrogate preservation",
            "limits": "actual Python active_mode/mode_view counterexample; native primitive probe, not an implemented native loop view; no membership/oracle/domain change"})
    return "BLOCKED_UNICODE_LOWER_CAPABILITY"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--phase", choices=("active", "fuel", "unicode-domain", "all"), default="all")
    parser.add_argument("--swipl", type=Path)
    parser.add_argument("--swi-root", type=Path)
    args = parser.parse_args()
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
    args.evidence.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="mode06-native-") as temp:
        c = Qualification(args.evidence.resolve(), Path(temp))
        c.snapshot_sources("tests/mode_state_native_check.py", "tests/mode_state_support.py",
                           "src/mode_runtime.metta", "src/mode_text_cetta.metta", "src/mode_values_cetta.metta",
                           "src/mode_io_cetta.metta", "src/engine_active_cetta.metta", "src/fuel_cetta.metta",
                           "src/engine_modes.metta", "src/engine_modes_cetta.metta", "src/engine_modes_python.metta",
                           "src/mode_paths_cetta.metta", "src/engine_modes.py", "src/fuel_modes.py",
                           "src/loop_modes.py", "src/attention.py")
        status = "PASS_INDEPENDENT_PHASE_ONLY"
        if args.phase in ("active", "all"): active(c, args)
        if args.phase in ("fuel", "all"):
            fuel_reads(c)
            fuel_numeric_boundaries(c)
            fuel_sequences(c)
        if args.phase in ("unicode-domain", "all"): status = unicode_domain(c)
        c.save("result.json", {"phase": args.phase, "status": status, "final_full_qualification": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())

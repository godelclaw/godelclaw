"""Prepare a private Omega core with offline host bindings.

The vendored core modules are immutable. Generated runtime copies adapt the
pinned PeTTa v1.0.4 input annotations to current PeTTa demand annotations, and
explicitly retain its Empty-case reevaluation at one utility. Clock/FS/codec
are separate, explicit host adapters. Provider/channel/startup use host.metta.
"""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLOCK_BINDINGS = {"sleep", "get_time_as_string"}
FILESYSTEM_BINDINGS = {"joinPath"}
LEGACY_INPUT_DECLARATIONS = {
    "src/utils.metta": (
        "(: empty-to-bool (-> Expression Bool))",
        "(: empty-to-bool (-> Atom Bool))"),
    "src/skills.metta": (
        "(: add-skill (-> Atom String Expression Bool))",
        "(: add-skill (-> %Undefined% String Atom Bool))"),
}
LEGACY_EMPTY_BODY = """(= (empty-to-bool $predicate)
   (case (eval $predicate)
     ((Empty False)
      (True True))))"""
CURRENT_EMPTY_BODY = """(= (empty-to-bool $predicate)
   (superpose
     ((case (eval $predicate) ((True True)))
      (if (== (collapse (once (eval $predicate))) ()) False (empty)))))"""


def adapt_legacy_semantics(runtime):
    """Translate the pinned utility boundary, not general engine semantics.

    PeTTa 7037f4c src/translator.pl:363-369 holds Expression inputs without a
    guard, and evaluates Atom inputs without a guard. Current PeTTa/CeTTa use
    Atom and %Undefined%, respectively. Result types remain Bool, so the newer
    Atom-result body-holding rule cannot affect either function. The old Empty
    case also enumerates (Key, Cases), then runs negation of Key as a separate
    alternative (translator.pl:163-172). Keep that second evaluation explicitly;
    once stops at the first answer, as Prolog negation does.
    """
    ledger = []
    for relative, (before, after) in LEGACY_INPUT_DECLARATIONS.items():
        path = runtime / relative
        text = path.read_text()
        if text.count(before) != 1:
            raise ValueError(f"pinned declaration changed: {relative}")
        path.write_text(text.replace(before, after))
        ledger.append({"path": relative, "source": before, "runtime": after})
    path = runtime / "src/utils.metta"
    text = path.read_text()
    if text.count(LEGACY_EMPTY_BODY) != 1:
        raise ValueError("pinned empty-to-bool body changed")
    path.write_text(text.replace(LEGACY_EMPTY_BODY, CURRENT_EMPTY_BODY))
    ledger.append({"path": "src/utils.metta", "source": LEGACY_EMPTY_BODY,
                   "runtime": CURRENT_EMPTY_BODY})
    return ledger


def forms(text):
    """Top-level MeTTa forms, retaining the original bytes within each form."""
    depth = 0
    start = None
    quoted = escaped = comment = False
    for i, char in enumerate(text):
        if comment:
            if char == "\n":
                comment = False
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == ";":
            comment = True
        elif char == '"':
            quoted = True
        elif char == "(":
            if depth == 0:
                start = i - 1 if i and text[i - 1] == "!" else i
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]
    if depth or quoted:
        raise ValueError("unbalanced vendored source")


def prepare(root, scenario, native_fs=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.json").write_text(json.dumps(scenario))
    runtime = root / "repos/Omega"
    shutil.copytree(HERE / "upstream", runtime)
    if native_fs:
        ledger = adapt_legacy_semantics(runtime)
        (root / "legacy-semantics-adapter.json").write_text(json.dumps(ledger, indent=2))
    shutil.copy2(HERE / "fixture_host.py", root)
    if scenario.get("history"):
        (runtime / "memory/history.metta").write_text(scenario["history"])
    utility_forms = []
    for form in forms((runtime / "src/utils.metta").read_text()):
        if form == "!(import_prolog_function get_time)":
            continue
        if any(form.startswith(f"(= ({name} ") or form.startswith(f"(= ({name})")
               for name in CLOCK_BINDINGS | FILESYSTEM_BINDINGS |
               ({"string-replace"} if native_fs else set())):
            continue
        utility_forms.append(form)
    (root / "utils-clock.metta").write_text("\n".join(utility_forms) + "\n")
    # py-str definitions are taken byte-for-byte from PeTTa's library, without
    # importing its unrelated network-client initialization.
    (root / "host.metta").write_text(HOST)
    if native_fs:
        with (root / "host.metta").open("a") as stream:
            stream.write('(= (string-replace $text $separators $replacement) (py-call (fixture_host.string_replace $text $separators $replacement)))\n')
    memory_file = runtime / "src/memory.metta"
    if native_fs:
        # CeTTa's library primitive cannot yet resolve the computed prompt path.
        # Bind only these filesystem reads; the oracle runs their original forms.
        selected = [form for form in forms(memory_file.read_text())
                    if not (form.startswith("(= (getPrompt ") or
                            form.startswith("(= (getHistory)"))]
        selected += ["(= (getPrompt $provider) (py-call (fixture_host.get_prompt $provider)))",
                     "(= (getHistory) (py-call (fixture_host.get_history (maxHistory))))"]
        memory_file = root / "memory-fs.metta"
        memory_file.write_text("\n".join(selected) + "\n")
    (root / "rag.py").write_text("def init_knowledge(provider): return True\n")
    (root / "lib_llm_ext.py").write_text("def initLocalEmbedding(): return True\n")
    (root / "channels.py").write_text(
        "import fixture_host\ndef commChannelStart(channel): return True\n"
        "def commChannelReceive(): return fixture_host.receive()\n"
        "def commChannelSend(message): return fixture_host.send(message)\n")
    entry = root / "run.metta"
    entry.write_text(f'''!(import! &self (library lib_import))
!(git-import! "https://github.com/singnet/Omega.git" "" "{root}/repos")
!(import! &self "{root}/fixture_host.py")
!(import! &self "{root}/rag.py")
!(import! &self "{root}/lib_llm_ext.py")
!(import! &self "{root}/channels.py")
!(import! &self (library Omega ./src/helper.py))
!(import! &self (library Omega lib_nal))
!(import! &self (library Omega lib_pln))
!(import! &self (library Omega ./src/utils.py))
!(import! &self (library Omega ./src/providers.py))
!(import! &self "{root}/host.metta")
!(import! &self "{root}/utils-clock.metta")
!(import! &self (library Omega ./src/skills.metta))
!(import! &self (library Omega ./src/channels.metta))
!(import! &self "{memory_file}")
!(import! &self (library Omega ./src/providers.metta))
!(import! &self (library Omega ./src/loop.metta))
!(py-call (fixture_host.install_provider))
!(add-heartbeat-listener fixture-trace (|-> ($k) (fixture-heartbeat $k)))
{scenario.get("setup", "")}
!(omega)
''')
    return entry


HOST = r'''
(= (py-str-helper () $outp) $outp)
(= (py-str-helper $L $outp)
   (let* (($head (car-atom $L))
          ($tail (cdr-atom $L))
          ($outp2 (py-call (operator.add $outp (py-call (str $head))))))
     (py-str-helper $tail $outp2)))
(= (py-str $L) (py-str-helper $L ""))
(= (configure $key $default)
   (let $value (py-call (fixture_host.config $key $default))
     (add-atom &self (= ($key) $value))))
(= (initConfig) ())
(= (initLogger) ())
(= (applySecurityPolicy) ())
(= (initPlugins) ())
(= (log $level $module $message)
   (py-call (fixture_host.log $level $module (repr $message))))
(= (get_time) (py-call (fixture_host.clock)))
(= (get_time_as_string) (py-call (fixture_host.timestamp)))
(= (joinPath $parts) (py-call (helper.joinPath $parts)))
(= (fixture-heartbeat $k)
   (py-call (fixture_host.heartbeat $k (get-state &loops)
             (get-state &prevmsg)
             (py-call (fixture_host.snapshot_messages (get-state &messages_all))))))
(= (sleep $seconds)
   (py-call (fixture_host.sleep $seconds (get-state &loops)
             (get-state &prevmsg)
             (py-call (fixture_host.snapshot_messages (get-state &messages_all)))
             (get-state &nextWakeAt))))
'''

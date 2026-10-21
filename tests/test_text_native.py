"""Shared text adapters preserve Python results and answer multiplicity."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_command_dispatch_engine as dispatch

ROOT = dispatch.ROOT
sys.path.insert(0, str(ROOT / "src"))

TEXTS = [
    "", " ", "\t\r\n", "plain text", "λ ❤️ Příliš žluťoučký",
    "_newline_", "_quote_", "_apostrophe_",
    "a_newline_b_quote_c_apostrophe_d",
    "_newline__newline_", "_quote__apostrophe__quote_",
    "literal \\n, \\t and \\", "_newlin", "__newline__",
    'quoted "source" with (parentheses) and $variables',
    "before\r\nafter_newline_end",
]


class NativeTextTests(unittest.TestCase):
    def check_adapter(self, native):
        harness = dispatch.CommandDispatchEngineTest()
        binary = harness._cetta_binary()
        petta = harness._petta_root()
        if native and not binary.is_file():
            self.skipTest("set CETTA_BIN")
        if not native and (not (petta / "run.sh").is_file() or not shutil.which("swipl")):
            self.skipTest("SWI-PeTTa is unavailable")
        env = harness._cetta_env()
        engine = "cetta" if native else "petta"
        env.update(METTACLAW_ENGINE=engine, METTACLAW_ACTIVE_ENGINE=engine)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "engine"
            state.write_text(engine)
            env["METTACLAW_ENGINE_STATE_PATH"] = str(state)
            search = Path(directory) / "websearch.py"
            search.write_text("def search(text):\n    return text\n"
                              "search_tavily = search_ddg = search\n"
                              "def matches(value, expected):\n    return int(value == expected)\n")
            env["PYTHONPATH"] = str(search.parent) + os.pathsep + env["PYTHONPATH"]
            header = [
                "!(import! &self (library lib_import))",
                "!(import! &self ./src/helper.py)",
                "!(import! &self ./tests/helper_port_probe.py)",
                "!(import! &self ./src/utils)",
                f"!(import! &self {json.dumps(str(search))})",
                "!(import! &self ./src/channels)",
                "!(import! &self ./src/command_pipeline)",
            ]
            # Native strings and Python-returned text have different atom
            # representations; the command reader must preserve parsed data.
            parsed = ['()', '(item _quote_hello_quote_)', '(item "λ_newline_end")']
            for index, text in enumerate(parsed):
                argument = json.dumps(text, ensure_ascii=False)
                header.append(
                    f"!(println! (ParseCheck {index} (== (model-response-read {argument}) "
                    f"(catch (sread (py-call (helper.decode_safe_text {argument})))))))")
            if native:
                header.append("!(py-call (helper_port_probe.forbid_python_helpers))")
                header.append(
                    '!(println! (DecoderGuard (case (catch (py-call '
                    '(helper.decode_safe_text "sentinel"))) '
                    '(((Error $call $reason) 1) ($unexpected 0)))))')
            calls = [(name, text) for name in
                     ("utils:decode-safe", "search", "search-tavily", "search-ddg")
                     for text in TEXTS]
            import helper
            for index, (name, text) in enumerate(calls):
                expected = json.dumps(helper.decode_safe_text(text), ensure_ascii=False)
                argument = json.dumps(text, ensure_ascii=False)
                header.append(
                    f"!(let $answers (collapse ({name} {argument})) "
                    f"(println! (TextCheck {index} (size-atom $answers) "
                    f"(py-call (websearch.matches (car-atom $answers) {expected})))))")
            with tempfile.NamedTemporaryFile("w", dir=ROOT, suffix=".metta") as source:
                source.write("\n".join(header) + "\n")
                source.flush()
                command = ([str(binary), "--lang", "petta", "--import-mode", "ancestor-walk"]
                           if native else ["bash", str(petta / "run.sh")])
                result = subprocess.run(command + [source.name], cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=45)
            detail = result.stdout[-4000:] + result.stderr[-2000:]
            self.assertEqual(result.returncode, 0, detail)
            self.assertEqual(re.findall(r"\(ParseCheck (\d+) (true|false)\)", result.stdout),
                             [(str(i), "true") for i in range(len(parsed))], detail)
            if native:
                self.assertIn("(DecoderGuard 1)", result.stdout, detail)
            self.assertEqual(re.findall(r"\(TextCheck (\d+) (\d+) (\d+)\)", result.stdout),
                             [(str(i), "1", "1") for i in range(len(calls))], detail)

    def test_cetta_never_uses_the_python_decoder(self):
        self.check_adapter(native=True)

    def test_swi_retains_the_python_adapter(self):
        self.check_adapter(native=False)


if __name__ == "__main__":
    unittest.main()

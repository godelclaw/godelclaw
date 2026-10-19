"""File skill conformance on CeTTa (Python backend disabled) and SWI-PeTTa."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_file_read_engine import ROOT, metta
sys.path.insert(0, str(ROOT / "src"))
import fsops


class FileToolsEngineTests(unittest.TestCase):
    def contract(self, engine):
        petta = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
        cetta = Path(os.environ.get("CETTA_BIN", ""))
        if not (petta / "run.sh").is_file() or (engine == "cetta" and not cetta.is_file()):
            self.skipTest("engine checkout/executable unavailable")
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            lines = []
            def call(name, *args):
                return "(" + name + " " + " ".join(metta(x) for x in args) + ")"
            def check(expression, expected):
                lines.append(f"!(println! FILE_CASE_{len(lines)})")
                lines.append(f"!(let $result {expression} (py-call (file_tools_probe.check $result {metta(expected)})))")
            text = work / "text"
            text.write_bytes("first\r\nλ 🌸\rthird\n\n".encode() + b"x" * 10000 + b"\xff\nlast")
            empty = work / "empty"; empty.write_bytes(b"")
            for path in (text, empty):
                check(call("read-lines", str(path)), fsops.read_lines(path))
                for start, count in ((1, 2), (2, 3), (5, 1), (6, 10), (99, 1), (0, 0), (-3, -1), ("2", "2")):
                    check(call("read-lines", str(path), start, count), fsops.read_lines(path, start, count))
            check(call("read-lines", str(work / "missing")), fsops.read_lines(work / "missing"))
            check(call("read-lines", str(work)), fsops.read_lines(work))
            tree = work / "tree"; tree.mkdir()
            (tree / "a").mkdir(); (tree / "a" / "nested").mkdir()
            (tree / "a" / "nested" / "deep").write_text("needle\n")
            (tree / "z").mkdir(); (tree / "z" / "leaf").write_text("NEEDLE\n")
            (tree / ".git").mkdir(); (tree / ".git" / "ignored").write_text("needle\n")
            (tree / "b").write_text("needle one\nλ_１２\nneedle two   \n[broken\n")
            (tree / "c").write_bytes(b"binary\0needle\n")
            (tree / "d").write_bytes(b"needle\xff\rnext\r\n")
            (tree / "link-file").symlink_to(tree / "b")
            (tree / "link-dir").symlink_to(tree / "a", target_is_directory=True)
            (tree / "broken-link").symlink_to(tree / "absent")
            for depth, limit, offset in ((1, 100, 0), (2, 100, 0), (5, 100, 0), (9, 2, 0), (9, 2, 2), (9, 2, 99)):
                check(call("ls-tree", str(tree), depth, limit, offset), fsops.ls_tree(tree, depth, limit, offset))
            check(call("ls-tree", str(text)), fsops.ls_tree(text))
            patterns = ("needle", "(?i)needle", "^needle", r"needle(?= two)", r"(?P<word>needle) (?P=word)", r"\w+", r"\bλ_１２\b", "[broken", r"needle.*\Z", r"^$", "")
            for pattern in patterns:
                for limit, offset in ((2, 0), (100, 0), (2, 2)):
                    expected = fsops.grep_files(pattern, tree, limit, offset)
                    # MeTTa repr uses double quotes for string literals.
                    expected = expected.replace(repr(pattern), metta(pattern))
                    check(call("grep-files", pattern, str(tree), limit, offset), expected)
            check(call("file-io:exists", str(text)), "True")
            check(call("file-io:exists", str(work)), "False")
            check(call("file-io:exists", str(work / "absent")), "False")
            target = work / "write"; target.write_bytes(b"previous\r\n"); target.chmod(0o640)
            def written(expression, data, previous, mode):
                lines.append(f"!(let $result {expression} (py-call (file_tools_probe.written $result {metta(str(target))} {metta(data.hex())} {metta(previous.hex() if previous is not None else 'none')} {mode})))")
            nul_input = work / "nul-input"; nul_input.write_bytes("Gödel\0x\n".encode())
            written(f"(let $content (read-file {metta(str(nul_input))}) (write-file {metta(str(target))} $content))", "Gödel\0x\n".encode(), b"previous\r\n", 0o640)
            written(call("append-file", str(target), "🌸"), "Gödel\0x\n🌸\n".encode(), "Gödel\0x\n".encode(), 0o640)
            check(call("edit-file", str(work / "absent"), "", "z"), "edit failed: old-string is empty")
            check(call("edit-file", str(target), "", "z"), "edit failed: old-string is empty")
            check(call("edit-file", str(target), "absent", "z"), f"edit failed: old-string occurs 0 times in {target}; it must occur exactly once")
            written(call("edit-file", str(target), "x", "XX"), "Gödel\0XX\n🌸\n".encode(), "Gödel\0x\n🌸\n".encode(), 0o640)
            check(call("edit-file", str(target), "X", "z"), f"edit failed: old-string occurs 2 times in {target}; it must occur exactly once")
            written(call("write-file", str(target), ""), b"", "Gödel\0XX\n🌸\n".encode(), 0o640)
            crlf_input = work / "crlf-input"; crlf_input.write_bytes(b"old\r\nend\r")
            written(f"(let $content (read-file {metta(str(crlf_input))}) (write-file {metta(str(target))} $content))", b"old\r\nend\r", b"", 0o640)
            written(call("edit-file", str(target), "old", "new"), b"new\nend\n", b"old\r\nend\r", 0o640)
            bad_input = work / "bad-input"; bad_input.write_bytes(b"bad\xff")
            expression = call("edit-file", str(bad_input), "bad", "ok")
            lines.append(f'!(let $result {expression} (py-call (file_tools_probe.failed $result "edit failed:" {metta(str(bad_input))} "626164ff")))')
            new = work / "new"
            lines.append(f"!(let $result {call('write-file', str(new), 'new')} (py-call (file_tools_probe.written $result {metta(str(new))} \"6e6577\" \"none\" 384)))")
            target = work / "nested" / "state.json"
            for content in ('{"default":"full"}', '{"default":"mid","name":"λ 🌸"}'):
                expression = '(file-io:write-present "replace" ' + call(
                    "file-io:update", str(target), "replace", content, "") + ')'
                written(expression, content.encode(), None, 0o600)
            code = "!(import! &self (library lib_import))\n!(import! &self ./tests/file_tools_probe.py)\n!(import! &self ./src/file_io)\n"
            if engine == "cetta": code += "!(py-call (file_tools_probe.forbid_python))\n"
            code += "\n".join(lines) + "\n!(println! FILE_TOOLS_CONTRACT_OK)\n"
            local = ROOT / (".file-tools-probe-" + work.name + ".metta"); local.write_text(code)
            python = Path(os.environ.get("PETTA_PY_ENV", Path.home() / "miniforge3/envs/petta"))
            env = dict(os.environ, METTACLAW_ACTIVE_ENGINE=engine, METTACLAW_ENGINE_STATE_PATH=str(work / "engine"), METTACLAW_INSTANCE="test-agent", XDG_STATE_HOME=str(work / "state"), PETTA_PATH=str(petta), PYTHONHOME=str(python), PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"))), PATH=str(python / "bin") + os.pathsep + os.environ.get("PATH", ""), LD_LIBRARY_PATH=str(python / "lib") + os.pathsep + os.environ.get("LD_LIBRARY_PATH", ""))
            command = [str(cetta), "--lang", "petta", "--import-mode", "ancestor-walk", str(petta / "lib/lib_import.metta"), str(local)] if engine == "cetta" else ["bash", str(petta / "run.sh"), str(local), "default"]
            try:
                run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            finally:
                local.unlink(missing_ok=True)
            if run.returncode:
                print(run.stdout[-4000:], run.stderr[-4000:])
            self.assertEqual(run.returncode, 0, (run.stdout + run.stderr)[-4000:])
            self.assertIn("FILE_TOOLS_CONTRACT_OK", run.stdout)
            self.assertNotIn("Error", run.stdout)
            self.assertNotIn("AssertionError", run.stderr)

    def test_native_without_python_filesystem(self):
        self.contract("cetta")

    def test_swi_petta(self):
        self.contract("petta")

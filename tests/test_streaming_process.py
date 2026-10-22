"""Exercise the native owner with real delayed processes and durable artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_coding_session as coding
sys.path.insert(0, str(coding.ROOT / "src"))


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN")
class ProcessStreamTests(unittest.TestCase):
    setUp = coding.CodingSessionTests.setUp
    tearDown = coding.CodingSessionTests.tearDown
    run_probe = coding.CodingSessionTests.run_probe

    def records(self):
        return [json.loads(line) for p in (self.directory/"display").glob("*.jsonl")
                for line in p.read_text().splitlines()]

    def test_output_is_visible_while_running_with_utf8_and_terminal_receipt(self):
        self.env["METTACLAW_STREAM_DIR"] = str(self.directory/"display")
        entry = self.directory/"native.metta"
        command = "printf 'first '; printf '\\360\\237'; sleep .2; printf '\\220\\210\\n'; sleep 1; printf 'last\\n' >&2; exit 7"
        args = json.dumps(json.dumps(dict(cmd=command)))
        directory = json.dumps(str(self.directory))
        entry.write_text('!(import! &self (library lib_import))\n!(import! &self '+
            json.dumps(str(coding.ROOT/'src/coding_session.metta'))+')\n'+
            '!(println! (coding:exec-owned '+directory+' '+directory+' (json-parse '+args+')))')
        process = subprocess.Popen([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(entry)],
            cwd=coding.ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+3
            while not any("🐈" in r.get("text", "") for r in self.records()) and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertIsNone(process.poll(), self.records())
            self.assertIn("first 🐈\n", "".join(r.get("text", "") for r in self.records() if r["kind"]=="tool.output"))
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn("Exit code: 7", out)
            final = [r for r in self.records() if r["kind"]=="tool.finished"][-1]
            self.assertEqual(final["exit_code"], 7)
            self.assertEqual(Path(final["artifact"]).read_text(), "first 🐈\nlast\n")
        finally:
            if process.poll() is None: process.kill(); process.communicate()

    def test_yield_poll_repeat_and_cancel_never_respawn(self):
        root = json.dumps(str(self.directory))
        command = "echo once >> count; printf start; sleep 4; printf finish"
        args = json.dumps(json.dumps(dict(cmd=command,yield_time_ms=0)))
        forms = f'''!(let* (($report (coding:exec-owned {root} {root} (json-parse {args})))
              ($id (str:trim (car-atom (cdr-atom (str:split ":" (car-atom (str:split "\\n" $report)))))))
              ($args (JsonObject ((JsonPair "execution_id" (JsonString $id)) (JsonPair "yield_time_ms" (JsonNumber "200"))))))
            (progn (println! (INITIAL $report))
              (println! (POLL (coding:poll {root} $args)))
              (println! (CANCEL (coding:cancel {root} $args)))
              (println! (AGAIN (coding:poll {root} $args)))))'''
        result = self.run_probe(forms)
        self.assertIn("Still running", result.stdout)
        self.assertIn("signaled", result.stdout)
        self.assertEqual((self.directory/"count").read_text(), "once\n")
        self.assertEqual(len(list((self.directory/"executions").glob("*.log"))), 1)

    def test_new_runtime_observes_uncertainty_without_running(self):
        import coding_io
        identity = "a"*32
        log = coding_io.execution_create(self.directory, identity, "do not replay")
        Path(log).write_bytes(b"partial\xff")
        root = json.dumps(str(self.directory))
        args = json.dumps(json.dumps(dict(execution_id=identity)))
        result = self.run_probe(f'!(println! (coding:poll {root} (json-parse {args})))')
        self.assertIn("uncertain", result.stdout)
        self.assertIn("not replayed", result.stdout)
        self.assertEqual(Path(log).read_bytes(), b"partial\xff")


if __name__ == "__main__": unittest.main()

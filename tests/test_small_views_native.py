"""The whole context caller with the six small views, against the Python bundle.

Each view on its own is native: tests/unit/small_views.metta."""
import base64
import contextlib
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from test_context_bundle_native import CALLER, HEADER, ROOT, guest, guest_env, native_coding_sessions, q, save_evidence
import small_views_probe as probe
import context_sources
import helper
import effect_receipts
from receipt_context_fixture import receipt_bytes

# Component tests deliberately supply arbitrary argv prefixes (not the real
# one-executable engine_command API). Keep that explicit counted TEST control
# boundary after project06 removes the runtime Python engine-command boundary.
# Actual native engine commands are qualified separately by mode-state tests.
NATIVE_COMMAND_FIXTURE = [
    '!(match &self (= (engine-native:command $name) $body) '
    '(remove-atom &self (= (engine-native:command $name) $body)))',
    '(= (engine-native:command $name) (py-call (small_views_probe.engine_command $name)))']
NATIVE_HEADER = HEADER + ["!(import! &self ./tests/small_views_probe.py)",
                          "!(import! &self ./src/small_views_cetta.metta)"] + NATIVE_COMMAND_FIXTURE
SOURCES = tuple(probe.MODULES)


def encoded(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
    return base64.b64encode(raw).decode()


def oracle(source):
    try:
        value = probe.MODULES[source].view()
        return ["missing"] if value is None else ["value", value]
    except Exception:
        return ["unavailable"]


class SmallViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A CeTTa guest renders coding-sessions natively; its oracle bundle
        # shows that view of the same workspace.
        with tempfile.TemporaryDirectory() as scratch, contextlib.chdir(ROOT):
            cls.coding_sessions = native_coding_sessions(Path(scratch))

    def setUp(self):
        self.enterContext(contextlib.chdir(ROOT))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.registry = self.directory / "registry.json"
        self.iter = self.directory / "iter"
        self.iter.mkdir()
        self.registry.write_text('{"schema":1,"projects":[]}')
        self.settings = {"METTACLAW_ITER_PROCESS_DIR": str(self.iter),
                         "METTACLAW_PROJECT_REGISTRY_PATH": str(self.registry)}
        self.index = 0

    def compare(self, source, steps, caller=False, swi=False, boot=False):
        self.index += 1
        paths = []
        expected = []
        expected_probes = []
        oracle_receipt_views = []
        # Oracle and guest must begin with the SAME bytes and artifact key.
        touched = {name for step in steps for field in ("files", "replace_files", "utime")
                   for name in step.get(field, {})}
        touched.update(step["command"][2] for step in steps if len(step.get("command", [])) >= 3)
        initial = {name: (Path(name).read_bytes(), Path(name).stat())
                   if Path(name).is_file() else None for name in touched}
        env = guest_env(self.directory, "petta" if swi else "cetta", **self.settings)
        view = self.coding_sessions
        with mock.patch.object(probe, "coding_sessions", None if swi else (lambda: view)), \
                mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                probe.engine_modes, "engine_command", probe.engine_command), mock.patch.object(
                context_sources, "_loaders", probe.retained_loaders), mock.patch.object(
                context_sources, "_PROJECTOR", context_sources.Projector()), mock.patch.object(
                effect_receipts, "view", probe.receipts.oracle_view):
            probe.project_capabilities._cache.clear()
            probe.project_evidence._cache = None
            probe.command = None
            probe.counter_path = None
            helper._change_marks.clear()
            for index, step in enumerate(steps):
                path = self.directory / ("step-%d-%d.json" % (self.index, index))
                path.write_text(json.dumps(step))
                paths.append(path)
                probe.apply(path)
                probe.receipts.reset()
                expected.append(context_sources.bundle() if caller else oracle(source))
                expected_probes.append(probe.probe_events())
                oracle_receipt_views.append(probe.receipts.calls.copy())
        for name, snapshot in initial.items():
            path = Path(name)
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                raw, info = snapshot
                path.write_bytes(raw)
                path.chmod(info.st_mode & 0o777)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        log = self.directory / ("output-%d" % self.index)
        lines = (HEADER + ["!(import! &self ./tests/small_views_probe.py)"] + ([] if boot else CALLER)
                 if caller else list(NATIVE_HEADER))
        lines += ["!(py-call (small_views_probe.setup %d %d))" % (not swi, caller)]
        if caller and not swi:
            lines += NATIVE_COMMAND_FIXTURE
        for path in paths:
            lines += ["!(py-call (small_views_probe.apply %s))" % q(path)]
            expression = ("(addition-context-sources)" if caller else
                          '(json:stringify (JsonArray (sv:load %s)))' % q(source))
            lines += ["!(py-call (small_views_probe.record %s %s))" % (q(log), expression)]
        guest(self.directory, lines, swi=swi, boot=boot, engine="petta" if swi else "cetta",
              METTACLAW_OBSERVED_STRATEGY="0", **self.settings)
        actual = [json.loads(line) for line in log.read_text().splitlines()]
        texts = [row["text"] if caller else json.loads(row["text"]) for row in actual]
        save_evidence("small-view-parity", {"source": source, "steps": steps, "caller": caller,
                                           "swi": swi, "expected": expected, "actual": actual})
        save_evidence("small-view-receipt-crossings", {"caller": caller, "swi": swi,
                      "oracle": oracle_receipt_views, "guest": [row["receipt_views"] for row in actual]})
        self.assertEqual(texts, expected)
        self.assertTrue(all(row["forbidden"] == 0 for row in actual))
        self.assertTrue(all(row["receipt_views"]["native"] == 0 for row in actual))
        self.assertEqual(actual[-1]["receipt_views"]["python"], len(steps) if caller and swi else 0)
        self.assertEqual([row["probe_events"] for row in actual], expected_probes)
        if caller and not swi:
            retained = {s.source_id for s in context_sources.SOURCE_SPECS} - set(SOURCES) - {"work-state", "affect-gestalt", "effect-receipts", "coding-sessions"}
            self.assertEqual(actual[-1]["calls"], {key: len(steps) for key in retained})
            self.assertEqual(actual[-1]["engine_calls"], len(steps))
            # The caller asks the strategy host whether the MeTTa client
            # renders the chat window or the route view. Whether the observed
            # strategy is on is read natively and never crosses.
            boundaries = {"prepare": len(steps), "retained-load": len(retained) * len(steps),
                          "engine-command": len(steps), "conversation-mode": len(steps)}
            self.assertEqual(actual[-1]["boundaries"], boundaries)
        return actual

    def file_steps(self, variable, values):
        path = self.directory / variable
        return [{"env": {variable: str(path)}, "files": {str(path): encoded(value)}}
                for value in values]

    def fake(self):
        path = self.directory / "fake-cetta"
        mode = self.directory / "mode"
        count = self.directory / "count"
        # Absolute interpreter is generated from the running Python, not a
        # committed machine-local tool path. The fake never contacts a peer.
        path.write_text("#!" + sys.executable + "\n" + '''import base64, json, os, pathlib, sys, time
mode = json.loads(pathlib.Path(sys.argv[1]).read_text())
with pathlib.Path(sys.argv[2]).open("a") as out: out.write(sys.argv[-1] + "\\n")
if mode.get("timeout") or mode.get("timeout_on") == sys.argv[-1]: time.sleep(4)
if mode.get("fail") or mode.get("fail_on") == sys.argv[-1]: sys.exit(7)
if mode.get("raw_stdout"):
    os.write(1, base64.b64decode(mode["raw_stdout"])); sys.exit(0)
if mode.get("raw_stderr"): os.write(2, base64.b64decode(mode["raw_stderr"]))
print(mode.get("version", "fixture 日 r1") if sys.argv[-1] == "--version" else mode.get("inventory", "Languages:\\n  petta implemented Native 日 🙂\\n  future planned Not yet\\nignored implemented no indent"))
''')
        path.chmod(0o700)
        return path, mode, count

    def test_whole_caller_all_six_disabled_changing_and_failure_containment(self):
        query = self.directory / "queries"
        commitment = self.directory / "commitment"
        task = self.directory / "task"
        fake, mode, count = self.fake()
        steps = [{"command": [str(fake), str(mode), str(count)],
                  "env": {"METTACLAW_ACTIVE_QUERIES_PATH": str(query),
                          "METTACLAW_COMMITMENT_STATE_PATH": str(commitment),
                          "METTACLAW_TASK_PHASE_PATH": str(task)},
                  "files": {str(query): encoded(["日🙂"]), str(mode): encoded({}),
                            str(commitment): encoded({"phase": "need-trust", "revision": 2}),
                            str(task): encoded({"schema": 1, "task_id": "fixture"})}},
                 {"files": {str(query): encoded(b"["), str(commitment): encoded({"phase": "bad"}),
                            str(task): encoded({"schema": 2})}},
                 {"env": {"METTACLAW_ACTIVE_QUERIES_PATH": None,
                          "METTACLAW_COMMITMENT_STATE_PATH": None,
                          "METTACLAW_TASK_PHASE_PATH": None}}]
        self.compare(None, steps, caller=True)

    def test_caller_capability_and_evidence_stale_known_retry_and_recovery(self):
        fake, mode, count = self.fake()
        stamp = fake.stat()
        steps = [{"command": [str(fake), str(mode), str(count)],
                  "files": {str(mode): encoded({})}},
                 {"files": {str(mode): encoded({"fail": True}), str(self.registry): encoded(b"{")},
                  "utime": {str(fake): [stamp.st_atime_ns, stamp.st_mtime_ns + 1]}, "sleep": 2.05},
                 {},
                 {"files": {str(mode): encoded({}),
                            str(self.registry): encoded({"schema": 1, "projects": []})}}]
        rows = self.compare(None, steps, caller=True)
        for source in ("project-capabilities", "project-evidence"):
            statuses = [row["text"].split("SOURCE[" + source + "] status=", 1)[1].split(" ", 1)[0]
                        for row in rows]
            self.assertEqual(statuses, ["known", "stale-known", "stale-known", "known"])

    def test_complete_caller_real_receipts_absent_and_populated(self):
        path = str(self.directory / "receipts.jsonl")
        entry = {"turn": 7, "disposition": "returned", "command": "generated command",
                 "result": "generated 日🙂\0result"}
        steps = [{"files": {path: None}}, {"files": {path: encoded(receipt_bytes([entry]))}},
                 {"files": {path: encoded(b"")}}]
        rows = self.compare(None, steps, caller=True)
        self.assertIn("SOURCE[effect-receipts] status=absent", rows[0]["text"])
        self.assertIn("AUTHORITATIVE BROKER RECEIPTS", rows[1]["text"])
        self.assertIn("generated 日🙂\0result", rows[1]["text"])
        self.assertIn("SOURCE[effect-receipts] status=absent", rows[2]["text"])

    def test_isolated_production_boot_all_six_python_loaders_disabled(self):
        library = Path(os.environ.get("PETTA_LIB_CHROMADB", ROOT / "repos" / "petta_lib_chromadb"))
        self.assertTrue((library / "lib_chromadb.metta").is_file())
        shutil.copytree(library, self.directory / "repos" / "petta_lib_chromadb",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        rendered = subprocess.run([sys.executable, str(ROOT / "scripts/render_channel_config.py"), "telegram"],
                                  env=guest_env(self.directory), capture_output=True, text=True, check=True)
        (self.directory / "channel.metta").write_text(rendered.stdout)
        self.settings["METTACLAW_TELEGRAM_CLIENT"] = "metta"
        fake, mode, count = self.fake()
        self.compare(None, [{"command": [str(fake), str(mode), str(count)],
                             "files": {str(mode): encoded({})}}, {}], caller=True, boot=True)

    def test_metadata_failure_retains_only_its_source_at_complete_caller(self):
        fake, mode, count = self.fake()
        mode.write_text("{}")
        step, log = self.directory / "metadata-step", self.directory / "metadata-caller"
        step.write_text(json.dumps({"command": [str(fake), str(mode), str(count)]}))
        record = '!(py-call (small_views_probe.record %s (addition-context-sources)))' % q(log)
        remove = '!(match &self (= (fs:metadata $path $links) $body) (remove-atom &self (= (fs:metadata $path $links) $body)))'
        guest(self.directory, HEADER + CALLER + [
            '!(import! &self ./tests/small_views_probe.py)',
            '!(py-call (small_views_probe.setup 1 1))',
            *NATIVE_COMMAND_FIXTURE,
            '!(py-call (small_views_probe.apply %s))' % q(step), record, remove,
            '(= (fs:metadata $path $links) (if (== $links follow-modified) '
            '(fs:failed fs:denied metadata $path 13 (fs:progress 0 known-position) "fixture") '
            '(__cetta_lib_fs_metadata $path $links)))', record, remove,
            '(= (fs:metadata $path $links) (__cetta_lib_fs_metadata $path $links))', record],
              METTACLAW_OBSERVED_STRATEGY="0", **self.settings)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([row["text"].split('SOURCE[project-capabilities] status=', 1)[1].split(' ', 1)[0]
                          for row in rows], ["known", "stale-known", "known"])
        neighbors = [{key: status for key, status in re.findall(r"SOURCE\[([^]]+)\] status=([^ ]+)", row["text"])
                      if key != "project-capabilities"} for row in rows]
        self.assertTrue(all(row["forbidden"] == 0 for row in rows))
        self.assertEqual(neighbors, [neighbors[0]] * 3)
        self.assertEqual(len(neighbors[0]), len(context_sources.SOURCE_SPECS) - 1)
        self.assertEqual(len(count.read_text().splitlines()), 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)

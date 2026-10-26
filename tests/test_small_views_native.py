"""Six read-only native loaders against their unchanged Python oracles."""
import base64
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

from test_context_bundle_native import CALLER, HEADER, ROOT, guest, guest_env, q, save_evidence
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
    def setUp(self):
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
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
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
            retained = {s.source_id for s in context_sources.SOURCE_SPECS} - set(SOURCES) - {"work-state", "affect-gestalt", "effect-receipts"}
            self.assertEqual(actual[-1]["calls"], {key: len(steps) for key in retained})
            self.assertEqual(actual[-1]["engine_calls"], len(steps))
            boundaries = {"prepare": len(steps), "retained-load": 4 * len(steps),
                          "engine-command": len(steps),
                          "action-strategy": 2 * len(steps)}
            if boot:
                boundaries["conversation-mode"] = len(steps)
                # Boot may retain a pre-instrumentation enabled_flag callable.
                # These witnesses are NOT an engine-level full-turn census.
                observed = actual[-1]["boundaries"].copy()
                self.assertIn(observed.pop("action-strategy", 0), (0, len(steps), 2 * len(steps)))
                boundaries.pop("action-strategy")
            else:
                observed = actual[-1]["boundaries"]
            self.assertEqual(observed, boundaries)
        return actual

    def file_steps(self, variable, values):
        path = self.directory / variable
        return [{"env": {variable: str(path)}, "files": {str(path): encoded(value)}}
                for value in values]

    def test_active_queries_present_absent_malformed_unicode_changing(self):
        path = self.directory / "queries"
        steps = [{"env": {"METTACLAW_ACTIVE_QUERIES_PATH": None}}]
        steps += self.file_steps("METTACLAW_ACTIVE_QUERIES_PATH", [
            [" x ", "x", "active-goals", "日🙂", "\u001c é \u3000", 'quote"\\\n\x00'],
            [], {}, [" "], [4], b"not json", b'"\xff"', ["recovered"]])
        steps += [{"env": {"METTACLAW_ACTIVE_QUERIES_PATH": str(path)}}]
        self.compare("active-query-declaration", steps)

    def test_commitment_present_absent_malformed_unicode_changing(self):
        steps = [{"env": {"METTACLAW_COMMITMENT_STATE_PATH": None}}]
        steps += self.file_steps("METTACLAW_COMMITMENT_STATE_PATH", [
            {"phase": "need-trust", "revision": 3, "finished": True},
            {"phase": "stopped", "effects": " +1_234 ", "finished": []},
            {"phase": "goal-satisfied", "revision": -2.8, "finished": "日"},
            {"phase": "need-launch", "revision": " +١_２٣ "},
            {"phase": "need-launch", "revision": "\u001c2"},
            {"phase": "unknown 日"}, [], b"{", {"phase": "need-create"}])
        self.compare("commitment-state", steps)

    def test_task_phase_present_absent_malformed_unicode_changing(self):
        steps = [{"env": {"METTACLAW_TASK_PHASE_PATH": None}}]
        steps += self.file_steps("METTACLAW_TASK_PHASE_PATH", [
            {"schema": 1, "task_id": "日🙂", "revision": 4, "evidence_revision": "r\\\"",
             "phases": {"z": {"status": "pending"}, "日": {"status": "active"},
                        "a": {"status": "completed"}, "ignored": []}},
            {"schema": 1, "phases": {"b": {"status": "active"}, "a": {"status": "active"}}},
            {"schema": 1, "phases": {"🙂": {"status": "completed"}}},
            {"schema": True, "task_id": [True, None, {"日": "quote'\"\n\u001c\u3000"}],
             "revision": None, "evidence_revision": {"a": "it's", "b": "\x00🙂\ue000"}},
            *({"schema": 1, "revision": number} for number in
              (1.0, 1e20, 1e15, 1e14, 1e-4, 1e-5, 1e-7, -1e15, 1.2345678901234567)),
            b'{"schema":1,"phases":{"a":{"status":"active"},"a":{"status":"pending"}}}',
            {"schema": 2}, {"schema": 1, "phases": []}, b"{", [], {"schema": 1}])
        self.compare("task-phase", steps)

    @unittest.expectedFailure
    def test_capability_gap_json_lone_surrogate_has_no_native_scalar(self):
        # Explicit qualification gap, NOT a pass: native JSON rejects a lone
        # surrogate which Python accepts. No Python-loader fallback is allowed.
        self.compare("active-query-declaration", self.file_steps(
            "METTACLAW_ACTIVE_QUERIES_PATH", [b'["\\ud800"]']))

    @unittest.expectedFailure
    def test_capability_gap_json_nonfinite_python_extension(self):
        self.compare("task-phase", self.file_steps(
            "METTACLAW_TASK_PHASE_PATH", [b'{"schema":1,"revision":NaN}']))

    def test_development_empty_unicode_raw_bytes_changing_and_nonregular(self):
        one, two = self.iter / "日.metta", self.iter / "_a.metta"
        steps = [{}, {"files": {str(one): encoded(b"(hello)\r\n\xff\0"),
                                str(two): encoded("🙂".encode())}},
                 {"files": {str(one): encoded(b"changed"), str(two): None}}]
        self.compare("development-state", steps)
        (self.iter / "bad.metta").mkdir()
        self.compare("development-state", [{}])
        (self.iter / "bad.metta").rmdir()
        (self.iter / "link.metta").symlink_to(one.name)
        self.compare("development-state", [{}])
        (self.iter / "link.metta").unlink()
        one.chmod(0)
        try:
            self.compare("development-state", [{}])
        finally:
            one.chmod(0o600)

    def test_development_inner_error_object(self):
        with mock.patch.object(probe.iter_authoring, "list_transformations", side_effect=ValueError("fixture")):
            expected = probe.development_state.view()
        log = self.directory / "inner-error"
        guest(self.directory, NATIVE_HEADER + [
            '!(py-call (small_views_probe.setup 1 0))',
            '!(match &self (= (sv:iter) $body) (remove-atom &self (= (sv:iter) $body)))',
            '(= (sv:iter) (Error sv:iter ValueError))',
            '!(py-call (small_views_probe.record %s (json:stringify (JsonArray (sv:load "development-state")))))' % q(log)],
              **self.settings)
        row = json.loads(log.read_text())
        self.assertEqual(json.loads(row["text"]), ["value", expected])
        self.assertEqual(row["forbidden"], 0)

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

    def test_capability_failure_timeout_inventory_and_exact_cache(self):
        path, mode, count = self.fake()
        script = path.read_bytes()
        stamp = path.stat()
        command = [str(path), str(mode), str(count)]
        steps = [{"command": command, "files": {str(mode): encoded({})}},
                 {"files": {str(mode): encoded({"fail": True})}},  # cache hit
                 {"utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns + 1]}},
                 {},  # failure is NOT cached; retry
                 {"files": {str(mode): encoded({"version": "changed 🙂"})}},
                 {"files": {str(mode): encoded({"timeout": True})},
                  "utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns + 2]}},
                 {"files": {str(mode): encoded({"inventory": "no inventory"})}},
                 {"files": {str(mode): encoded({})}},
                 {"command": [str(self.directory / "missing")]},
                 {"command": command},  # absence leaves the previous cache intact
                 {"command": command + ["extra-prefix-argument"]},  # command is part of the key
                 {"files": {str(path): encoded(script + b"\n")},
                  "utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns + 2]}},  # size only
                 {"files": {str(path): encoded(script + b" "), str(mode): encoded({"fail": True})},
                  "utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns + 2]}}]  # identity retained
        self.compare("project-capabilities", steps)
        probes = count.read_text().splitlines()
        # Per-step probe logs, not just final values, are compared in compare().
        # 2 + 0 + 1 + 1 + 2 + 1 + 2 + 2 + 0 + 0 + 2 + 2 + 0 = 15 on EACH route.
        self.assertEqual(len(probes), 15)

    def test_capability_cache_ignores_replaced_inode(self):
        path, mode, count = self.fake()
        raw, stamp = path.read_bytes(), path.stat()
        rows = self.compare("project-capabilities", [
            {"command": [str(path), str(mode), str(count)], "files": {str(mode): encoded({})}},
            {"replace_files": {str(path): encoded(raw)},
             "files": {str(mode): encoded({"fail": True})},
             "utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns]}},
            {"utime": {str(path): [stamp.st_atime_ns, stamp.st_mtime_ns + 1]}}])
        self.assertEqual(len(count.read_text().splitlines()), 3)  # hit, then mtime invalidation
        self.assertEqual(len(rows[-1]["replacements"]), 1)
        replacement = rows[-1]["replacements"][0]
        self.assertNotEqual(replacement["old_inode"], replacement["new_inode"])

    def test_capability_cache_follows_symlink_target_and_missing_artifact(self):
        target, mode, count = self.fake()
        link = self.directory / "artifact-link"
        link.symlink_to(target.name)
        stamp = target.stat()
        self.compare("project-capabilities", [
            {"command": [str(link), str(mode), str(count)], "files": {str(mode): encoded({})}},
            {"files": {str(mode): encoded({"fail": True})}},
            {"utime": {str(target): [stamp.st_atime_ns, stamp.st_mtime_ns + 1]}},
            {"files": {str(mode): encoded({"version": "symlink recovery"})}},
            {"files": {str(target): None}}])
        self.assertEqual(len(count.read_text().splitlines()), 5)

    def test_artifact_key_matches_python_nanoseconds_and_size(self):
        target, mode, count = self.fake()
        command = [str(target), str(mode), str(count)]
        config, log = self.directory / "key-config", self.directory / "keys"
        config.write_text(json.dumps({"command": command}))
        lines = NATIVE_HEADER + ["!(py-call (small_views_probe.setup 1 0))",
                                "!(py-call (small_views_probe.apply %s))" % q(config)]
        expected = []
        for index, nanoseconds in enumerate((target.stat().st_mtime_ns, 1, -1)):
            path = self.directory / ("key-step-%d" % index)
            path.write_text(json.dumps({"utime": {str(target): [nanoseconds, nanoseconds]}}))
            os.utime(target, ns=(nanoseconds, nanoseconds))
            info = target.stat()
            expected.append([command, info.st_mtime_ns, info.st_size])
            lines += ["!(py-call (small_views_probe.apply %s))" % q(path),
                      "!(let ($command $mtime $size) (sv:artifact-key (sv:command "
                      '(py-call (engine_modes.engine_command "cetta")))) '
                      '(py-call (small_views_probe.record %s (json:stringify '
                      '(JsonArray ((JsonArray $command) $mtime $size))))))' % q(log)]
        guest(self.directory, lines, **self.settings)
        self.assertEqual([json.loads(json.loads(line)["text"]) for line in log.read_text().splitlines()], expected)

    def test_capability_inventory_failure_timeout_and_strict_stream_decoding(self):
        path, mode, count = self.fake()
        steps = [{"command": [str(path), str(mode), str(count)],
                  "files": {str(mode): encoded(value)}} for value in (
                      {"fail_on": "--list-languages"}, {"timeout_on": "--list-languages"},
                      {"raw_stdout": base64.b64encode(b"\xff").decode()},
                      {"raw_stderr": base64.b64encode(b"\xff").decode()},
                      {"version": ""},
                      {"inventory": "Header\n \u001fname\u001fimplemented\u001fUnicode 日 🙂\u001f\n\tplan\tplanned\tdesc"})]
        self.compare("project-capabilities", steps)

    def test_evidence_registry_reports_git_unicode_malformed_changing(self):
        repo = self.directory / "checkout"
        repo.mkdir()
        env = guest_env(self.directory)
        for args in (["init", "-b", "fixture"], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, check=True)
        reports = self.directory / "reports" / "日"
        reports.mkdir(parents=True)
        for i in range(6):
            (reports / ("%d.md" % i)).write_bytes(("text\n#  日 🙂 %d\t title\n" % i).encode() + b"\xff")
        registry = {"schema": 1, "projects": [{"id": "日", "checkouts": [{"path": str(repo)}]}]}
        steps = [{"files": {str(self.registry): encoded(registry)},
                  "env": {"METTACLAW_REPORTS_ROOT": str(reports.parent)}},
                 {"files": {str(self.registry): encoded(b"{")}},  # cached
                 {"sleep": 2.05},  # now unavailable, not cached
                 {"files": {str(self.registry): encoded({"schema": 1, "projects": []})}}]
        self.compare("project-evidence", steps)

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

    def test_swi_caller_keeps_all_python_loaders(self):
        self.compare(None, [{}, {}], caller=True, swi=True)

    def test_complete_caller_real_receipts_absent_and_populated(self):
        path = str(self.directory / "receipts.jsonl")
        entry = {"turn": 7, "disposition": "returned", "command": "generated command",
                 "result": "generated 日🙂\0result"}
        steps = [{"files": {path: None}}, {"files": {path: encoded(receipt_bytes([entry]))}},
                 {"files": {path: encoded(b"")}}]
        for swi in (False, True):
            with self.subTest(swi=swi):
                rows = self.compare(None, steps, caller=True, swi=swi)
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

    def test_unavailable_or_legacy_metadata_has_no_python_workaround(self):
        fake, mode, count = self.fake()
        mode.write_text("{}")
        step = self.directory / "step"
        step.write_text(json.dumps({"command": [str(fake), str(mode), str(count)]}))
        for index, reply in enumerate(('(fs:failed fs:denied metadata $path 13 (fs:progress 0 known-position) "fixture")',
                                       '(fs:info regular 1 (fs:identity "1" "2"))')):
            log = self.directory / ("missing-metadata-%d" % index)
            guest(self.directory, NATIVE_HEADER + [
                "!(py-call (small_views_probe.setup 1 0))",
                "!(py-call (small_views_probe.apply %s))" % q(step),
                '!(match &self (= (fs:metadata $path $links) $body) (remove-atom &self (= (fs:metadata $path $links) $body)))',
                '(= (fs:metadata $path $links) %s)' % reply,
                '!(py-call (small_views_probe.record %s (json:stringify (JsonArray (sv:load "project-capabilities")))))' % q(log)],
                  **self.settings)
            row = json.loads(log.read_text())
            self.assertEqual(json.loads(row["text"]), ["unavailable"])
            self.assertEqual(row["forbidden"], 0)
            self.assertFalse(count.exists(), "missing identity must not launch an artifact probe")

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
        self.assertEqual(len(neighbors[0]), 12)
        self.assertEqual(len(count.read_text().splitlines()), 2)

    def test_empty_directory_paths_keep_each_sources_outcome(self):
        empty = self.directory / "empty"
        empty.mkdir()
        for source, variable in (("active-query-declaration", "METTACLAW_ACTIVE_QUERIES_PATH"),
                                 ("commitment-state", "METTACLAW_COMMITMENT_STATE_PATH"),
                                 ("task-phase", "METTACLAW_TASK_PHASE_PATH"),
                                 ("project-evidence", "METTACLAW_PROJECT_REGISTRY_PATH"),
                                 ("development-state", "METTACLAW_ITER_PROCESS_DIR")):
            with self.subTest(source=source):
                self.compare(source, [{"env": {variable: str(empty)}}])
        self.compare("project-capabilities", [{"command": [str(empty)]}])

    def test_unicode_table_matches_matching_python(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/generate_small_views_unicode.py"), "--check"],
                                cwd=ROOT, env=guest_env(self.directory), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capability_lean_projection_replay(self):
        model = Path(os.environ.get("CAPABILITY_PROJECTION_LEAN",
                                    Path.home() / "repos/MeTTapedia/lean/pettaclaw/CapabilityProjection.lean"))
        lean = os.environ.get("LEAN_BIN") or shutil.which("lean") or str(Path.home() / ".elan/bin/lean")
        self.assertTrue(model.is_file())
        self.assertTrue(Path(lean).exists())
        # Compile real model sources in an isolated file, without touching any
        # other project's build or cache. Compare the implemented subset only:
        # the runtime deliberately also displays explicitly witnessed plans.
        source = model.with_name("ContextProjection.lean").read_text() + "\n" + model.read_text().replace("import ContextProjection\n", "", 1)
        program = self.directory / "CapabilityReplay.lean"
        program.write_text(source + '''
def main : IO Unit := do
  let snapshot : CapabilityProjection.Snapshot :=
    { artifact := "cetta", revision := "fixture", inventory := [
      { name := "petta", status := .implemented, description := "Native 日 🙂" },
      { name := "future", status := .planned, description := "Not yet" },
      { name := "unicode", status := .implemented, description := "λ" }] }
  for row in (CapabilityProjection.project snapshot).implemented do
    IO.println s!"WITNESSED {row.name} {row.description}"
''')
        result = subprocess.run([lean, "+leanprover/lean4:v4.33.1", "--run", str(program)],
                                cwd=self.directory, env=guest_env(self.directory), capture_output=True,
                                text=True, timeout=180)
        save_evidence("capability-model", {"source": program.read_text(), "returncode": result.returncode,
                                           "stdout": result.stdout, "stderr": result.stderr})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = [line.removeprefix("WITNESSED ") for line in result.stdout.splitlines()
                    if line.startswith("WITNESSED ")]
        log = self.directory / "model-native"
        guest(self.directory, NATIVE_HEADER + [
            '!(py-call (small_views_probe.record %s (json:stringify (JsonArray '
            '(map-atom (filter-atom (sv:inventory ("  petta implemented Native 日 🙂" '
            '"  future planned Not yet" "  unicode implemented λ")) $row '
            '(== (index-atom $row 1) "implemented")) $row '
            '(str:join " " ((index-atom $row 0) (index-atom $row 2))))))))' % q(log)])
        self.assertEqual(json.loads(json.loads(log.read_text())["text"]), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)

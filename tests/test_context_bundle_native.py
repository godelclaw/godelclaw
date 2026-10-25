"""Production-engine parity at addition-context-sources; no live fixtures."""
import itertools
import json
import os
from pathlib import Path
import resource
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ("tests", "src", "channels")]
import context_sources
import context_bundle_probe as probe
import helper
from test_pins_context_native import guest_env, q

ENGINE = os.environ.get("CETTA_BIN", "cetta")
PETTA_ROOT = Path(os.environ.get("PETTA_ROOT", Path.home() / "repos/PeTTa"))
VM_LIMIT = 25165824 * 1024
NATIVE_SOURCES = {"active-query-declaration", "commitment-state", "task-phase",
                  "development-state", "project-capabilities", "project-evidence"}


def limit_engine():
    resource.setrlimit(resource.RLIMIT_AS, (VM_LIMIT, VM_LIMIT))


def save_evidence(prefix, value):
    evidence = os.environ.get("CONTEXT_BUNDLE_EVIDENCE")
    if evidence:
        directory = Path(evidence)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", prefix=prefix + "-", suffix=".json", dir=directory,
                                         encoding="utf-8", delete=False) as stream:
            json.dump(value, stream, ensure_ascii=True)


def guest(directory, lines, engine="cetta", swi=False, boot=False, runtime_root=None, **settings):
    root = Path(runtime_root) if runtime_root is not None else ROOT
    env = guest_env(directory, engine, OPENAI_API_KEY="test-only-unused", **settings)
    if root != ROOT:
        env["PYTHONPATH"] = os.pathsep.join(str(root / name) for name in ("tests", "src", "channels"))
    env.update(METTACLAW_SKIP_INITIALIZE="1", METTACLAW_REPOS_DIR=str(directory / "repos"),
               METTACLAW_CHROMA_DIR=str(directory / "chroma"))
    library = directory / "repos" / "petta_lib_chromadb"
    if library.is_dir():
        env["PYTHONPATH"] += os.pathsep + str(library)
    with tempfile.NamedTemporaryFile("w", dir=root, suffix=".metta", encoding="utf-8") as source:
        source.write("\n".join(lines) + "\n")
        source.flush()
        if swi:
            command = ["bash", str(PETTA_ROOT / "run.sh"), source.name, "default"]
        else:
            command = [ENGINE, "--lang", "petta", "--import-mode", "ancestor-walk"]
            if boot:
                command += [str(root / "cetta_bootstrap.metta"), str(directory / "channel.metta")]
            command += [source.name]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                text=True, timeout=900, preexec_fn=limit_engine)
    save_evidence("guest", {"command": command, "program": lines, "returncode": result.returncode,
                            "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode or "uncaught" in result.stderr or "AssertionError" in result.stdout:
        raise AssertionError((result.returncode, result.stdout[-5000:], result.stderr[-5000:]))
    return result


HEADER = ["!(import! &self (library lib_import))",
          "!(import! &self ./src/helper.py)",
          "!(import! &self ./tests/context_bundle_probe.py)"]
CALLER = ["!(import! &self ./src/memory)", "!(import! &self ./src/turn_additions)",
          "(= (tg:client-metta?) False)"]


class NativeContextBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # Project 03 sources use actual native loaders, not the old per-source
        # Python wire fixtures. Keep their stores empty and deterministic here;
        # test_small_views_native owns their changing/failure fixture matrix.
        registry = self.directory / "registry.json"
        registry.write_text('{"schema":1,"projects":[]}')
        patcher = mock.patch.dict(os.environ, {
            "METTACLAW_PROJECT_REGISTRY_PATH": str(registry),
            "METTACLAW_ITER_PROCESS_DIR": str(self.directory / "transformations"),
            "CETTA_BIN": str(self.directory / "absent-cetta")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_smoke_native_projector(self):
        out = self.directory / "out"
        guest(self.directory, HEADER + [
            "!(import! &self ./src/context_sources_cetta.metta)",
            '!(ctx:observe "s" 4 False ("value" "🙂é日本"))',
            '!(py-call (context_bundle_probe.note %s (ctx:render-one (ctx:observe "s" 4 False ("unavailable")))))' % q(out)])
        self.assertEqual(out.read_text(), "SOURCE[s] status=stale-known revision=" +
                         context_sources.Projector._revision("🙂é日本") + "\n🙂é…")

    def compare(self, scenarios, prelude=(), engine="cetta", swi=False, trigger="idle", oracle_scenarios=None, **settings):
        import strategy_host
        if engine == "cetta":
            scenarios = [{key: value for key, value in fixture.items() if key not in NATIVE_SOURCES}
                         for fixture in scenarios]
            if oracle_scenarios is not None:
                oracle_scenarios = [{key: value for key, value in fixture.items() if key not in NATIVE_SOURCES}
                                    for fixture in oracle_scenarios]
        lines = HEADER + CALLER + list(prelude)
        lines += ["!(py-call (context_bundle_probe.trigger %s))" % q(trigger)]
        log = self.directory / "bundles"
        log.unlink(missing_ok=True)
        projector = context_sources.Projector()
        expected = []
        environment = guest_env(self.directory, engine, **settings)
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(context_sources, "_PROJECTOR", projector), mock.patch.object(context_sources, "_loaders", probe.fixture_loaders), mock.patch.object(strategy_host, "_context_trigger", trigger):
            helper._change_marks.clear()
            for index, fixture in enumerate(scenarios):
                probe.fixture = fixture if oracle_scenarios is None else oracle_scenarios[index]
                probe.calls.clear()
                path = self.directory / ("fixture-%d.json" % index)
                path.write_text(json.dumps(fixture, ensure_ascii=True), encoding="utf-8")
                lines += ["!(py-call (context_bundle_probe.configure %s %d))" % (q(path), 1 if engine == "cetta" else 0),
                          "!(py-call (context_bundle_probe.record %s (addition-context-sources)))" % q(log)]
                expected.append(context_sources.bundle())
        guest(self.directory, lines, engine=engine, swi=swi, **settings)
        actual = [json.loads(line) for line in log.read_text().splitlines()]
        save_evidence("caller-parity", {"engine_route": engine, "swi": swi, "settings": settings,
                                       "trigger": trigger, "fixtures": scenarios,
                                       "oracle_fixtures": oracle_scenarios,
                                       "expected": expected, "actual": actual})
        self.assertEqual([entry["text"] for entry in actual], expected)
        for entry in actual:
            self.assertEqual(entry["forbidden"], 0)
            self.assertEqual(entry["calls"], {s.source_id: 1 for s in context_sources.SOURCE_SPECS
                                            if engine != "cetta" or s.source_id not in NATIVE_SOURCES})
        return actual

    def test_caller_all_statuses_and_multiturn(self):
        base = {s.source_id: "known " + s.source_id for s in context_sources.SOURCE_SPECS}
        self.compare([{key: {"raises": True} for key in base}, base,
                      {key: {"raises": True} for key in base},
                      {key: "recovered 🙂" for key in base},
                      {key: " \t\n\u001c\u3000" for key in base},
                      {key: {"raises": True} for key in base},
                      {key: None for key in base}, base])

    def test_surrogate_replacement_preserves_revisions_at_real_caller(self):
        # fx045 in the read-only review reproduction is "lone\ud800surrogate".
        # Render replacement is intentional; revisions are exactly the oracle's
        # already-existing UTF-8 errors="replace" hash, never a new hash policy.
        scenarios = []
        oracles = []
        values = ["lone\ud800surrogate", "low\udfffend", "pair\ud83d\ude42end",
                  "🙂\ud800e\u0301漢\udc00", "clean recovery 🙂", " \t"]
        for raw in values:
            # JSON combines a high/low surrogate pair into one astral scalar.
            # Build exact Python loader strings from code points instead.
            scenarios.append({s.source_id: {"codepoints": list(map(ord, raw))}
                              for s in context_sources.SOURCE_SPECS})
            normalized = raw.encode("utf-8", "replace").decode("utf-8")
            oracles.append({s.source_id: normalized for s in context_sources.SOURCE_SPECS})
        actual = self.compare(scenarios, oracle_scenarios=oracles)
        for raw, row in zip(values, actual):
            for spec in context_sources.SOURCE_SPECS:
                if spec.source_id in NATIVE_SOURCES:
                    continue
                revision = context_sources.Projector._revision(raw if raw.strip() else "")
                self.assertRegex(row["text"], r"SOURCE\[%s\] status=(?:known|absent) revision=%s" %
                                 (re.escape(spec.source_id), re.escape(revision)))
        self.assertIn("lone?surrogate", actual[0]["text"])

    def test_decode_failures_are_source_local_stale_and_recoverable(self):
        faults = [(2, '"\\ud800"'), (2, '"unterminated'), (2, '{"not":"text"}'),
                  (2, 'null'), (2, 7), (9,), (2,), ()]
        for source_id in ("work-state", "effect-receipts", "conversation"):
            for fault in faults:
                with self.subTest(source=source_id, wire=fault):
                    scenarios, oracles = [], []
                    for step, value in enumerate((None, "known A 🙂", None, None,
                                                  "recovered B 漢字", " \t", None, "recovered C")):
                        neighbors = {s.source_id: "neighbor %s step %d" % (s.source_id, step)
                                     for s in context_sources.SOURCE_SPECS}
                        fixture = dict(neighbors)
                        fixture[source_id] = {"wire": fault} if value is None else value
                        expected = dict(neighbors)
                        expected[source_id] = {"raises": True} if value is None else value
                        scenarios.append(fixture)
                        oracles.append(expected)
                    rows = self.compare(scenarios, oracle_scenarios=oracles, prelude=[
                        "!(py-call (context_bundle_probe.inject_wire_faults))"])
                    statuses = [re.search(r"SOURCE\[%s\] status=([^ ]+)" % re.escape(source_id),
                                          row["text"]).group(1) for row in rows]
                    self.assertEqual(statuses, ["unavailable", "known", "stale-known", "stale-known",
                                                "known", "absent", "unavailable", "known"])
                    for row in rows:
                        self.assertEqual(row["text"].count("SOURCE["), 13)
        print("Compared 192 real-caller decode-fault steps, with seven retained Python loaders called once per step")

    def test_all_python_whitespace_and_near_misses_at_real_caller(self):
        spaces = [chr(c) for c in range(0x110000) if chr(c).isspace()]
        self.assertEqual(len(spaces), 29)
        values = [""]
        for space in spaces:
            values.extend((space * 3, space + "known", "known" + space))
        for near in ("\u200b", "\ufeff", "\u180e", "\u2060", "\0"):
            self.assertFalse(near.isspace())
            values.extend((near, " " + near, near + " \t\n"))
        self.compare([{s.source_id: value for s in context_sources.SOURCE_SPECS} for value in values])
        print("Compared all 29 Python whitespace scalars and 5 near misses at the real caller")

    def test_every_source_clipping_unicode_and_conversion_at_caller(self):
        scenarios = []
        for delta in (-1, 0, 1, 10000):
            for alphabet in ("a", "🙂", "e\u0301", "漢字🙂e\u0301"):
                fixture = {}
                for spec in context_sources.SOURCE_SPECS:
                    length = spec.max_chars + delta
                    fixture[spec.source_id] = (alphabet * (length // len(alphabet) + 1))[:length]
                scenarios.append(fixture)
        very_long = {spec.source_id: "known" for spec in context_sources.SOURCE_SPECS}
        very_long["work-state"] = "🙂e\u0301漢" * 250000
        very_long["conversation"] = "漢🙂e\u0301" * 250000
        scenarios.append(very_long)
        # Every Python whitespace scalar, plus near misses and embedded NUL.
        for value in ("", " \t\n\r\v\f\u001c\u001d\u001e\u001f\x85\xa0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000",
                      "\u200b\ufeff", 'True False None (x) $v "quote" \\n \0 after nul',
                      42, False, 0, ["x", "y"], {"text": "data"}):
            scenarios.append({spec.source_id: value for spec in context_sources.SOURCE_SPECS})
        result = self.compare(scenarios)
        self.assertTrue(any("…\n" in row["text"] for row in result))
        self.assertTrue(any("\0 after nul" in row["text"] for row in result))
        print("Compared %d full-caller clipping/Unicode/conversion fixtures" % len(scenarios))

    def test_native_views_strategy_and_conversation_modes(self):
        # These sources use actual loaders, with native action/pins views.
        fixture = {s.source_id: "fixture " + s.source_id for s in context_sources.SOURCE_SPECS
                   if s.source_id not in {"pins", "recent-proposals", "relevant-files", "effect-receipts", "conversation"}}
        (self.directory / "history.metta").write_text('("t1" "\n" ((shell "cat src/a.py")) "\n")\n')
        (self.directory / "pins.txt").write_text("[x] actual pin 🙂\n")
        for strategy in ("0", "1"):
            for trigger in ("operator-event", "idle"):
                with self.subTest(strategy=strategy, trigger=trigger):
                    # Compare identical loader inputs in both processes.
                    # Real Telegram rendering parity has its own suites;
                    # this tests that the caller passes its native result.
                    self.compare([fixture, fixture], engine="cetta",
                                 METTACLAW_OBSERVED_STRATEGY=strategy,
                                 trigger=trigger,
                                 METTACLAW_EFFECT_RECEIPT_PATH=str(self.directory / "receipts"))

    def test_operator_event_selects_chat_window_instead_of_route(self):
        # Native Telegram route/chat output is supplied without touching Python's
        # conversation loader. Execute both callers with the same synthetic
        # client equations and compare directly with bundle(conversation=...).
        for mode in ("chat", "route"):
            log = self.directory / "native-conversation"
            log.unlink(missing_ok=True)
            path = self.directory / "fixture-native.json"
            path.write_text(json.dumps({s.source_id: "fixed" for s in context_sources.SOURCE_SPECS if s.source_id != "conversation"}))
            lines = HEADER + ["!(import! &self ./src/memory)", "!(import! &self ./src/turn_additions)",
                              "(= (tg:client-metta?) True)", '(= (tg:conversation-events) 64)',
                              '(= (tg:get reply-chat "") "fixture-chat")',
                              '(= (tg:conversation-window-for $events $chat) "native chat 🙂")',
                              '(= (tg:route-view) "native route 漢字")',
                              '!(py-call (context_bundle_probe.trigger %s))' % q("operator-event" if mode == "chat" else "idle"),
                              '!(py-call (context_bundle_probe.configure %s))' % q(path),
                              '!(py-call (context_bundle_probe.record %s (addition-context-sources)))' % q(log)]
            guest(self.directory, lines, METTACLAW_OBSERVED_STRATEGY="1")
            got = json.loads(log.read_text())["text"]
            with mock.patch.dict(os.environ, guest_env(self.directory), clear=True), mock.patch.object(context_sources, "_loaders", probe.fixture_loaders), mock.patch.object(context_sources, "_PROJECTOR", context_sources.Projector()):
                probe.fixture = json.loads(path.read_text())
                want = context_sources.bundle(conversation="native " + ("chat 🙂" if mode == "chat" else "route 漢字"))
            self.assertEqual(got, want)

    def test_bad_and_missing_action_views_cost_only_their_sources(self):
        fixture = {s.source_id: "fixed" for s in context_sources.SOURCE_SPECS
                   if s.source_id not in {"pins", "recent-proposals", "relevant-files"}}
        for broken in (None, '(Error action-window:context-views simulated)', '(garbage)',
                       '(("recent-proposals" "") ("relevant-files" 7))'):
            prelude = ["!(match &self (= (action-window:context-views) $body) (remove-atom &self (= (action-window:context-views) $body)))"]
            if broken is not None:
                prelude += ["(= (action-window:context-views) %s)" % broken]
            self.compare([fixture, fixture], prelude=prelude, METTACLAW_OBSERVED_STRATEGY="0")

    def test_whole_bundle_failure_and_missing_helper_do_not_fallback(self):
        for prelude in (["!(py-call (context_bundle_probe.fail_prepare))"],
                        ["!(match &self (= (ctx:layout $items) $body) (remove-atom &self (= (ctx:layout $items) $body)))"]):
            out = self.directory / "failure"
            out.unlink(missing_ok=True)
            path = self.directory / "fixture-failure.json"
            path.write_text("{}")
            guest(self.directory, HEADER + CALLER + [
                "!(py-call (context_bundle_probe.configure %s))" % q(path)] + prelude + [
                "!(py-call (context_bundle_probe.record %s (addition-context-sources)))" % q(out)])
            result = json.loads(out.read_text())
            self.assertEqual(result["text"], "SOURCE[context-bundle] status=unavailable")
            # fail_prepare is deliberately counted; missing layout never calls it.
            self.assertEqual(result["forbidden"], 1 if "fail_prepare" in prelude[0] else 0)

    def test_fresh_process_has_no_known_projection(self):
        base = {s.source_id: "known" for s in context_sources.SOURCE_SPECS}
        self.compare([base])
        first = self.compare([{key: {"raises": True} for key in base}])[0]["text"]
        self.assertNotIn("stale-known", first)
        self.assertEqual(first.count("status=unavailable"), 7)

    def test_text_conversion_failure_aborts_bundle_without_fallback(self):
        for failed_index in (0, 6, 12):
            values = {s.source_id: "known" for s in context_sources.SOURCE_SPECS}
            values[context_sources.SOURCE_SPECS[failed_index].source_id] = {"broken_text": True}
            path = self.directory / "broken-text.json"
            path.write_text(json.dumps(values))
            out = self.directory / "broken-text-output"
            out.unlink(missing_ok=True)
            guest(self.directory, HEADER + CALLER + [
                "!(py-call (context_bundle_probe.configure %s))" % q(path),
                "!(py-call (context_bundle_probe.record %s (addition-context-sources)))" % q(out)])
            actual = json.loads(out.read_text())
            with mock.patch.dict(os.environ, guest_env(self.directory), clear=True), mock.patch.object(context_sources, "_loaders", probe.fixture_loaders), mock.patch.object(context_sources, "_PROJECTOR", context_sources.Projector()):
                probe.fixture = values
                probe.calls.clear()
                expected = context_sources.bundle()
                self.assertEqual(actual["calls"], {key: value for key, value in probe.calls.items()
                                                  if key not in NATIVE_SOURCES})
            self.assertEqual(actual["text"], expected)
            self.assertEqual(expected, "SOURCE[context-bundle] status=unavailable")
            self.assertEqual(len(actual["calls"]), sum(
                spec.source_id not in NATIVE_SOURCES
                for spec in context_sources.SOURCE_SPECS[:failed_index + 1]))
            self.assertEqual(actual["forbidden"], 0)

    def test_swi_and_engine_selected_python_caller_unchanged(self):
        fixtures = [{s.source_id: value for s in context_sources.SOURCE_SPECS}
                    for value in ("known 🙂", {"raises": True}, None, "again")]
        self.compare(fixtures, engine="petta")
        self.compare(fixtures, engine="petta", swi=True)

    def test_production_boot_real_telegram_views_with_python_bundle_disabled(self):
        library = Path(os.environ.get("PETTA_LIB_CHROMADB", ROOT / "repos" / "petta_lib_chromadb"))
        self.assertTrue((library / "lib_chromadb.metta").is_file())
        shutil.copytree(library, self.directory / "repos" / "petta_lib_chromadb",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        rendered = subprocess.run([sys.executable, str(ROOT / "scripts/render_channel_config.py"), "telegram"],
                                  env=guest_env(self.directory), capture_output=True, text=True, check=True)
        (self.directory / "channel.metta").write_text(rendered.stdout)
        (self.directory / "pins.txt").write_text("[x] boot pin 🙂\n")
        (self.directory / "history.metta").write_text('("t1" "\n" ((shell "cat src/a.py")) "\n")\n')
        (self.directory / "updates.jsonl").write_text(json.dumps({
            "received_at": "2026-10-25T00:00:00+0000", "kind": "message", "allowed": True,
            "queued": True, "chat_id": "7", "chat_title": "Fixture", "from": "fixture",
            "message_id": 1, "update_id": 1, "text": "hello from fixture 🙂", "note": ""}) + "\n")
        fixture = {s.source_id: "boot " + s.source_id for s in context_sources.SOURCE_SPECS
                   if s.source_id not in NATIVE_SOURCES | {"pins", "recent-proposals", "relevant-files", "conversation"}}
        scenarios = [fixture, dict(fixture, **{"work-state": {"raises": True}}),
                     dict(fixture, **{"work-state": " \t"}), fixture]
        lines = HEADER + ['!(tg:set reply-chat "7")']
        log = self.directory / "boot-bundles"
        conversations = []
        for i, values in enumerate(scenarios):
            path = self.directory / ("boot-fixture-%d" % i)
            path.write_text(json.dumps(values))
            conv = self.directory / ("conversation-%d" % i)
            conversations.append(conv)
            lines += ["!(py-call (context_bundle_probe.configure %s))" % q(path),
                      "!(py-call (context_bundle_probe.trigger %s))" % q("operator-event" if i % 2 == 0 else "idle"),
                      "!(py-call (context_bundle_probe.note %s %s))" % (q(conv),
                       '(tg:conversation-window-for (tg:conversation-events) (tg:get reply-chat ""))' if i % 2 == 0 else '(tg:route-view)'),
                      "!(py-call (context_bundle_probe.record %s (addition-context-sources)))" % q(log)]
        guest(self.directory, lines, boot=True, METTACLAW_TELEGRAM_CLIENT="metta", METTACLAW_OBSERVED_STRATEGY="1")
        actual = [json.loads(row) for row in log.read_text().splitlines()]
        expected = []
        with mock.patch.dict(os.environ, guest_env(self.directory, METTACLAW_OBSERVED_STRATEGY="1"), clear=True), mock.patch.object(context_sources, "_loaders", probe.fixture_loaders), mock.patch.object(context_sources, "_PROJECTOR", context_sources.Projector()):
            helper._change_marks.clear()
            for i, (values, row) in enumerate(zip(scenarios, actual)):
                probe.fixture = values
                conversation = conversations[i].read_text()
                expected.append(context_sources.bundle(conversation=conversation))
                self.assertEqual(row["text"], expected[-1])
                self.assertEqual(row["forbidden"], 0)
                if i % 2 == 0:
                    self.assertIn("hello from fixture 🙂", conversation)
                else:
                    self.assertNotIn("hello from fixture", conversation)
        save_evidence("boot-parity", {"fixtures": scenarios, "expected": expected, "actual": actual,
                                     "conversations": [path.read_text() for path in conversations]})

    def test_exhaustive_lean_model_and_python_replay(self):
        sequences = [seq for size in range(5) for seq in itertools.product(range(5), repeat=size)]
        self.assertEqual(len(sequences), 781)
        # Read the ACTUAL model source, compile its proofs in this temporary
        # program and execute admit. No MeTTapedia build/cache is touched.
        model = Path(os.environ.get("CONTEXT_PROJECTION_LEAN",
                                    Path.home() / "repos/MeTTapedia/lean/pettaclaw/ContextProjection.lean"))
        lean_bin = os.environ.get("LEAN_BIN") or shutil.which("lean") or str(Path.home() / ".elan/bin/lean")
        if not model.is_file() or not Path(lean_bin).exists():
            self.skipTest("set CONTEXT_PROJECTION_LEAN and LEAN_BIN to replay the Lean model")
        lean_source = model.read_text()
        lean = self.directory / "Replay.lean"
        lean.write_text(lean_source + '\n\ndef replaySequences : List (List Nat) := ' +
                        repr([list(seq) for seq in sequences]) + '''
def main : IO Unit := do
  for (sequence, i) in replaySequences.zipIdx do
    let mut previous : Option String := none
    for (outcome, j) in sequence.zipIdx do
      let observation : ContextProjection.Observation String :=
        if outcome == 0 then .known "A"
        else if outcome == 1 then .known "B"
        else if outcome == 4 then .unavailable else .absent
      previous := ContextProjection.admit previous observation
      IO.println s!"MODEL {i} {j} {previous.getD "-"}"
''', encoding="utf-8")
        command = [lean_bin, "+leanprover/lean4:v4.33.1", "--run", str(lean)]
        result = subprocess.run(command, cwd=self.directory, env=guest_env(self.directory), capture_output=True, text=True, timeout=180)
        evidence = os.environ.get("CONTEXT_BUNDLE_EVIDENCE")
        if evidence:
            import hashlib
            (Path(evidence) / "lean-replay.lean").write_text(lean.read_text(), encoding="utf-8")
            (Path(evidence) / "lean-replay.json").write_text(json.dumps({"command": command, "model_sha256": hashlib.sha256(lean_source.encode()).hexdigest(), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        admitted = {(int(i), int(j)): value for i, j, value in re.findall(r"^MODEL (\d+) (\d+) ([AB-])$", result.stdout, re.M)}
        self.assertEqual(len(admitted), 2930)
        log = self.directory / "replay"
        lines = HEADER + ["!(import! &self ./src/context_sources_cetta.metta)"]
        expected = []
        for i, sequence in enumerate(sequences):
            lines.append("!(change-state! &ctx-known ())")
            projector = context_sources.Projector()
            spec = context_sources.SourceSpec("replay", 20)
            for j, outcome in enumerate(sequence):
                raw = ("A", "B", " \t\u3000", None, None)[outcome]
                def loader():
                    if outcome == 4:
                        raise RuntimeError("fixture")
                    return raw
                observed = projector.observe(spec, loader)
                retained = admitted[i, j]
                status = ("known" if outcome < 2 else "absent" if outcome < 4
                          else "unavailable" if retained == "-" else "stale-known")
                text = "" if retained == "-" else retained
                revision = "" if status == "unavailable" else projector._revision(text)
                self.assertEqual((observed.status, observed.text, observed.revision), (status, text, revision), (sequence, j, "Lean/Python disagreement"))
                expected.append(context_sources.Projector.render_observations([observed]))
                native_outcome = '("unavailable")' if outcome == 4 else '("missing")' if raw is None else '("value" %s)' % q(raw)
                lines.append('!(py-call (context_bundle_probe.record %s (ctx:render-one (ctx:observe "replay" 20 False %s))))' % (q(log), native_outcome))
        guest(self.directory, lines)
        actual = [json.loads(row)["text"] for row in log.read_text().splitlines()]
        save_evidence("projector-replay", {"sequences": sequences, "expected": expected, "actual": actual})
        self.assertEqual(actual, expected)
        print("Executable Lean admit + Python labels + native: 781 sequences, 2930 step comparisons; model proofs compiled in temporary source")


if __name__ == "__main__":
    unittest.main(verbosity=2)

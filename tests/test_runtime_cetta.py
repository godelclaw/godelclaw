"""The native runtime projection reads and renders exactly what
src/runtime_context.py does, over the same files at the same instant.

Only the process facts differ by construction: each side describes its own
process, so they are checked for shape and plausibility instead."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000.5


def receipt(disposition, reason=None, command=True):
    record = {"turn": 1, "disposition": disposition}
    if command:
        record["command"] = ["shell", "true"]
    if reason is not None:
        record["reason"] = reason
    return json.dumps(record)


def scenario(**overrides):
    base = {
        "health": {"schema": 2, "obligation_since": NOW - 40, "in_flight_since": NOW - 12.7, "retry_at": 0,
                   "soft_wait_elapsed": True, "response_bytes": 1234, "transport_quiet_ms": 5500},
        "completion": {"observed_at": NOW - 30, "finish_reason": "length", "prompt_layout": {"stable_chars": 4567},
                       "usage": {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 750}}},
        "control": {"observed_at": NOW - 5, "channel_ready": True,
                    "operator_inbox": {"observedAt": NOW - 3, "pending": 2,
                                       "newestMessageAt": NOW - 60, "oldestMessageAt": NOW - 600}},
        "receipts": [receipt("returned"), receipt("withheld", "stale"), receipt("deferred", '{"reason": "paused"}'),
                     receipt("exception"), receipt("no_result"), receipt("mystery"), receipt("failed", {"reason": "cooldown"}),
                     receipt("returned", command=False), receipt("withheld", "not-a-reason"), receipt("uncertain", "{bad")]
                    + [receipt("returned") for _ in range(12)],
        "backend": {"mode": "native", "name": "memories-fixture", "socket": "LISTENING"},
    }
    base.update(overrides)
    return base


SCENARIOS = {
    "waiting": scenario(),
    "backoff": scenario(health={"schema": 2, "obligation_since": NOW - 40, "in_flight_since": 0, "retry_at": NOW + 7.2,
                                "last_outcome": "failed", "last_failed_at": NOW - 3, "last_failure_type": "ProviderTimeoutError"},
                        completion={"observed_at": NOW - 10, "usage": {"input_tokens": 100, "cache_read_input_tokens": 900,
                                                                       "cache_creation_input_tokens": 50}}),
    "failed": scenario(health={"schema": 2.0, "obligation_since": 0, "in_flight_since": 0, "retry_at": NOW - 1,
                               "last_outcome": "failed", "last_failed_at": NOW - 100, "last_failure_type": "Quota exceeded"},
                       completion={"observed_at": NOW - 5000, "usage": {"prompt_tokens": 5}}),
    "failed-known-reason": scenario(health={"schema": 2, "obligation_since": 0, "in_flight_since": 0, "retry_at": 0,
                                            "last_outcome": "failed", "last_failure_type": "paused"}),
    "failed-no-label": scenario(health={"schema": 2, "obligation_since": 0, "in_flight_since": 0, "retry_at": 0,
                                        "last_outcome": "failed", "last_failure_type": None}),
    "returned": scenario(health={"schema": 2, "obligation_since": NOW - 1, "in_flight_since": 0, "retry_at": 0,
                                 "last_outcome": "completed", "last_completed_at": NOW - 9},
                         completion={"observed_at": NOW - 2, "usage": "not-an-object", "prompt_layout": None}),
    "idle-zero-cache": scenario(health={"schema": 2, "obligation_since": 0, "in_flight_since": 0, "retry_at": 0},
                                completion={"observed_at": NOW - 2, "usage": {"prompt_tokens": 0, "prompt_cache_hit_tokens": 0}}),
    "bad-schema": scenario(health={"schema": 1, "obligation_since": 0, "in_flight_since": 0, "retry_at": 0}),
    "future": scenario(health={"schema": 2, "obligation_since": NOW + 100, "in_flight_since": 0, "retry_at": 0}),
    "stale-channel": scenario(control={"observed_at": NOW - 31, "channel_ready": True, "operator_inbox": {}}),
    "empty-inbox": scenario(control={"observed_at": NOW - 1, "channel_ready": True,
                                     "operator_inbox": {"observedAt": NOW - 1, "pending": 0, "newestMessageAt": NOW - 9}}),
    "malformed-receipts": scenario(receipts=[receipt("returned"), "", receipt("withheld", "stale")]),
    "unreachable-memory": scenario(backend={"mode": "native", "name": None, "socket": "/nonexistent/socket"}),
    "invalid-backend": scenario(backend="{not json"),
    "nothing": {"health": None, "completion": None, "control": None, "receipts": None, "backend": None},
    "chroma": scenario(backend={"mode": "chroma"}, chroma={"threshold": 25, "meta": 40, "vector": 35, "documents": 7}),
    "chroma-nested-threshold": scenario(backend={"mode": "chroma"}, chroma={"threshold": None, "nested": 9, "meta": 5,
                                                                           "vector": 5, "documents": 2}),
    "chroma-index-ahead": scenario(backend={"mode": "chroma"}, chroma={"threshold": 25, "meta": 3, "vector": 4, "documents": 1}),
    "chroma-no-threshold": scenario(backend={"mode": "chroma"}, chroma={"threshold": None, "meta": 3, "vector": 3, "documents": 1}),
    "chroma-missing": scenario(backend={"mode": "chroma"}),
}


def chroma_database(directory, spec):
    """A minimal Chroma store with the tables the memory facts read."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "chroma.sqlite3"
    config = {"hnsw": {"sync_threshold": spec["threshold"]}} if spec["threshold"] is not None else {"hnsw": {"space": "cosine"}}
    if spec.get("nested") is not None:
        config = {"wrapper": [{"x": 1}, {"deeper": {"sync_threshold": spec["nested"]}}]}
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE collections(id TEXT, name TEXT, config_json_str TEXT);"
                         "CREATE TABLE segments(id TEXT, scope TEXT, collection TEXT);"
                         "CREATE TABLE max_seq_id(segment_id TEXT, seq_id INTEGER);"
                         "CREATE TABLE embeddings(id INTEGER, segment_id TEXT);")
        db.execute("INSERT INTO collections VALUES ('c1', 'memories', ?)", (json.dumps(config),))
        db.executemany("INSERT INTO segments VALUES (?, ?, 'c1')", [("m1", "METADATA"), ("v1", "VECTOR")])
        db.executemany("INSERT INTO max_seq_id VALUES (?, ?)", [("m1", spec["meta"]), ("v1", spec["vector"])])
        db.executemany("INSERT INTO embeddings VALUES (?, 'm1')", [(i,) for i in range(spec["documents"])])
    return path


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to a CeTTa build with Python")
class NativeRuntimeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket_path = str(self.dir / "memory.sock")
        self.listener.bind(self.socket_path)
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.accepting = True
        self.acceptor = threading.Thread(target=self.accept, daemon=True)
        self.acceptor.start()

    def accept(self):
        # A reachable service accepts connections; without this the backlog fills.
        while self.accepting:
            try:
                connection, _ = self.listener.accept()
                connection.close()
            except OSError:
                pass

    def tearDown(self):
        self.accepting = False
        self.acceptor.join()
        self.listener.close()
        self.tmp.cleanup()

    def environment(self, files):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("METTACLAW_", "OMEGACLAW_"))}
        env.update(PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "channels")]),
                   SYNTHETIC_MODEL="fixture-model", METTACLAW_MODEL_STATE_PATH=str(self.dir / "model-state"),
                   METTACLAW_REASONING_EFFORT="high", METTACLAW_CHROMA_DIR=str(self.dir / "chroma"))
        paths = {"health": "METTACLAW_COGNITIVE_HEALTH_PATH", "completion": "METTACLAW_LLM_COMPLETION_PATH",
                 "receipts": "METTACLAW_EFFECT_RECEIPT_PATH", "backend": "METTACLAW_MEMORY_BACKEND_CONFIG"}
        for name, value in files.items():
            if value is None:
                continue
            if name == "chroma":
                chroma_database(self.dir / "chroma", value)
                continue
            if name == "control":
                target = self.dir / "modes" / "control-status.json"
                target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps(value))
                env["METTACLAW_MODE_STATE_ROOT"] = str(target.parent)
                continue
            target = self.dir / (name + ".data")
            if name == "receipts":
                target.write_text("\n".join(value) + "\n")
            elif name == "backend" and isinstance(value, dict):
                target.write_text(json.dumps({**value, "socket": self.socket_path} if value.get("socket") == "LISTENING" else value))
            else:
                target.write_text(value if isinstance(value, str) else json.dumps(value))
            env[paths[name]] = str(target)
        return env

    def python_facts(self, env, now=NOW):
        code = ("import json, sys, runtime_context; "
                "print(json.dumps(runtime_context.read(float(sys.argv[1]) if sys.argv[1] != 'now' else None)))")
        result = subprocess.run([sys.executable, "-c", code, repr(now) if now else "now"], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout)

    def native(self, env, render_file=None):
        facts_out, text_out = self.dir / "native-facts.json", self.dir / "native-text.txt"
        for path in (facts_out, text_out):
            path.unlink(missing_ok=True)
        facts = ("(json-parse (fs:read-text %s))" % json.dumps(str(render_file))) if render_file else "(runtime:read %r)" % NOW
        program = ROOT / (".runtime_probe_%d.metta" % os.getpid())
        program.write_text("\n".join([
            "!(import! &self (library lib_import))", "!(import! &self (library fs))", "!(import! &self (library json))",
            "!(import! &self ./src/runtime_cetta.metta)",
            "!(let* (($facts %s) ($text (runtime:render $facts)))" % facts,
            "   (let* (($a (fs:update-text %s \"replace\" (json-stringify $facts) \"\" \"\"))"
            " ($b (fs:update-text %s \"replace\" $text \"\" \"\"))) done))" % (json.dumps(str(facts_out)), json.dumps(str(text_out)))]) + "\n")
        try:
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk",
                                     str(program)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        finally:
            program.unlink()
        self.assertTrue(text_out.is_file(), result.stdout[-3000:] + result.stderr[-2000:])
        return json.loads(facts_out.read_text()), text_out.read_text()

    def python_render(self, env, facts):
        code = ("import json, sys, runtime_context; "
                "sys.stdout.write(runtime_context.render(json.loads(sys.stdin.read())))")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, input=json.dumps(facts),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result.stdout

    def test_every_scenario_reads_and_renders_as_python_does(self):
        for name, files in SCENARIOS.items():
            with self.subTest(scenario=name):
                if (self.dir / "chroma").exists():
                    import shutil
                    shutil.rmtree(self.dir / "chroma")
                env = self.environment(files)
                expected = self.python_facts(env)
                started = time.time()
                facts, text = self.native(env)
                process = facts.pop("process", None)
                expected.pop("process", None)
                self.assertEqual(facts, expected)
                self.assertEqual(sorted(process), ["startedAt", "uptimeSeconds"])
                self.assertLessEqual(abs(process["startedAt"] - started), 120)
                self.assertEqual(text, self.python_render(env, {**facts, "process": process}))

    def test_the_cetta_context_path_appends_one_native_packet_after_the_evidence(self):
        import hashlib
        sys.path.insert(0, str(ROOT / "tests"))
        import test_command_dispatch_engine as dispatch
        import test_task_context as reference
        fixture = reference.TaskContextTests(); fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        fixture.call([["read-file", "source"]], ["inspected evidence"])
        harness = dispatch.CommandDispatchEngineTest()
        env = {**harness._cetta_env(), **self.environment(SCENARIOS["waiting"])}
        env.update(METTACLAW_STRATEGY_PATH=str(fixture.path), METTACLAW_ENGINE_STATE_PATH=str(fixture.path.parent / "engine"),
                   METTACLAW_ACTIVE_ENGINE="cetta", METTACLAW_ENGINE="cetta", METTACLAW_OBSERVED_STRATEGY="1",
                   METTACLAW_LLM_TRANSPORT="native", METTACLAW_LLM_LEDGER_PATH=str(self.dir / "ledger.sqlite3"))
        output = self.dir / "prepared.txt"
        program = ROOT / (".prepare_probe_%d.metta" % os.getpid())
        program.write_text('!(import! &self (library lib_import))\n!(import! &self (library fs))\n'
                           '!(import! &self ./src/state_io.metta)\n'
                           '!(fs:update-text %s "replace" (state:prepare-context "PROMPT-HEAD" 32768) "" "")\n'
                           % json.dumps(str(output)))
        try:
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk",
                                     str(program)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        finally:
            program.unlink()
        self.assertTrue(output.is_file(), result.stdout[-3000:] + result.stderr[-2000:])
        text = output.read_text()
        evidence = subprocess.run([sys.executable, "-c", "import strategy_host, sys; sys.stdout.write(strategy_host.lifetime_context())"],
                                  cwd=ROOT, env=env, capture_output=True, text=True, timeout=60).stdout
        self.assertTrue(text.startswith("PROMPT-HEAD" + evidence + "\nSOURCE[runtime-state] revision=sha256:"), text[:400])
        self.assertEqual(text.count("RUNTIME_STATE "), 1)
        revision = text.split("revision=sha256:", 1)[1][:64]
        body = next(line for line in text.splitlines() if line.startswith("RUNTIME_STATE "))[len("RUNTIME_STATE "):]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), revision)
        # This path samples the wall clock, so compare with Python reading now too.
        self.assertEqual(sorted(json.loads(body)), sorted(self.python_facts(env, now=None)))
        self.assertIn("TASK_CONTEXT", text.split(body, 1)[1])

    def test_an_oversized_projection_falls_back_like_python(self):
        env = self.environment({})
        oversized = {"schema": 1, "call": {"phase": "x" * 3000}, "budgetStatus": "no-volume-caps"}
        source = self.dir / "oversized.json"
        source.write_text(json.dumps(oversized))
        _, text = self.native(env, render_file=source)
        self.assertEqual(text, self.python_render(env, oversized))
        self.assertIn('"unavailable":["projection-overflow"]', text)


if __name__ == "__main__":
    unittest.main()

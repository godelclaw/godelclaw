"""Real native provider workers, reversed completion and reviewed merges."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "channels")]
import agent_checkpoint
import topic_jobs as jobs
import strategy_host as host
import llm_policy
import observed_strategy as strategy


class Provider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        question = body["messages"][-1]["content"]
        self.server.requests.append(question)
        self.server.contexts.append(body["messages"][0]["content"])
        time.sleep(.8 if "slow" in question else .1)
        reply = {"choices": [{"message": {"role": "assistant", "content": "evidence for " + question}, "finish_reason": "stop"}],
                 "usage": {"completion_tokens": 10}}
        self.server.completed.append(question)
        self.send_response(200); self.end_headers()
        try:
            self.wfile.write(json.dumps(reply).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to a native HTTP/Python build")
class NativeTopicJobsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        modules = self.root / "modules"; modules.mkdir()
        (modules / "openai.py").write_text('class OpenAI:\n    def __init__(self,*args,**kwargs):\n        raise AssertionError("Python SDK called")\n')
        latch = self.root / "lifecycle.json"; latch.write_text('{"schema":1,"state":"running"}')
        self.peer = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.peer.requests, self.peer.completed, self.peer.contexts = [], [], []
        threading.Thread(target=self.peer.serve_forever, daemon=True).start()
        self.addCleanup(self.peer.server_close); self.addCleanup(self.peer.shutdown)
        env = {"PYTHONHOME": sys.prefix, "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
               "PYTHONPATH": os.pathsep.join([str(modules), str(ROOT / "src"), str(ROOT / "channels")]),
               "METTACLAW_STRATEGY_PATH": str(self.root / "strategy.db"),
               "METTACLAW_OBSERVED_STRATEGY": "1", "METTACLAW_INPUT_POLICY": "cooperative",
               "METTACLAW_ACTIVE_ENGINE": "cetta", "METTACLAW_BACKGROUND_MODELS": "2",
               "METTACLAW_ACTIVE_LOOP_MODE": "godelclaw", "METTACLAW_LLM_POLICY": "managed",
               "METTACLAW_LLM_POLICY_PATH": "", "METTACLAW_LLM_LEDGER_PATH": "",
               "METTACLAW_LIFECYCLE_PATH": str(latch), "METTACLAW_LOOP_MODE_PATH": str(self.root / "mode"),
               "METTACLAW_MODEL_STATE_PATH": str(self.root / "model"), "SYNTHETIC_MODEL": "fixture-model",
               "SYNTHETIC_BASE_URL": "http://127.0.0.1:%d/v1" % self.peer.server_port,
               "SYNTHETIC_API_KEY": "fixture-secret", "SYNTHETIC_TIMEOUT": "10",
               "METTACLAW_LLM_STREAM": "0", "METTACLAW_LLM_SOFT_SECONDS": "10",
               "METTACLAW_LLM_HARD_SECONDS": "20",
               "METTACLAW_ENGINE_STATE_PATH": str(self.root / "engine"),
               "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.root / "health.json"),
               "METTACLAW_WAKE_REQUEST_PATH": str(self.root / "wake")}
        patch = mock.patch.dict(os.environ, env); patch.start(); self.addCleanup(patch.stop)
        state = mock.patch.multiple(host, _focus="owned-request", _ticket="foreground-action", _sample={})
        state.start(); self.addCleanup(state.stop)
        recover = mock.patch.multiple(jobs, _recovered=False, _workers={})
        recover.start(); self.addCleanup(recover.stop)
        store = agent_checkpoint.ledger()
        store.accept("owned-request", "fixture work")
        store.policy(allowance=5)
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for process in list(jobs._workers.values()):
            if process.poll() is None:
                process.kill()
            process.wait()
        deadline = time.monotonic() + 10
        while jobs._workers and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(jobs._workers, "worker supervision did not settle before fixture cleanup")

    def start(self, topic, question, dependencies="[]"):
        result = jobs.start(topic, question, dependencies)
        self.assertIn(" started for topic ", result, result)
        return result.split()[2]

    def wait_result(self, job):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            value = json.loads(jobs.results(job))
            if value["state"] not in ("queued", "running"):
                return value
            time.sleep(.05)
        self.fail("worker failed to settle")

    def observe(self, job):
        store = agent_checkpoint.ledger()
        evidence = jobs.results(job)
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            db.execute("INSERT OR REPLACE INTO command_attempts VALUES (?,?,?,?)", ("reader:" + job, 0,
                strategy.encoded(["model-result", job]), strategy.encoded({"disposition": "returned", "result": evidence})))
        ticket = "reader:" + job
        jobs.observe_receipts({ticket: [{"ticket": ticket, "truncated": False}]})

    def test_concurrent_workers_finish_in_reverse_order_without_blocking_foreground(self):
        begin = time.monotonic()
        first = self.start("research", "slow alpha")
        second = self.start("reading", "fast beta")
        self.assertLess(time.monotonic() - begin, .6)
        a, b = self.wait_result(first), self.wait_result(second)
        self.assertEqual((a["topic"], b["topic"]), ("research", "reading"))
        self.assertEqual((a["state"], b["state"]), ("returned", "returned"), (a, b))
        self.assertIn("beta", self.peer.completed[0])
        self.assertEqual(len(self.peer.requests), 2)
        self.assertTrue(any(first in context for context in self.peer.contexts))
        self.assertIn("research:returned", jobs.activity())
        store = agent_checkpoint.ledger()
        with store.transaction() as db:
            self.assertEqual(store.get(db, "allowance"), 3)
        with llm_policy.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM requests WHERE status='completed'").fetchone()[0], 2)
        self.assertIn("observed", jobs.adopt(first))
        self.observe(first)
        self.assertIn("adopted model evidence once", jobs.adopt(first))
        self.assertIn("previously adopted", jobs.adopt(first))

    def test_changed_dependency_refuses_merge_and_preserves_the_result(self):
        jobs.select("knowledge"); jobs.note("original evidence")
        job = self.start("analysis", "fast question", '["knowledge"]')
        value = self.wait_result(job)
        self.assertEqual(value["state"], "returned", value)
        self.observe(job)
        jobs.note("corrected evidence")
        self.assertIn("dependency changed", jobs.adopt(job))
        self.assertIn("evidence for fast question", jobs.results(job))

    def test_stop_prevents_background_provider_admission(self):
        Path(os.environ["METTACLAW_LIFECYCLE_PATH"]).write_text('{"schema":1,"state":"stopped"}')
        job = self.start("analysis", "fast stopped")
        value = self.wait_result(job)
        self.assertEqual(value["state"], "failed")
        self.assertEqual(self.peer.requests, [])
        store = agent_checkpoint.ledger()
        with store.transaction() as db:
            self.assertEqual(store.get(db, "allowance"), 5)

    def test_native_foreground_observes_receipt_before_adoption(self):
        env = dict(os.environ, METTACLAW_ENGINE="cetta", METTACLAW_SKIP_INITIALIZE="1",
                   METTACLAW_TOPIC_TEST_ORIGIN=os.environ["SYNTHETIC_BASE_URL"],
                   METTACLAW_LLM_TRANSPORT="python", METTACLAW_FAIR_GOALS="0",
                   METTACLAW_COMMAND_BATCH_LIMIT="1", METTACLAW_STRATEGY_PATH=str(self.root / "foreground.db"),
                   PETTA_PY_ENV=sys.prefix, XDG_STATE_HOME=str(self.root),
                   PYTHONPATH=os.pathsep.join([str(ROOT / "tests"), os.environ["PYTHONPATH"]]))
        result = subprocess.run(["./run.sh", "tests/cooperative_topics_probe.metta"], cwd=ROOT,
                                env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-5000:])
        self.assertIn("\nCOOPERATIVE_TOPICS_OK\n", result.stdout)

    def test_only_presented_excerpt_is_adopted_and_full_result_is_retained(self):
        job = self.start("research", "fast long finding")
        self.assertEqual(self.wait_result(job)["state"], "returned")
        store = agent_checkpoint.ledger()
        full = "quoted \"finding\" " * 2000 + " UNSEEN_END"
        with store.transaction() as db:
            db.execute("UPDATE model_jobs SET result=? WHERE id=?", (full, job))
        evidence = json.loads(jobs.results(job))
        self.assertTrue(evidence["truncated"])
        self.assertLessEqual(len(jobs.results(job)), 2400)
        evidence_text = jobs.results(job)
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS command_attempts (ticket TEXT,position INTEGER,command TEXT,result TEXT,PRIMARY KEY(ticket,position))")
            db.execute("INSERT INTO command_attempts VALUES (?,?,?,?)", ("truncated-view", 0,
                strategy.encoded(["model-result", job]), strategy.encoded({"disposition": "returned", "result": evidence_text})))
        jobs.observe_receipts({"truncated-view": [{"ticket": "truncated-view", "truncated": True}]})
        self.assertIn("observed", jobs.adopt(job))
        self.observe(job)
        self.assertIn("adopted model evidence once", jobs.adopt(job))
        with store.transaction() as db:
            notes = db.execute("SELECT notes FROM topic_contexts WHERE name='research'").fetchone()[0]
            self.assertEqual(db.execute("SELECT result FROM model_jobs WHERE id=?", (job,)).fetchone()[0], full)
        self.assertNotIn("UNSEEN_END", notes)
        self.assertIn("excerpt; complete result retained", notes)

    def test_background_goal_cost_has_a_correlated_auditable_charge(self):
        import fair_goals as fair
        store = agent_checkpoint.ledger()
        fair.synchronize(store, [dict(name="research",area="Research",note="Check evidence",blocked="none",
                                     share=1,interval=None,baseUrgency=0,checker="operator")], time.time())
        view = fair.snapshot(store,time.time()); goal = view["selected"]
        focus = fair.accept_selected(store,view)
        lease = {k:goal[k] for k in ("id","round","service")}; lease["inventory_digest"] = view["inventory_digest"]
        router = dict(advertised=["background-model"],executable=["background-model"],permitted=["background-model"])
        sample = store.capture(focus,"fixture",router,time.time(),host.guard(include_topic=False),goal_lease=lease)
        ticket = store.decide(sample["serial"],dict(kind="command",tool="background-model",request=focus,
                                                  owner=sample["owner"],command={}),time.time(),sample["guard"])
        with mock.patch.multiple(host,_focus=focus,_sample=sample,_ticket=ticket):
            job = self.start("research","fast goal evidence")
            self.assertEqual(self.wait_result(job)["state"],"returned")
        spec = importlib.util.spec_from_file_location("goal_auditor",ROOT / "scripts/audit_fair_goals.py")
        audit = importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
        data = audit.read(store.path)
        source, result = audit.export(data)
        self.assertEqual((result["charges"],result["auxiliary_charges"]),(1,1))
        (self.root / "auxiliary.lean").write_text(source)
        output = os.environ.get("METTACLAW_AUXILIARY_REPLAY_OUTPUT")
        if output:
            Path(output).write_text(source)
        broken = dict(data,events=[r for r in data["events"] if json.loads(r["event"])["kind"] != "model-goal-charge"])
        with self.assertRaises(ValueError): audit.export(broken)

    def test_capacity_and_restart_preserve_uncertainty_without_replaying_posts(self):
        with mock.patch.dict(os.environ, {"METTACLAW_BACKGROUND_MODELS": "1"}):
            job = self.start("research", "slow unfinished")
            self.assertIn("capacity", jobs.start("other", "next"))
        process = jobs._workers[job]
        until = time.monotonic() + 5
        while not self.peer.requests and time.monotonic() < until:
            time.sleep(.02)
        self.assertEqual(len(self.peer.requests), 1)
        process.kill(); process.wait()
        value = self.wait_result(job)
        self.assertEqual(value["state"], "uncertain")
        jobs._recovered = False
        with mock.patch.object(jobs, "_boot", "new-process"):
            jobs.recover()
        self.assertIn("uncertain", jobs.results(job))
        self.assertEqual(len(self.peer.requests), 1)


if __name__ == "__main__":
    unittest.main()

"""Coding mode's work loop against a scripted provider: no network, no keys."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Script(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        reply = self.server.replies.pop(0) if self.server.replies else self.server.final
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)


def call(identity, name, arguments):
    return {"id": identity, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply(content, calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list(calls)
    return {"id": "r", "choices": [{"message": message,
            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class CodingSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Script)
        self.server.requests, self.server.replies = [], []
        self.server.final = reply("Nothing more to do.")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        latch = self.directory / "lifecycle.json"
        latch.write_text('{"schema":1,"state":"running"}')
        modules = self.directory / "fixture_modules"
        modules.mkdir()
        (modules / "openai.py").write_text("class OpenAI:\n    pass\n")
        self.env = {**os.environ, "PYTHONHOME": sys.prefix, "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            # The agent's own path (run.sh): sessions inherit it, and iter mode's modules are not on it.
            "PYTHONPATH": os.pathsep.join([str(modules), str(ROOT / "src"), str(ROOT / "channels")]),
            "METTACLAW_ACTIVE_ENGINE": "cetta", "METTACLAW_ENGINE": "cetta", "METTACLAW_LLM_TRANSPORT": "native",
            "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "engine"),
            "METTACLAW_LLM_POLICY": "managed", "METTACLAW_LLM_POLICY_PATH": "",
            "METTACLAW_LLM_LEDGER_PATH": str(self.directory / "ledger.db"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_LIFECYCLE_PATH": str(latch), "SYNTHETIC_MODEL": "fixture-model",
            "METTACLAW_MODEL_STATE_PATH": str(self.directory / "model"),
            "METTACLAW_CONTEXT_WINDOW_TOKENS": "100000",
            "SYNTHETIC_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "SYNTHETIC_API_KEY": "fixture-secret", "SYNTHETIC_EMPTY_BACKOFF": "0"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def session(self, task, workspace):
        session = self.directory / "session"
        session.mkdir()
        (session / "task.json").write_text(json.dumps({"task": task, "workdir": str(workspace), "effort": "default"}))
        return session

    def run_session(self, session):
        result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(ROOT / "coding_main.metta"), str(session)],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        return result

    def test_runs_tools_until_a_reply_calls_none(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Write hello.txt saying hello, then report.", workspace)
        patch = "*** Begin Patch\n*** Update File: hello.txt\n-hi\n+hello\n*** End Patch"
        self.server.replies = [
            reply("I will create the file and record a plan.",
                  [call("c1", "exec_command", {"cmd": "echo hi > hello.txt && cat hello.txt"}),
                   call("c2", "update_plan", {"plan": [{"step": "write", "status": "in_progress"}]})]),
            reply("Now the edit.", [call("c3", "apply_patch", {"patch": patch})]),
            reply("Done: hello.txt says hello; checked with cat.")]
        self.run_session(session)
        state = json.loads((session / "state.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertEqual((workspace / "hello.txt").read_text(), "hello\n")
        self.assertEqual((session / "report.txt").read_text(), "Done: hello.txt says hello; checked with cat.")
        self.assertEqual(json.loads((session / "plan.json").read_text())["plan"][0]["step"], "write")
        messages = [json.loads(line) for line in (session / "messages.jsonl").read_text().splitlines()]
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "assistant", "tool", "tool", "assistant", "tool", "assistant"])
        self.assertIn("Exit code: 0", messages[3]["content"])
        self.assertIn("hi", messages[3]["content"])
        self.assertIn("Success. Updated the following files:\nM hello.txt", messages[6]["content"])
        self.assertEqual(len(self.server.requests), 3)
        second = self.server.requests[1]
        self.assertTrue(any(m.get("tool_call_id") == "c1" for m in second["messages"]))
        self.assertTrue(any(t["function"]["name"] == "apply_patch" for t in second["tools"]))

    def test_long_output_is_cut_in_the_middle_and_steering_is_folded_in(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Print a lot.", workspace)
        (session / "inbox.jsonl").write_text(json.dumps("Also mention the line count.") + "\n")
        self.server.replies = [reply("Printing.", [call("c1", "exec_command", {"cmd": "seq 1 30000"})]),
                               reply("30000 lines.")]
        self.run_session(session)
        messages = [json.loads(line) for line in (session / "messages.jsonl").read_text().splitlines()]
        output = next(m["content"] for m in messages if m["role"] == "tool")
        self.assertIn("tokens truncated", output)
        self.assertTrue(output.rstrip().endswith("30000"))
        self.assertIn("\n1\n2\n3\n", output)
        self.assertIn("From the requester, mid-task: Also mention the line count.",
                      json.dumps(self.server.requests[0]["messages"]))

    def test_compacts_past_ninety_percent_into_a_handoff_summary(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"] = "40000"
        session = self.session("Survey the numbers.", workspace)
        self.server.replies = [reply("Listing.", [call("c1", "exec_command", {"cmd": "seq 1 30000"})]),
                               reply("SUMMARY: listed 1..30000; nothing else done."),
                               reply("Finished after the summary.")]
        self.run_session(session)
        compaction = self.server.requests[1]
        self.assertNotIn("tools", compaction)
        self.assertIn("handoff summary", compaction["messages"][-1]["content"])
        after = self.server.requests[2]["messages"]
        self.assertEqual([m["role"] for m in after], ["system", "user", "user"])
        self.assertIn("SUMMARY: listed 1..30000", after[-1]["content"])
        self.assertEqual(len(list(session.glob("messages.jsonl.*.archived"))), 1)
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "done")

    def test_long_runs_have_no_step_cap(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Count to sixty.", workspace)
        self.server.replies = [reply("Step %d." % i, [call("c%d" % i, "exec_command", {"cmd": "echo %d" % i})])
                               for i in range(60)] + [reply("Counted to sixty.")]
        self.run_session(session)
        state = json.loads((session / "state.json").read_text())
        self.assertEqual((state["status"], state["steps"]), ("done", 61))

    def test_work_starts_a_scope_that_reports_by_mail(self):
        import shutil, time
        if not shutil.which("systemd-run"):
            self.skipTest("systemd-run is unavailable")
        workspace = self.directory / "workspace"
        workspace.mkdir()
        sent = self.directory / "sent.txt"
        mailer = self.directory / "mailer"
        mailer.write_text('#!/bin/sh\nprintf "%s|%s|%s\\n" "$CLAW_MAIL_AGENT" "$2" "$3" >> ' + str(sent) + '\n')
        mailer.chmod(0o755)
        self.env.update(METTACLAW_WORK_MAIL=str(mailer), METTACLAW_MAIL_NAME="requester-fixture",
                        CETTA_BIN=os.environ["CETTA_BIN"])
        self.server.replies = [reply("Writing.", [call("c1", "exec_command", {"cmd": "echo done > out.txt"})]),
                               reply("out.txt written.")]
        probe = ROOT / ("work_probe_%d.metta" % os.getpid())
        probe.write_text('!(import! &self (library lib_import))\n!(import! &self ./src/coding_work.metta)\n'
                         '!(let $r (work "Write out.txt." ' + json.dumps(str(workspace)) + ') (println! $r))\n')
        root = ROOT / "memory" / "coding"
        before = set(root.iterdir()) if root.is_dir() else set()
        try:
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                                    cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
            self.assertIn("WORK_STARTED work-", result.stdout, result.stdout + result.stderr)
            session = (set(root.iterdir()) - before).pop()
            self.addCleanup(shutil.rmtree, session, True)
            for _ in range(120):
                if sent.is_file():
                    break
                time.sleep(0.5)
            self.assertEqual((workspace / "out.txt").read_text(), "done\n")
            agent, to, text = sent.read_text().split("|", 2)
            self.assertEqual((agent, to), (session.name, "requester-fixture"))
            self.assertIn("done after 2 steps", text)
            self.assertIn("out.txt written.", text)
            # Its model calls are recorded as its own, never as the agent's turns.
            self.assertTrue((session / "completion.json").is_file())
            self.assertFalse((self.directory / "health.json").exists())
        finally:
            probe.unlink()

    def test_anthropic_sessions_cache_the_growing_conversation(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        self.env.update(SYNTHETIC_MODEL="claude-opus-5-5", ANTHROPIC_API_KEY="fixture-secret",
                        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/v1")

        def message(content, stop):
            return {"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                    "content": content, "stop_reason": stop, "usage": {"input_tokens": 10, "output_tokens": 2}}
        self.server.replies = [
            message([{"type": "text", "text": "Running echo."},
                     {"type": "tool_use", "id": "t1", "name": "exec_command", "input": {"cmd": "echo hi"}}], "tool_use"),
            message([{"type": "text", "text": "It printed hi."}], "end_turn")]
        session = self.session("Say what echo hi prints.", workspace)
        self.run_session(session)

        def marked(body):
            return [i for i, m in enumerate(body["messages"]) if any("cache_control" in b for b in m["content"])]
        first, second = self.server.requests
        self.assertEqual(first["system"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual((marked(first), marked(second)), ([0], [0, 2]))
        self.assertEqual(second["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual((session / "report.txt").read_text(), "It printed hi.")

    def test_resume_continues_the_recorded_conversation(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Count to two.", workspace)
        self.server.replies = [reply("One.", [call("c1", "exec_command", {"cmd": "echo 1"})]), reply("Two.")]
        self.run_session(session)
        (session / "stop").write_text("")
        before = (session / "messages.jsonl").read_text()
        self.run_session(session)
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "stopped")
        self.assertEqual((session / "messages.jsonl").read_text(), before)


if __name__ == "__main__":
    unittest.main()

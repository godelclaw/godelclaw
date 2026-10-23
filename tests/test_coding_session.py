"""Coding mode's work loop against a scripted provider: no network, no keys."""
import http.server
import json
import os
from isolated_env import isolated_environ  # noqa: E402
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class Script(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        before_reply = getattr(self.server, "before_reply", None)
        if before_reply:
            before_reply(body)
        status = getattr(self.server, "status", 200)
        if status != 200:
            data = json.dumps({"error": {"type": "fixture", "message": "fixture rejection"}}).encode()
        else:
            reply = self.server.replies.pop(0) if self.server.replies else self.server.final
            data = json.dumps(reply).encode()
        self.send_response(status)
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


class CodingContextTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "src"))
        import coding_io
        self.io = coding_io
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def observe(self, usage, size):
        (self.directory / "reply.json").write_text(json.dumps({"usage": usage}))
        return self.io.context_observe(self.directory, size)

    def test_without_a_count_four_bytes_make_a_token(self):
        self.assertEqual(self.io.context_tokens(self.directory, 4000), 1000)
        self.assertEqual(self.observe({"completion_tokens": 3}, 4000), 0)
        self.assertEqual(self.io.context_tokens(self.directory, 4000), 1000)

    def test_the_provider_rate_applies_when_it_counted_more(self):
        # Anthropic: the cached and newly written prefix count toward the input.
        self.assertEqual(self.observe({"input_tokens": 2, "cache_read_input_tokens": 1000,
                                       "cache_creation_input_tokens": 998}, 6000), 1)
        self.assertEqual(self.io.context_tokens(self.directory, 9000), 3000)
        # Responses: input_tokens is the whole input, cached part included.
        self.assertEqual(self.observe({"input_tokens": 1500,
                                       "input_tokens_details": {"cached_tokens": 1400}}, 6000), 1)
        self.assertEqual(self.io.context_tokens(self.directory, 12000), 3000)
        # A provider counting fewer never lowers the four-byte estimate.
        self.assertEqual(self.observe({"prompt_tokens": 10}, 6000), 1)
        self.assertEqual(self.io.context_tokens(self.directory, 12000), 3000)

    def test_unreadable_replies_change_nothing(self):
        self.assertEqual(self.io.context_observe(self.directory, 100), 0)
        (self.directory / "reply.json").write_text("not json")
        self.assertEqual(self.io.context_observe(self.directory, 100), 0)
        self.assertFalse((self.directory / "context.json").exists())


class EnginePinTests(unittest.TestCase):
    def test_a_session_keeps_the_engine_file_it_started_on(self):
        sys.path.insert(0, str(ROOT / "src"))
        import coding_io
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / "root"; (root / "config").mkdir(parents=True)
            (root / "config" / "runtime-files.json").write_text('["coding_main.metta"]')
            (root / "coding_main.metta").write_text("; fixture\n")
            engine = tmp / "engine-v1"; engine.write_text("engine one")
            link = tmp / "current"; link.symlink_to(engine)
            session = tmp / "memory" / "coding" / "work-pin"; session.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"CETTA_BIN": str(link)}):
                coding_io.freeze_runtime(root, session)
            record = json.loads((session / "runtime.json").read_text())
            self.assertEqual(record["binary"], str(engine))
            self.assertEqual(coding_io.runtime_binary(session), str(engine))
            self.assertEqual(coding_io.engine_problem(session), "")
            # A link flip leaves the session on the file it started with.
            engine2 = tmp / "engine-v2"; engine2.write_text("engine two")
            link.unlink(); link.symlink_to(engine2)
            self.assertEqual(coding_io.runtime_binary(session), str(engine))
            self.assertEqual(coding_io.engine_problem(session), "")
            engine.write_text("engine one, rebuilt in place")
            self.assertIn("has changed since", coding_io.engine_problem(session))
            engine.unlink()
            self.assertIn("is gone", coding_io.engine_problem(session))


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
        self.env = {**isolated_environ(), "PYTHONHOME": sys.prefix, "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            # The agent's own path (run.sh): sessions inherit it, and iter mode's modules are not on it.
            "PYTHONPATH": os.pathsep.join([str(modules), str(ROOT / "src"), str(ROOT / "channels")]),
            "METTACLAW_ACTIVE_ENGINE": "cetta", "METTACLAW_ENGINE": "cetta", "METTACLAW_LLM_TRANSPORT": "native",
            "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "engine"),
            "METTACLAW_LLM_POLICY": "managed", "METTACLAW_LLM_POLICY_PATH": "",
            "METTACLAW_LLM_LEDGER_PATH": str(self.directory / "ledger.db"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_LLM_COMPLETION_PATH": str(self.directory / "completion.json"),
            "METTACLAW_PROVIDER_OBSERVATION_PATH": str(self.directory / "providers.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_WAKE_REQUEST_PATH": str(self.directory / "wake"),
            "METTACLAW_CODING_SESSION": "1",
            "METTACLAW_LIFECYCLE_PATH": str(latch), "SYNTHETIC_MODEL": "fixture-model",
            "METTACLAW_MODEL_STATE_PATH": str(self.directory / "model"),
            "METTACLAW_CONTEXT_WINDOW_TOKENS": "100000",
            "SYNTHETIC_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "SYNTHETIC_API_KEY": "fixture-secret", "SYNTHETIC_EMPTY_BACKOFF": "0"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def session(self, task, workspace, name="session"):
        session = self.directory / name
        session.mkdir()
        (session / "task.json").write_text(json.dumps({"task": task, "workdir": str(workspace), "effort": "default"}))
        return session

    def run_session(self, session):
        result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk",
                                 str(ROOT / "coding_main.metta"), str(session)],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        return result

    def run_probe(self, forms, module="coding_session.metta", cwd=ROOT):
        probe = self.directory / "coding_probe.metta"
        probe.write_text('!(import! &self (library lib_import))\n'
                         '!(import! &self ' + json.dumps(str(ROOT / "src" / module)) + ')\n' + forms)
        result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                                cwd=cwd, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        return result

    def fake_work_scope(self):
        """Record executable scope commands without touching the user's service manager."""
        binary = self.directory / "fixture_bin"
        binary.mkdir()
        active, launches = self.directory / "scope-active", self.directory / "scope-launches.jsonl"
        scripts = {
            "systemctl": 'import os, sys\nfrom pathlib import Path\n'
                         'sys.exit(0 if sys.argv[1:3] == ["--user", "is-active"] and '
                         'Path(os.environ["FIXTURE_SCOPE_ACTIVE"]).is_file() else 3)\n',
            "systemd-run": 'import json, os, sys\n'
                           'try:\n'
                           '    fd = os.open(os.environ["FIXTURE_SCOPE_ACTIVE"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n'
                           'except FileExistsError:\n'
                           '    sys.exit(1)\n'
                           'os.close(fd)\n'
                           'with open(os.environ["FIXTURE_SCOPE_LAUNCHES"], "a") as stream:\n'
                           '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        }
        for name, source in scripts.items():
            executable = binary / name
            executable.write_text("#!" + sys.executable + "\n" + source)
            executable.chmod(0o755)
        self.env.update(PATH=str(binary) + os.pathsep + self.env.get("PATH", ""),
                        METTACLAW_INSTANCE="fixture", FIXTURE_SCOPE_ACTIVE=str(active),
                        FIXTURE_SCOPE_LAUNCHES=str(launches))
        return active, launches

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

    def test_launcher_does_not_wait_on_the_background_scope_output_pipe(self):
        active, launches = self.fake_work_scope()
        fake = self.directory/"fixture_bin/systemd-run"
        fake.write_text(fake.read_text()+
            '\nimport time\ntime.sleep(2)\nos.unlink(os.environ["FIXTURE_SCOPE_ACTIVE"])\n')
        result = self.run_probe('!(println! (work "fixture" '+json.dumps(str(self.directory))+'))',
                                module="coding_work.metta",cwd=self.directory)
        self.assertIn("WORK_STARTED", result.stdout)
        deadline=time.monotonic()+1
        while not active.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(active.exists(),"launcher waited for scope completion; inherited pipe keeps its timeout owner attached")
        deadline=time.monotonic()+3
        while active.exists() and time.monotonic()<deadline:time.sleep(.05)
        self.assertFalse(active.exists())

    def test_yielded_process_streams_while_the_next_provider_call_waits(self):
        workspace=self.directory/"workspace";workspace.mkdir()
        session=self.session("Yield, observe and verify.",workspace)
        self.env["METTACLAW_STREAM_DIR"]=str(self.directory/"display")
        self.server.replies=[reply("Start the delayed command.",[call("c1","exec_command",
            dict(cmd="printf first; sleep 1; printf second; sleep 1; printf third",yield_time_ms=0))])]
        def before(body):
            if len(self.server.requests)==2:
                result=next(m['content'] for m in reversed(body['messages']) if m['role']=='tool')
                identity=result.split('Execution ID: ',1)[1].split('\n',1)[0]
                (session/"inbox.jsonl").write_text(json.dumps("Verify all three pieces.")+"\n")
                time.sleep(1.2)
                self.server.replies=[reply("Observe completion.",[call("c2","exec_poll",
                    dict(execution_id=identity,yield_time_ms=3000))]),reply("All three pieces verified.")]
        self.server.before_reply=before
        self.run_session(session)
        self.assertEqual((session/'report.txt').read_text(),"All three pieces verified.")
        self.assertEqual(len(list((session/'executions').glob('*.log'))),1)
        self.assertEqual(next((session/'executions').glob('*.log')).read_text(),'firstsecondthird')
        self.assertTrue(any('Verify all three pieces.' in str(m.get('content','')) for m in self.server.requests[-1]['messages']))
        events=[json.loads(l) for p in (self.directory/'display').glob('*.jsonl') for l in p.read_text().splitlines()]
        self.assertEqual(len([e for e in events if e['kind']=='tool.started']),1)
        self.assertEqual(len([e for e in events if e['kind']=='tool.finished']),1)
        second_finished=[e['time'] for e in events if e['kind']=='attempt.finished'][1]
        second_output=[e['time'] for e in events if e['kind']=='tool.output' and 'second' in e.get('text','')][0]
        self.assertLess(second_output,second_finished,"yielded output waited for model completion")

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

    def test_steering_during_a_final_reply_continues_the_same_session(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Investigate the task.", workspace)
        def steer(_):
            if len(self.server.requests) == 1:
                (session / "inbox.jsonl").write_text(json.dumps("Keep the source-only qualification.") + "\n")
        self.server.before_reply = steer
        self.server.replies = [reply("Initial report."), reply("Qualified report: inspected, not compiled.")]
        self.run_session(session)
        self.assertEqual(len(self.server.requests), 2)
        continued = self.server.requests[1]["messages"]
        self.assertEqual(continued[-2]["content"], "Initial report.")
        self.assertIn("Keep the source-only qualification.", continued[-1]["content"])
        self.assertEqual((session / "report.txt").read_text(), "Qualified report: inspected, not compiled.")
        self.assertEqual(json.loads((session / "state.json").read_text())["inbox"], 1)

    def test_resuming_uses_prior_messages_and_new_guidance_once(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Keep working in this session.", workspace)
        self.server.replies = [reply("First report.")]
        self.run_session(session)
        (session / "inbox.jsonl").write_text(json.dumps("Now verify the result.") + "\n")
        self.server.replies = [reply("Verified report.")]
        self.run_session(session)
        resumed = self.server.requests[1]["messages"]
        self.assertEqual([m["role"] for m in resumed], ["system", "user", "assistant", "user"])
        self.assertEqual(resumed[2]["content"], "First report.")
        self.assertEqual(sum("Now verify the result." in str(m.get("content")) for m in resumed), 1)
        self.assertEqual((session / "report.txt").read_text(), "Verified report.")
        self.assertFalse(any("_coding_input" in m for m in resumed))

    def test_resume_records_uncertain_tools_without_replaying_them(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Inspect the interrupted command before continuing.", workspace)
        marker = workspace / "counter.txt"; marker.write_text("effect already happened\n")
        pending = call("interrupted", "exec_command", {"cmd": "echo repeated >> counter.txt"})
        messages = [{"role": "system", "content": "fixture instructions"},
                    {"role": "user", "content": "Inspect existing evidence."},
                    {"role": "assistant", "content": "Running command.", "tool_calls": [pending]}]
        (session / "messages.jsonl").write_text("".join(json.dumps(m)+"\n" for m in messages))
        self.server.replies = [reply("The earlier effect needs verification.")]
        self.run_session(session)
        sent = self.server.requests[0]["messages"]
        results = [m for m in sent if m.get("tool_call_id") == "interrupted"]
        self.assertEqual(len(results), 1)
        self.assertIn("unknown", results[0]["content"])
        self.assertIn("not automatically replayed", results[0]["content"])
        self.assertEqual(marker.read_text(), "effect already happened\n")

    def test_restart_does_not_duplicate_already_recorded_steering(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Continue from the existing transcript.", workspace)
        messages = [{"role": "system", "content": "instructions"},
                    {"role": "user", "content": "Original task."},
                    {"role": "user", "content": "From the requester, mid-task: ONE_STEERING_OCCURRENCE", "_coding_input": 0}]
        (session / "messages.jsonl").write_text("".join(json.dumps(m)+"\n" for m in messages))
        (session / "inbox.jsonl").write_text(json.dumps("ONE_STEERING_OCCURRENCE")+"\n")
        (session / "state.json").write_text(json.dumps({"status":"running", "steps":0, "inbox":0}))
        self.server.replies = [reply("Continued.")]
        self.run_session(session)
        sent = self.server.requests[0]["messages"]
        self.assertEqual(sum("ONE_STEERING_OCCURRENCE" in str(m.get("content")) for m in sent), 1)
        self.assertFalse(any("_coding_input" in m for m in sent))

    def test_a_rejected_request_ends_the_session_at_once_with_its_status(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Anything.", workspace)
        self.server.status = 401
        self.run_session(session)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "failed")
        self.assertIn("rejected the request: HTTP 401", (session / "report.txt").read_text())

    def test_incomplete_tool_reply_cannot_execute_an_effect(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Return a complete tool call.", workspace)
        incomplete = reply("Cut off.", [call("cut", "exec_command", {"cmd":"touch must-not-exist"})])
        incomplete["choices"][0]["finish_reason"] = "length"
        self.server.replies = [incomplete]
        self.run_session(session)
        self.assertFalse((workspace / "must-not-exist").exists())
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "failed")

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

    def test_compaction_follows_the_tokens_the_provider_counted(self):
        # Four bytes per token undercounts real requests; the provider's count decides.
        workspace = self.directory / "workspace"
        workspace.mkdir()
        self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"] = "100000"
        session = self.session("Survey the numbers.", workspace)
        counted = reply("Listing.", [call("c1", "exec_command", {"cmd": "seq 1 10"})])
        counted["usage"] = {"prompt_tokens": 70000, "prompt_tokens_details": {"cached_tokens": 0},
                            "completion_tokens": 2}
        self.server.replies = [counted, reply("SUMMARY: listed 1..10."), reply("Finished after the summary.")]
        self.run_session(session)
        self.assertNotIn("tools", self.server.requests[1])
        self.assertIn("handoff summary", self.server.requests[1]["messages"][-1]["content"])
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "done")

    def test_the_chatgpt_model_list_gives_the_largest_window(self):
        items = json.dumps({"models": [{"slug": "small", "context_window": 272000},
                                       {"slug": "large", "context_window": 500000, "max_context_window": 872000},
                                       {"slug": "unreported"}]})
        result = self.run_probe(
            '!(let $list (llm:v1:parse ' + json.dumps(items) + ') (let (JsonArray $items) (llm:v1:get $list "models") '
            '(println! (Windows (chatgpt:window "large" $items) (chatgpt:window "small" $items) '
            '(chatgpt:window "unreported" $items) (chatgpt:window "absent" $items)))))\n', module="native_llm.metta")
        self.assertIn("(Windows 872000 272000 0 0)", result.stdout)

    def test_each_step_asks_the_native_runtime_for_a_chatgpt_window(self):
        # No account is selected here: the read fails, is recorded, and waits.
        self.env["SYNTHETIC_MODEL"] = "chatgpt:fixture"
        del self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"]
        result = self.run_probe('!(import! &self ' + json.dumps(str(ROOT / "src" / "coding_session.metta")) + ')\n'
                                '!(println! (Window (coding:window)))\n', module="native_llm.metta")
        self.assertIn("(Window 200000)", result.stdout)
        observed = json.loads((self.directory / "providers.json").read_text())["model-window"]
        self.assertEqual(observed["attempted_model"], "chatgpt:fixture")
        self.assertNotIn("value", observed)

    def test_a_window_read_never_asks_chatgpt_about_another_providers_model(self):
        # The host's "no ChatGPT model is due" reaches MeTTa as a Symbol, which
        # never equals the String "".
        del self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"]
        result = self.run_probe('!(import! &self ' + json.dumps(str(ROOT / "src" / "coding_session.metta")) + ')\n'
                                '!(println! (Observed (llm:observe-window)))\n'
                                '!(println! (Window (coding:window)))\n', module="native_llm.metta")
        self.assertIn("(Observed true)", result.stdout)
        self.assertIn("(Window 200000)", result.stdout)
        observations = self.directory / "providers.json"
        record = json.loads(observations.read_text()).get("model-window", {}) if observations.exists() else {}
        self.assertNotIn("attempted_model", record)

    def test_a_window_reported_after_the_first_step_is_used(self):
        # A session's provider facts start empty and arrive after its first call.
        workspace = self.directory / "workspace"
        workspace.mkdir()
        del self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"]
        session = self.session("Survey the numbers.", workspace)
        def report_window(_):
            if len(self.server.requests) == 1:
                (self.directory / "providers.json").write_text(json.dumps({"schema": 1, "models": {
                    "observed_at": time.time(), "attempted_at": time.time(),
                    "value": [{"id": "fixture-model", "context": 40000}]}}))
        self.server.before_reply = report_window
        self.server.replies = [reply("Listing.", [call("c1", "exec_command", {"cmd": "seq 1 30000"})]),
                               reply("SUMMARY: listed 1..30000."), reply("Finished after the summary.")]
        self.run_session(session)
        self.assertNotIn("tools", self.server.requests[1])
        self.assertEqual(json.loads((session / "state.json").read_text())["status"], "done")

    def test_repeated_compaction_replaces_handoff_and_keeps_requester_inputs(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"] = "40000"
        session = self.session("Continue the same investigation.", workspace)
        inbox = session / "inbox.jsonl"
        inbox.write_text(json.dumps("FIRST_GUIDANCE: preserve uncertainty.") + "\n")
        def steer(_):
            if len(self.server.requests) == 3:
                with inbox.open("a") as stream:
                    stream.write(json.dumps("SECOND_GUIDANCE: include verification limits.") + "\n")
        self.server.before_reply = steer
        self.server.replies = [
            reply("First evidence.", [call("c1", "exec_command", {"cmd": "seq 1 30000"})]),
            reply("FIRST_HANDOFF: inspected the first range; preserve uncertainty."),
            reply("More evidence.", [call("c2", "exec_command", {"cmd": "seq 30001 60000"})]),
            reply("SECOND_HANDOFF: inspected the next range; no extra verification."),
            reply("Final evidence.", [call("c3", "exec_command", {"cmd": "seq 60001 90000"})]),
            reply("THIRD_HANDOFF: inspected all three ranges; verification remains limited."),
            reply("Finished with the stated limits.")]
        self.run_session(session)
        self.assertEqual(len(self.server.requests), 7)
        second_compaction = self.server.requests[3]
        self.assertNotIn("tools", second_compaction)
        self.assertIn("FIRST_HANDOFF", json.dumps(second_compaction["messages"]))
        third_compaction = self.server.requests[5]
        self.assertNotIn("tools", third_compaction)
        self.assertIn("SECOND_HANDOFF", json.dumps(third_compaction["messages"]))
        self.assertNotIn("FIRST_HANDOFF", json.dumps(third_compaction["messages"]))
        for index, current, replaced in ((2, "FIRST_HANDOFF", ()),
                                         (4, "SECOND_HANDOFF", ("FIRST_HANDOFF",)),
                                         (6, "THIRD_HANDOFF", ("FIRST_HANDOFF", "SECOND_HANDOFF"))):
            after = self.server.requests[index]["messages"]
            self.assertEqual(after[1]["content"], "Continue the same investigation.")
            self.assertEqual(sum(current in str(m.get("content")) for m in after), 1)
            for marker in replaced:
                self.assertNotIn(marker, json.dumps(after))
            self.assertEqual(sum("FIRST_GUIDANCE" in str(m.get("content")) for m in after), 1)
            if index >= 4:
                self.assertEqual(sum("SECOND_GUIDANCE" in str(m.get("content")) for m in after), 1)
        for request in self.server.requests:
            self.assertFalse(any("_coding_input" in m or "_coding_handoff" in m for m in request["messages"]))
        archives = list(session.glob("messages.jsonl.*.archived"))
        self.assertEqual(len(archives), 3)
        self.assertTrue(any("FIRST_HANDOFF" in archive.read_text() for archive in archives))
        self.assertTrue(any("SECOND_HANDOFF" in archive.read_text() for archive in archives))
        self.assertEqual(sorted(sum("_coding_handoff" in json.loads(line) for line in archive.read_text().splitlines())
                                for archive in archives), [0, 1, 1])
        recorded = [json.loads(line) for line in (session / "messages.jsonl").read_text().splitlines()]
        handoffs = [m for m in recorded if "_coding_handoff" in m]
        self.assertEqual(len(handoffs), 1)
        self.assertIn("THIRD_HANDOFF", handoffs[0]["content"])
        self.assertRegex(handoffs[0]["_coding_handoff"], r"^[0-9a-f]{16}$")
        self.assertEqual([m["_coding_input"] for m in recorded if "_coding_input" in m], [0, 1])
        self.assertEqual(json.loads((session / "state.json").read_text())["inbox"], 2)

        self.server.replies = [reply("Continued from the latest handoff.")]
        self.run_session(session)
        self.assertEqual(self.server.requests[7]["messages"],
                         [{k: v for k, v in m.items() if k not in ("_coding_input", "_coding_handoff")} for m in recorded])
        self.assertEqual(len(list(session.glob("messages.jsonl.*.archived"))), 3)

    def test_compaction_preserves_unmarked_handoff_text_from_requesters_and_legacy_sessions(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"] = "40000"
        prefix = "Another model began this task and left this handoff summary; continue from it and do not restart from scratch.\n\n"
        task = prefix + "REQUESTER_TASK: this text is my original instruction."
        legacy = prefix + "LEGACY_HANDOFF: this old record has no local marker."
        session = self.session(task, workspace)
        messages = [{"role": "system", "content": "fixture instructions"},
                    {"role": "user", "content": task}, {"role": "user", "content": legacy},
                    {"role": "assistant", "content": "Existing evidence " + "x" * 40000}]
        (session / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in messages))
        self.server.replies = [reply("CURRENT_HANDOFF: existing evidence summarized."), reply("Continued.")]
        self.run_session(session)
        after = self.server.requests[1]["messages"]
        self.assertEqual([m["content"] for m in after[1:3]], [task, legacy])
        self.assertIn("CURRENT_HANDOFF", after[-1]["content"])
        self.assertFalse(any("_coding_handoff" in m for request in self.server.requests for m in request["messages"]))
        recorded = [json.loads(line) for line in (session / "messages.jsonl").read_text().splitlines()]
        self.assertEqual(sum("_coding_handoff" in m for m in recorded), 1)

    def test_invalid_compaction_keeps_history_and_cursor_and_never_runs_tools(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        self.env["METTACLAW_CONTEXT_WINDOW_TOKENS"] = "40000"
        truncated = reply("Partial handoff.")
        truncated["choices"][0]["finish_reason"] = "length"
        refused = reply("No summary.")
        refused["choices"][0]["message"]["refusal"] = "fixture refusal"
        invalid = [reply(""), reply(" \n\t "), reply(None), truncated, refused,
                   reply("Run this instead.", [call("unsafe-summary", "exec_command", {"cmd": "touch must-not-run"})])]
        for index, summary in enumerate(invalid):
            with self.subTest(summary=index):
                session = self.session("Preserve existing evidence.", workspace, "invalid-%d" % index)
                messages = [{"role": "system", "content": "fixture instructions"},
                            {"role": "user", "content": "Original task."},
                            {"role": "assistant", "content": "EXISTING_EVIDENCE " + "x" * 40000},
                            {"role": "user", "content": "From the requester, mid-task: RECORDED_GUIDANCE", "_coding_input": 0}]
                history = session / "messages.jsonl"
                history.write_text("".join(json.dumps(m) + "\n" for m in messages))
                original = history.read_bytes()
                (session / "inbox.jsonl").write_text(json.dumps("RECORDED_GUIDANCE") + "\n")
                (session / "state.json").write_text(json.dumps({"status": "running", "steps": 7, "inbox": 0}))
                before = len(self.server.requests)
                self.server.replies = [summary]
                self.run_session(session)
                self.assertEqual(len(self.server.requests), before + 1)
                self.assertNotIn("tools", self.server.requests[-1])
                self.assertEqual(history.read_bytes(), original)
                self.assertEqual(list(session.glob("messages.jsonl.*.archived")), [])
                state = json.loads((session / "state.json").read_text())
                self.assertEqual((state["status"], state["steps"], state["inbox"]), ("failed", 7, 0))
                self.assertIn("could not compact", (session / "report.txt").read_text())
                self.assertFalse((workspace / "must-not-run").exists())

        self.server.replies = [reply("Valid handoff after retry."), reply("Continued from existing evidence.")]
        self.run_session(session)
        self.assertEqual(self.server.requests[-2]["messages"][:-1],
                         [{k: v for k, v in m.items() if k != "_coding_input"} for m in messages])
        self.assertEqual(next(session.glob("messages.jsonl.*.archived")).read_bytes(), original)
        self.assertEqual(json.loads((session / "state.json").read_text())["inbox"], 1)

    def test_rewrite_failure_keeps_the_live_history_and_its_archive(self):
        module = self.directory / "fixture_modules" / "coding_rewrite_fault.py"
        module.write_text('import os, shutil\n'
                          '_copy = shutil.copyfile\n'
                          'def install(mode):\n'
                          '    def archive(source, target):\n'
                          '        result = _copy(source, target)\n'
                          '        assert os.path.isfile(source), "live history disappeared"\n'
                          '        if mode == "interrupt": raise OSError("fixture interruption after archive")\n'
                          '        os.chmod(os.path.dirname(source), 0o500)\n'
                          '        return result\n'
                          '    shutil.copyfile = archive\n'
                          '    return 1\n')
        for mode in ("interrupt", "denied"):
            with self.subTest(mode=mode):
                session = self.directory / mode; session.mkdir()
                history = session / "messages.jsonl"
                original = b'{"role": "user", "content": "Preserve original bytes."}\n'
                history.write_bytes(original)
                forms = ('!(import! &self ' + json.dumps(str(module)) + ')\n'
                         '!(py-call (coding_rewrite_fault.install ' + json.dumps(mode) + '))\n'
                         '!(println! (coding:rewrite ' + json.dumps(str(session)) +
                         ' ((coding:message "user" "Replacement transcript."))))\n')
                try:
                    result = self.run_probe(forms)
                finally:
                    session.chmod(0o700)
                self.assertIn("CompactionFailed", result.stdout)
                self.assertEqual(history.read_bytes(), original)
                archives = list(session.glob("messages.jsonl.*.archived"))
                self.assertEqual(len(archives), 1)
                self.assertEqual(archives[0].read_bytes(), original)

    def test_a_session_keeps_the_model_and_effort_it_starts_with(self):
        active, launches = self.fake_work_scope()
        runner = self.directory / "runner"; runner.mkdir()
        workspace = json.dumps(str(self.directory))
        result = self.run_probe('!(println! (work "Chosen." ' + workspace + ' "fixture-other" "medium"))\n'
                                '!(println! (work "Bad model." ' + workspace + ' "bad model"))\n'
                                '!(println! (work "Bad effort." ' + workspace + ' "fixture-other" "loud"))\n',
                                module="coding_work.metta", cwd=runner)
        self.assertIn("WORK_STARTED work-", result.stdout)
        self.assertIn("work: invalid model id 'bad model'", result.stdout)
        self.assertIn('work: unknown effort \\"loud\\"', result.stdout)  # println! escapes the quotes
        [session] = list((runner / "memory" / "coding").iterdir())
        task = json.loads((session / "task.json").read_text())
        self.assertEqual((task["model"], task["effort"]), ("fixture-other", "medium"))
        self.assertEqual((session / "model.metta").read_text(), "(active-model fixture-other)\n")
        deadline = time.monotonic() + 5
        while not launches.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        args = json.loads(launches.read_text())
        self.assertIn("SYNTHETIC_MODEL=fixture-other", args)
        self.assertIn("METTACLAW_MODEL_STATE_PATH=" + str(session / "model.metta"), args)
        # Without a choice, the session keeps the agent's model of that moment.
        active.unlink(); launches.unlink()
        self.run_probe('!(println! (work "Default." ' + workspace + '))\n', module="coding_work.metta", cwd=runner)
        [default] = [s for s in (runner / "memory" / "coding").iterdir() if s != session]
        task = json.loads((default / "task.json").read_text())
        self.assertEqual((task["model"], task["effort"]), ("fixture-model", "high"))

    def test_a_session_calls_the_provider_with_its_own_model(self):
        workspace = self.directory / "workspace"; workspace.mkdir()
        session = self.session("Report.", workspace)
        (session / "model.metta").write_text("(active-model fixture-pinned)\n")
        # The agent's own model file names another model; the session never reads it.
        (self.directory / "model").write_text("(active-model fixture-agent)\n")
        self.env.update(METTACLAW_MODEL_STATE_PATH=str(session / "model.metta"))
        self.run_session(session)
        self.assertEqual({request["model"] for request in self.server.requests}, {"fixture-pinned"})

    def test_work_resume_refuses_a_session_whose_engine_is_gone(self):
        active, launches = self.fake_work_scope()
        runner = self.directory / "runner"
        session = runner / "memory" / "coding" / "work-engine-fixture"
        session.mkdir(parents=True)
        (session / "task.json").write_text(json.dumps({"task": "Long job.", "workdir": str(self.directory)}))
        (session / "state.json").write_text(json.dumps({"status": "interrupted", "steps": 3}))
        (session / "runtime.json").write_text(json.dumps({"root": str(ROOT), "digest": "fixture",
                                                          "binary": str(self.directory / "engine-gone"),
                                                          "binary_sha256": "0" * 64}))
        result = self.run_probe('!(println! (work-resume "work-engine-fixture"))\n', module="coding_work.metta", cwd=runner)
        self.assertIn("the engine this session started on is gone", result.stdout)
        self.assertFalse(launches.exists())

    def test_work_resume_keeps_one_session_and_its_existing_evidence(self):
        active, launches = self.fake_work_scope()
        self.env.update(METTACLAW_ACTIVITY_CALL_SCOPE="parent-conversation", METTACLAW_ACTIVITY_CALL_LANE="interactive")
        runner = self.directory / "runner's directory $(touch RESUME_INJECTION)"
        runner.mkdir()
        identity = "work-resume-fixture"
        session = runner / "memory" / "coding" / identity
        session.mkdir(parents=True)
        workspace = self.directory / "workspace"; workspace.mkdir()
        (session / "task.json").write_text(json.dumps({"task": "Original investigation.", "workdir": str(workspace), "effort": "default"}))
        (session / "plan.json").write_text('{"plan":[{"step":"verify existing evidence","status":"in_progress"}]}\n')
        prior = [{"role": "system", "content": "fixture instructions"},
                 {"role": "user", "content": "Original investigation."},
                 {"role": "assistant", "content": "Existing qualified result."},
                 {"role": "user", "content": "From the requester, mid-task: SAVED_GUIDANCE", "_coding_input": 0}]
        (session / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in prior))
        (session / "inbox.jsonl").write_text(json.dumps("SAVED_GUIDANCE") + "\n")
        (session / "state.json").write_text(json.dumps({"status": "done", "steps": 2, "inbox": 1}))
        stop = session / "stop"; stop.write_text("")
        recycle = session / "recycle.requested"; recycle.write_text("old request\n")
        saved = {name: (session / name).read_bytes() for name in ("task.json", "plan.json", "messages.jsonl")}

        result = self.run_probe('!(println! (work-status ' + json.dumps(identity) + '))\n'
                                '!(println! (work-steer ' + json.dumps(identity) + ' "NEW_GUIDANCE"))\n',
                                module="coding_work.metta", cwd=runner)
        self.assertIn("WORK_STEERED", result.stdout)
        self.assertTrue(stop.is_file())
        self.assertFalse(launches.exists())
        result = self.run_probe('!(println! (work-resume ' + json.dumps(identity) + '))\n',
                                module="coding_work.metta", cwd=runner)
        self.assertIn("WORK_RESUMING " + identity, result.stdout)
        self.assertFalse(stop.exists())
        self.assertFalse(recycle.exists())
        deadline = time.monotonic() + 5
        while not launches.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(active.is_file())
        args = json.loads(launches.read_text())
        self.assertIn("--unit=fixture-" + identity, args)
        self.assertEqual(args[-1], str(session))
        self.assertIn("METTACLAW_COGNITIVE_HEALTH_PATH=" + str(session / "health.json"), args)
        self.assertIn("METTACLAW_LLM_COMPLETION_PATH=" + str(session / "completion.json"), args)
        self.assertIn("METTACLAW_PROVIDER_OBSERVATION_PATH=" + str(session / "providers.json"), args)
        self.assertIn("METTACLAW_LLM_LEDGER_PATH=" + self.env["METTACLAW_LLM_LEDGER_PATH"], args)
        self.assertIn("METTACLAW_ACTIVITY_CALL_SCOPE=" + identity, args)
        self.assertIn("METTACLAW_ACTIVITY_CALL_LANE=background", args)
        self.assertIn("METTACLAW_CODING_SESSION=1", args)
        self.assertIn("TMPDIR=" + str(session / "tmp"), args)
        self.assertNotIn("METTACLAW_ACTIVITY_CALL_SCOPE=parent-conversation", args)
        self.assertFalse((runner / "RESUME_INJECTION").exists())
        for name, content in saved.items():
            self.assertEqual((session / name).read_bytes(), content)
        guidance = [json.loads(line) for line in (session / "inbox.jsonl").read_text().splitlines()]
        self.assertEqual(guidance[:2], ["SAVED_GUIDANCE", "NEW_GUIDANCE"])
        self.assertEqual(len(guidance), 3)
        self.assertIn("existing evidence and plan", guidance[-1])

        before = (session / "inbox.jsonl").read_bytes()
        result = self.run_probe('!(println! (work-resume ' + json.dumps(identity) + '))\n',
                                module="coding_work.metta", cwd=runner)
        self.assertIn("WORK_RUNNING " + identity, result.stdout)
        self.assertEqual(len(launches.read_text().splitlines()), 1)
        self.assertEqual((session / "inbox.jsonl").read_bytes(), before)
        self.server.replies = [reply("Resumed and verified the existing result.")]
        self.run_session(session)
        sent = self.server.requests[0]["messages"]
        self.assertEqual(sent[:len(prior)], [{k: v for k, v in m.items() if k != "_coding_input"} for m in prior])
        for text in guidance:
            self.assertEqual(sum(text in str(m.get("content")) for m in sent), 1)
        self.assertTrue((session / "messages.jsonl").read_bytes().startswith(saved["messages.jsonl"]))
        for name in ("task.json", "plan.json"):
            self.assertEqual((session / name).read_bytes(), saved[name])
        state = json.loads((session / "state.json").read_text())
        self.assertEqual((state["status"], state["steps"], state["inbox"]), ("done", 3, 3))
        self.assertEqual(list((runner / "memory" / "coding").iterdir()), [session])

    def test_work_resume_leaves_a_running_scope_and_its_stop_flag_untouched(self):
        active, launches = self.fake_work_scope()
        active.write_text("already running")
        identity = "work-already-running"
        session = self.directory / "memory" / "coding" / identity
        session.mkdir(parents=True)
        fixtures = {"task.json": '{"task":"Original task."}', "messages.jsonl": '{"role":"user","content":"Existing transcript."}\n',
                    "plan.json": '{"plan":[]}', "inbox.jsonl": '"Existing guidance."\n', "stop": ""}
        for name, text in fixtures.items():
            (session / name).write_text(text)
        result = self.run_probe('!(println! (work-resume ' + json.dumps(identity) + '))\n'
                                '!(println! (work-resume "missing-session"))\n',
                                module="coding_work.metta", cwd=self.directory)
        self.assertIn("WORK_RUNNING " + identity, result.stdout)
        self.assertIn("work-resume: no session missing-session", result.stdout)
        for name, text in fixtures.items():
            self.assertEqual((session / name).read_text(), text)
        self.assertFalse(launches.exists())
        self.assertFalse((self.directory / "memory" / "coding" / "missing-session").exists())

    def test_coding_launch_shares_account_admission_with_an_active_parent(self):
        _, launches = self.fake_work_scope()
        identity = "work-scoped-admission"
        session = self.directory / "memory" / "coding" / identity
        session.mkdir(parents=True)
        (session / "task.json").write_text(json.dumps({"task": "Use the existing account admission.",
                                                       "workdir": str(self.directory), "effort": "default"}))
        self.env.update(METTACLAW_ACTIVITY_CALL_SCOPE="parent-conversation", METTACLAW_ACTIVITY_CALL_LANE="interactive",
                        METTACLAW_ACTIVITY_CALL_CAPACITY="2", METTACLAW_ACTIVITY_INTERACTIVE_RESERVE="1",
                        METTACLAW_OBSERVED_STRATEGY="0")
        # Preserve even a ledger selected indirectly by the parent's health path.
        self.env.pop("METTACLAW_LLM_LEDGER_PATH", None)
        parent_ledger = self.directory / "llm-budget.sqlite3"
        self.run_probe('!(println! (work-resume ' + json.dumps(identity) + '))\n',
                       module="coding_work.metta", cwd=self.directory)
        deadline = time.monotonic() + 5
        while not launches.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        args = json.loads(launches.read_text())
        own = dict(arg.split("=", 1) for arg in args if arg.startswith("METTACLAW_") and "=" in arg)
        self.assertEqual(own["METTACLAW_LLM_LEDGER_PATH"], str(parent_ledger))
        self.assertEqual((own["METTACLAW_ACTIVITY_CALL_SCOPE"], own["METTACLAW_ACTIVITY_CALL_LANE"]),
                         (identity, "background"))
        self.assertNotIn("METTACLAW_ACTIVITY_CALL_CAPACITY", own)
        self.assertNotIn("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", own)
        self.env.update(own)
        observed = {}
        with mock.patch.object(sys, "path", [str(ROOT / "src")] + sys.path), mock.patch.dict(os.environ, self.env, clear=True):
            import llm_policy
            import native_lifetime
            parent = SimpleNamespace(correlation=llm_policy.admit("synthetic", 32), epoch="fixture-parent",
                                     activity="parent-conversation", lane="interactive", cap=1024)
            self.assertTrue(native_lifetime.claim(parent))
            def while_child_is_submitted(_):
                with native_lifetime.transaction() as db:
                    observed["active"] = db.execute("SELECT activity,lane FROM native_calls WHERE state IN ('claimed','submitted')").fetchall()
                # With the parent's slot free, a duplicate child still has no authority.
                observed["parent_settled"] = native_lifetime.settle(parent, "completed", "parent evidence")
                replay = SimpleNamespace(correlation=llm_policy.admit("synthetic", 32), epoch="fixture-replay",
                                         activity=identity, lane="background", cap=1024)
                observed["replay_claimed"] = native_lifetime.claim(replay)
                llm_policy.fail(replay.correlation, sent=False)
                other = SimpleNamespace(correlation=llm_policy.admit("synthetic", 32), epoch="fixture-other",
                                        activity="another-coding-session", lane="background", cap=1024)
                observed["extra_background_claimed"] = native_lifetime.claim(other)
                llm_policy.fail(other.correlation, sent=False)
            self.server.before_reply = while_child_is_submitted
            self.server.replies = [reply("The coding worker used the shared account.")]
            self.run_session(session)
            self.assertCountEqual(observed["active"], [("parent-conversation", "interactive"), (identity, "background")])
            self.assertTrue(observed["parent_settled"])
            self.assertFalse(observed["replay_claimed"])
            self.assertFalse(observed["extra_background_claimed"])
            with native_lifetime.transaction() as db:
                row = db.execute("SELECT state FROM native_calls WHERE activity=?", (identity,)).fetchone()
            self.assertEqual(row, ("completed",))
        self.assertEqual(len(self.server.requests), 1)
        self.assertFalse((session / "llm-budget.sqlite3").exists())
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

    def test_commands_run_without_the_agent_runtime_environment(self):
        # A session's test run once wrote into the session's own recycle and
        # health files and into the live ledger, through inherited METTACLAW_* paths.
        self.env.update(ANTHROPIC_API_KEY="fixture-anthropic", OPENAI_API_KEY="fixture-openai",
                        TAVILY_API_KEY="fixture-tavily", TG_BOT_TOKEN="fixture-telegram",
                        AWS_SECRET_ACCESS_KEY="fixture-aws", SSH_AUTH_SOCK="/fixture/agent.sock",
                        KEEP_THIS="kept")
        workspace = self.directory / "workspace"
        workspace.mkdir()
        listing = ('env | cut -d= -f1 | grep -E "^(METTACLAW_|PYTHONPATH$|.*_API_KEY$|'
                   '.*_TOKEN$|AWS_.*|SSH_AUTH_SOCK$|KEEP_THIS$)" | sort | tr "\\n" " "')
        result = self.run_probe('!(Out (coding:exec %s (json-parse %s)))'
                                % (json.dumps(str(workspace)), json.dumps(json.dumps({"cmd": listing}))))
        out = result.stdout[result.stdout.index("(Out "):]
        self.assertIn("Exit code: 0", out)
        self.assertIn("KEEP_THIS", out)
        for name in ("METTACLAW_", "PYTHONPATH", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                     "SYNTHETIC_API_KEY", "TAVILY_API_KEY", "TG_BOT_TOKEN",
                     "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK"):
            self.assertNotIn(name, out)

    def test_ssh_agent_requires_an_explicit_coding_opt_in(self):
        self.env.update(SSH_AUTH_SOCK="/fixture/agent.sock", METTACLAW_CODING_ALLOW_SSH_AUTH="1")
        workspace = self.directory / "workspace"
        workspace.mkdir()
        command = 'printf "%s" "$SSH_AUTH_SOCK"'
        result = self.run_probe('!(Out (coding:exec %s (json-parse %s)))'
                                % (json.dumps(str(workspace)), json.dumps(json.dumps({"cmd": command}))))
        self.assertIn("/fixture/agent.sock", result.stdout)

    def test_agent_recycle_does_not_end_an_independent_coding_scope(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        session = self.session("Finish despite the agent recycle.", workspace)
        Path(self.env["METTACLAW_RECYCLE_REQUEST_PATH"]).write_text("agent engine switch\n")
        self.server.replies = [reply("Coding scope completed independently.")]
        self.run_session(session)
        state = json.loads((session / "state.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertIn("completed independently", (session / "report.txt").read_text())

    def test_failed_session_reports_plan_changes_and_resume_command(self):
        workspace = self.directory / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        (workspace / "unfinished.txt").write_text("valuable work\n")
        session = self.session("Preserve a useful failure handover.", workspace)
        (session / "plan.json").write_text('{"plan":[{"step":"verify","status":"in_progress"}]}\n')
        self.server.replies = [{"id": "r", "choices": [{"message": {
            "role": "assistant", "content": "", "refusal": "fixture refusal"},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}]
        self.run_session(session)
        state = json.loads((session / "state.json").read_text())
        report = (session / "report.txt").read_text()
        self.assertEqual(state["status"], "failed")
        self.assertIn("provider refused", report.lower())
        self.assertIn("in_progress", report)
        self.assertIn("?? unfinished.txt", report)
        self.assertIn('(work-resume "session")', report)

    def test_a_failed_call_names_the_python_error_and_its_message(self):
        fixture = self.directory / "fixture_modules" / "boom_fixture.py"
        fixture.write_text('def boom():\n    raise RuntimeError("provider said no: 429")\n')
        result = self.run_probe('!(import! &self %s)\n'
                                '!(Text (case (catch (py-call (boom_fixture.boom))) '
                                '(((Error $where $why) (coding:error-text $where $why)) ($other none))))'
                                % json.dumps(str(fixture)))
        self.assertIn("RuntimeError: provider said no: 429", result.stdout)

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

    def test_progress_names_the_commands_of_a_silent_reply(self):
        calls = [call("c1", "exec_command", {"cmd": "rg -n record_command src"}),
                 call("c2", "apply_patch", {"patch": "*** Begin Patch\n*** Update File: a.py\n-x\n+y\n*** Add File: b.py\n+z\n*** End Patch"}),
                 call("c3", "update_plan", {"plan": []})]
        probe = ROOT / ("progress_probe_%d.metta" % os.getpid())
        probe.write_text('!(import! &self (library lib_import))\n!(import! &self ./src/coding_session.metta)\n'
                         '!(let $calls (coding:items (json-parse ' + json.dumps(json.dumps(calls)) + ')) '
                         '(println! (coding:progress "" $calls)))\n'
                         '!(println! (coding:progress "Reading the receipts." ()))\n')
        try:
            result = subprocess.run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                                    cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        finally:
            probe.unlink()
        lines = result.stdout.splitlines()
        self.assertIn('"$ rg -n record_command src · apply_patch Update File: a.py Add File: b.py · update_plan"',
                      lines, result.stdout + result.stderr)
        self.assertIn('"Reading the receipts."', lines)

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

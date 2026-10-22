"""Coding mode's work loop against a scripted provider: no network, no keys."""
import http.server
import json
import os
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

    def test_second_compaction_and_resume_keep_handoffs_and_steering(self):
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
            reply("Finished with the stated limits.")]
        self.run_session(session)
        self.assertEqual(len(self.server.requests), 5)
        second_compaction = self.server.requests[3]
        self.assertNotIn("tools", second_compaction)
        self.assertIn("FIRST_HANDOFF", json.dumps(second_compaction["messages"]))
        after = self.server.requests[4]["messages"]
        for marker in ("FIRST_GUIDANCE", "SECOND_GUIDANCE", "FIRST_HANDOFF", "SECOND_HANDOFF"):
            self.assertEqual(sum(marker in str(m.get("content")) for m in after), 1)
        archives = list(session.glob("messages.jsonl.*.archived"))
        self.assertEqual(len(archives), 2)
        self.assertTrue(any("FIRST_HANDOFF" in archive.read_text() for archive in archives))
        recorded = [json.loads(line) for line in (session / "messages.jsonl").read_text().splitlines()]
        self.assertEqual([m["_coding_input"] for m in recorded if "_coding_input" in m], [0, 1])
        self.assertEqual(json.loads((session / "state.json").read_text())["inbox"], 2)

        self.server.replies = [reply("Continued from the preserved handoffs.")]
        self.run_session(session)
        self.assertEqual(self.server.requests[5]["messages"],
                         [{k: v for k, v in m.items() if k != "_coding_input"} for m in recorded])
        self.assertEqual(len(list(session.glob("messages.jsonl.*.archived"))), 2)

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
        deadline = time.monotonic() + 5
        while not launches.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(active.is_file())
        args = json.loads(launches.read_text())
        self.assertIn("--unit=fixture-" + identity, args)
        self.assertEqual(args[-1], str(session))
        self.assertIn("METTACLAW_COGNITIVE_HEALTH_PATH=" + str(session / "health.json"), args)
        self.assertIn("METTACLAW_LLM_COMPLETION_PATH=" + str(session / "completion.json"), args)
        self.assertIn("METTACLAW_LLM_LEDGER_PATH=" + self.env["METTACLAW_LLM_LEDGER_PATH"], args)
        self.assertIn("METTACLAW_ACTIVITY_CALL_SCOPE=" + identity, args)
        self.assertIn("METTACLAW_ACTIVITY_CALL_LANE=background", args)
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

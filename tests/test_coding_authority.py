"""Coding-session authority, single-use run tokens, and checkpoint-checked re-pinning.

Correspondence with the Lean 4 models in MeTTapedia (`lean/pettaclaw/`):
- `test_stale_reaper_does_not_overwrite_resumed_run`:
  `CodingSessionAuthority.staleReaper`, `fixed_stale_reaper_skips`, `fixed_never_marks_live_run` (Trace 1).
- `test_stale_launch_exits_in_both_registration_orders`:
  `CodingSessionAuthority.staleLaunch`, `fixed_stale_launch_exits`, `supersededFirst`, `fixed_superseded_first_exits` (Trace 2).
- `test_duplicate_start_with_same_token_refused`:
  `CodingSessionAuthority.fixed_tokens_consumed_once` (consuming changes `reserved` -> `started`; single-use token).
- `test_launching_reservation_displayed_and_superseded_by_next_resume`:
  `CodingSessionAuthority.reserve`, `fixed_reservation_is_newest` (`phase = "reserved"` with inactive scope shows `launching since <date>`).
- `test_legacy_pin_without_phase_displays_predates_run_tokens_and_refuses_plain_resume`:
  `CodingSessionAuthority` scope boundary (pre-token bundles lack `"phase"` in `runtime.json` and require `"current"` re-pin).
- `test_repin_replaces_manifest_keeps_one_previous_preserves_executions_and_logs_run`:
  `CodingSessionRepin.repin`, `repin_keeps_executions`, `repin_keeps_uncertain`, `repin_keeps_previous`, `repin_same_is_noop`.
- `test_repin_failed_check_leaves_manifest_unchanged_and_deletes_copy`:
  `CodingSessionRepin.failed_check_changes_nothing`.
- `test_repin_crash_before_rename_leaves_manifest_unchanged`:
  `CodingSessionRepin.crash_reads_old_or_checked`.
- `test_repin_checkpoint_changed_during_check_refuses_while_inbox_append_succeeds`:
  `CodingSessionRepin.rechecked_pairs_current`, `unrechecked_pairs_stale`, `rechecked_keeps_inbox`.
- `test_check_only_boundary_makes_no_provider_or_ledger_calls_and_keeps_live_files_identical`:
  `CodingSessionRepin` check-only reader isolation (`coding_check.metta`, `METTACLAW_CODING_CHECK=1`).
- `test_torn_runs_jsonl_tail_ignored_and_resume_setting_current`:
  `CodingSessionRepin` `runs.jsonl` torn-tail resilience and `(setting "work" "resume" "current")`.
- `test_world_selection_map_supports_multiple_retirements`:
  `CodingSessionLifecycle` multi-retirement `World.selection` map (`dict[str, object]`).
- `test_repin_manifest_changed_during_check_refuses`:
  `CodingSessionRepin.rechecked_pairs_current` manifest `(run, phase)` recheck under `.ending.lock`.
- `test_single_ending_implementation_shared_by_reap_stop_and_resume`:
  Single `coding_io.settle_ending` handover report implementation shared across MeTTa and Python.
- `test_work_stop_revokes_reserved_launch_without_recording_ending`:
  `CodingSessionAuthority` `work-stop` revokes `"reserved"` launch token (`phase = "ended"`).
"""

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]

from isolated_env import isolated_environ  # noqa: E402
import apply_patch  # noqa: E402
import coding_estate  # noqa: E402
import coding_io  # noqa: E402
import native_lifetime  # noqa: E402
import native_llm_host  # noqa: E402


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.server.requests.append(body)
        reply = (
            self.server.replies.pop(0)
            if self.server.replies
            else {
                "id": "r",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Finished run."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
        )
        data = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)


def _manifest_token(session):
    try:
        return str(json.loads((Path(session) / "runtime.json").read_text(encoding="utf-8")).get("run") or "")
    except (OSError, ValueError):
        return ""


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class CodingAuthorityAndRepinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        coding_estate._engine_digest_cache.clear()
        coding_estate._runtime_blocks_cache.clear()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
        self.server.requests = []
        self.server.replies = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        latch = self.directory / "lifecycle.json"
        latch.write_text('{"schema":1,"state":"running"}', encoding="utf-8")
        (self.directory / "AGENTS.md").write_text("# Ancestor guidance outside workspace root\n", encoding="utf-8")
        modules = self.directory / "fixture_modules"
        modules.mkdir()
        (modules / "openai.py").write_text("class OpenAI:\n    pass\n", encoding="utf-8")

        self.mail_log = self.directory / "mail.jsonl"
        mailer = self.directory / "fake-claw-mail"
        mailer.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "msg_id = args[args.index('--id') + 1] if '--id' in args else 'no-id'\n"
            "with open(%r, 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps({'agent': os.environ.get('CLAW_MAIL_AGENT'), 'args': args, 'id': msg_id}) + '\\n')\n"
            "print(json.dumps({'status': 'accepted', 'id': msg_id}))\n" % str(self.mail_log),
            encoding="utf-8",
        )
        mailer.chmod(0o755)

        self.stream_dir = self.directory / "stream"
        self.stream_dir.mkdir(exist_ok=True)
        self.env = {
            **isolated_environ(),
            "PYTHONHOME": sys.prefix,
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": os.pathsep.join([str(modules), str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]),
            "METTACLAW_ACTIVE_ENGINE": "cetta",
            "METTACLAW_ENGINE": "cetta",
            "METTACLAW_LLM_TRANSPORT": "native",
            "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "engine"),
            "METTACLAW_LLM_POLICY": "managed",
            "METTACLAW_LLM_POLICY_PATH": "",
            "METTACLAW_LLM_LEDGER_PATH": str(self.directory / "ledger.db"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_LLM_COMPLETION_PATH": str(self.directory / "completion.json"),
            "METTACLAW_PROVIDER_OBSERVATION_PATH": str(self.directory / "providers.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_WAKE_REQUEST_PATH": str(self.directory / "wake"),
            "METTACLAW_CODING_SESSION": "1",
            "METTACLAW_LIFECYCLE_PATH": str(latch),
            "METTACLAW_STREAM_DIR": str(self.stream_dir),
            "SYNTHETIC_MODEL": "fixture-model",
            "METTACLAW_MODEL_STATE_PATH": str(self.directory / "model"),
            "METTACLAW_CONTEXT_WINDOW_TOKENS": "100000",
            "SYNTHETIC_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "SYNTHETIC_API_KEY": "fixture-secret",
            "SYNTHETIC_EMPTY_BACKOFF": "0",
            "METTACLAW_WORK_MAIL": str(mailer),
            "METTACLAW_MAIL_NAME": "requester-agent",
            "METTACLAW_MAIL_SPACE": "test-space",
        }
        self.fake_work_scope()

    def fake_work_scope(self):
        binary = self.directory / "fixture_bin"
        binary.mkdir(exist_ok=True)
        self.active = self.directory / "scope.active"
        self.launches = self.directory / "scope.launches"
        scripts = {
            "systemctl": (
                "import os, sys\n"
                'if sys.argv[1:4] == ["--user", "is-active", "--quiet"]:\n'
                '    sys.exit(0 if os.path.exists(os.environ["FIXTURE_SCOPE_ACTIVE"]) else 3)\n'
                "sys.exit(0)\n"
            ),
            "systemd-run": (
                "import json, os, sys\n"
                'with open(os.environ["FIXTURE_SCOPE_LAUNCHES"], "a", encoding="utf-8") as stream:\n'
                '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            ),
        }
        for name, source in scripts.items():
            executable = binary / name
            executable.write_text("#!" + sys.executable + "\n" + source, encoding="utf-8")
            executable.chmod(0o755)
        self.env.update(
            PATH=str(binary) + os.pathsep + self.env.get("PATH", ""),
            METTACLAW_INSTANCE="fixture",
            FIXTURE_SCOPE_ACTIVE=str(self.active),
            FIXTURE_SCOPE_LAUNCHES=str(self.launches),
        )

    def make_session(self, name="work-auth", *, task="Inspect workspace", status="running", owner="999999:111"):
        coding_dir = self.directory / "memory" / "coding"
        session = coding_dir / name
        session.mkdir(parents=True, exist_ok=True)
        workspace = self.directory / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / ".git").mkdir(exist_ok=True)
        (session / "task.json").write_text(
            json.dumps(
                {
                    "task": task,
                    "workdir": str(workspace),
                    "requester": "requester-agent",
                    "requester_space": "test-space",
                    "model": "fixture-model",
                    "effort": "default",
                }
            ),
            encoding="utf-8",
        )
        if status is not None:
            (session / "state.json").write_text(
                json.dumps(
                    {
                        "status": status,
                        "steps": 2,
                        "progress": "step 2",
                        "delivery": "none" if status == "running" else "pending",
                        "delivery_version": 2,
                        "worker_owner": owner,
                        "inbox": 0,
                        "updated": 1761200000.0,
                    }
                ),
                encoding="utf-8",
            )
        return session

    def run_work_probe(self, forms):
        probe = ROOT / ("authority_probe_%d.metta" % os.getpid())
        probe.write_text(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./src/coding_work.metta)\n" + forms,
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=self.directory,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=90,
            )
        finally:
            probe.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_worker(self, session, token):
        env = {**self.env, "METTACLAW_CODING_RUN_TOKEN": str(token)}
        with open(session / "run.log", "a", encoding="utf-8") as log_stream:
            result = subprocess.run(
                [
                    os.environ["CETTA_BIN"],
                    "--lang",
                    "petta",
                    "--import-mode",
                    "ancestor-walk",
                    str(ROOT / "coding_main.metta"),
                    str(session),
                ],
                cwd=ROOT,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
            )
        self.assertEqual(result.returncode, 0)
        return result

    def test_stale_reaper_does_not_overwrite_resumed_run(self):
        """Trace 1 (`staleReaper` / `fixed_stale_reaper_skips`):
        1. Run 1 is dead (`state.json` says `running`, `worker_owner = O1`, scope inactive).
        2. Reaper observes `(running, O1)` before pausing.
        3. `work-resume` settles Run 1 under `.ending.lock` and reserves Run 2 (`T2`).
        4. Worker 2 consumes `T2` (`phase = "started"`, `worker_owner = O2`, `status = "running"`).
        5. Paused reaper calls `mark_state(..., expected_owner=O1)` and `work:ended` — it must
           return 0, leave `state.json`, `report.txt`, and `runtime.json` untouched, and send
           no second notification.
        """
        session = self.make_session("work-trace1", status="running", owner="999991:111")
        coding_io.freeze_runtime(ROOT, session)
        t1 = _manifest_token(session)
        if hasattr(coding_io, "consume_run"):
            self.assertEqual(coding_io.consume_run(session, t1, "999991:111"), 1)

        # Reaper reads state.json before resume
        observed_state = json.loads((session / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(observed_state["status"], "running")
        self.assertEqual(observed_state["worker_owner"], "999991:111")

        # Double-ending case (kills Survivor 2): if O1 is settled to "interrupted" before a paused
        # reaper publishes for O1 (including when runtime.json still has phase="started" or has no phase),
        # mark_state(..., expected_owner=O1) must return 0 solely because status != "running",
        # leaving state.json and delivery_id unchanged and sending no second notification.
        double_session = self.make_session("work-double-end", status="running", owner="999991:111")
        self.assertEqual(coding_io.settle_ending(double_session, expected_owner="999991:111"), "interrupted")
        settled_bytes = (double_session / "state.json").read_bytes()
        settled_state = json.loads(settled_bytes.decode("utf-8"))
        settled_delivery_id = settled_state["delivery_id"]
        self.assertTrue(settled_delivery_id)
        self.assertEqual(
            coding_io.mark_state(
                double_session, "interrupted", "second report", "other-id", expected_owner="999991:111"
            ),
            0,
        )
        self.assertEqual((double_session / "state.json").read_bytes(), settled_bytes)
        self.assertEqual(
            json.loads((double_session / "state.json").read_text(encoding="utf-8"))["delivery_id"],
            settled_delivery_id,
        )
        # Also verify when runtime.json has phase == "started" and matching run while state.json is "interrupted"
        (double_session / "runtime.json").write_text(
            json.dumps({"root": str(ROOT), "digest": "0" * 64, "run": "r-double", "phase": "started"}),
            encoding="utf-8",
        )
        settled_with_run = {**settled_state, "run": "r-double"}
        (double_session / "state.json").write_text(json.dumps(settled_with_run), encoding="utf-8")
        settled_with_run_bytes = (double_session / "state.json").read_bytes()
        self.assertEqual(
            coding_io.mark_state(
                double_session, "interrupted", "second report", "other-id-2", expected_owner="999991:111"
            ),
            0,
        )
        self.assertEqual((double_session / "state.json").read_bytes(), settled_with_run_bytes)
        reap_out = self.run_work_probe('!(println! (ReapDouble (work:reap-one "work-double-end")))\n').stdout
        self.assertIn("(ReapDouble 0)", reap_out)
        shutil.rmtree(double_session)

        # Resume runs while reaper is paused between read and publish
        res = self.run_work_probe('!(println! (work-resume "work-trace1"))\n')
        self.assertIn("WORK_RESUMING work-trace1", res.stdout)

        # Run 1's interrupted state was settled and Run 2 was reserved under .ending.lock
        manifest_reserved = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_reserved.get("phase"), "reserved")
        t2 = _manifest_token(session)
        self.assertTrue(t2)
        self.assertNotEqual(t1, t2)

        # Even before Worker 2 starts, a stale reaper for 999991:111 must be rejected because
        # work-resume already settled Run 1 under .ending.lock
        rc_before_w2 = coding_io.mark_state(
            session, "interrupted", "stale reaper report", "stale-id-1", expected_owner="999991:111"
        )
        self.assertEqual(rc_before_w2, 0)

        # Reserved-phase guard (kills Survivor 3): if state.json is running with owner O1 (no run mismatch)
        # while runtime.json has phase == "reserved" for a new token, a reaper publishing with expected O1
        # must be refused solely by `runtime.get("phase") != "started"`.
        saved_settled_state = (session / "state.json").read_bytes()
        (session / "state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "steps": 2,
                    "progress": "step 2",
                    "delivery": "none",
                    "delivery_version": 2,
                    "worker_owner": "999991:111",
                    "inbox": 0,
                    "updated": 1761200000.0,
                }
            ),
            encoding="utf-8",
        )
        running_o1_bytes = (session / "state.json").read_bytes()
        self.assertEqual(
            coding_io.mark_state(
                session, "interrupted", "reaper on reserved", "stale-id-reserved", expected_owner="999991:111"
            ),
            0,
        )
        self.assertEqual((session / "state.json").read_bytes(), running_o1_bytes)
        self.assertEqual(
            json.loads((session / "runtime.json").read_text(encoding="utf-8"))["phase"],
            "reserved",
        )
        (session / "state.json").write_bytes(saved_settled_state)

        # Live-owner guard (kills Survivor 4): freeze_runtime and repin_session must NOT settle a session
        # whose state.json is running with a LIVE worker_owner; state.json stays "running".
        live_owner = f"{os.getpid()}:{native_lifetime.process_start(os.getpid()) or 'portable'}"
        self.assertTrue(coding_io._owner_alive(live_owner))
        live_session = self.make_session("work-live-owner", status="running", owner=live_owner)
        coding_io.freeze_runtime(ROOT, live_session)
        self.assertEqual(
            json.loads((live_session / "state.json").read_text(encoding="utf-8"))["status"],
            "running",
        )
        self.assertFalse((live_session / "report.txt").exists())
        repin_status, _ = coding_io.repin_session(ROOT, live_session)
        self.assertEqual(repin_status, "same")
        self.assertEqual(
            json.loads((live_session / "state.json").read_text(encoding="utf-8"))["status"],
            "running",
        )
        self.assertFalse((live_session / "report.txt").exists())
        shutil.rmtree(live_session)

        # Worker 2 consumes T2 and is now live with owner 999992:222
        self.assertEqual(coding_io.consume_run(session, t2, "999992:222"), 1)
        live_state = json.loads((session / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(live_state["status"], "running")
        self.assertEqual(live_state["worker_owner"], "999992:222")
        self.assertEqual(live_state["run"], t2)

        # Now the paused reaper tries to publish its stale observation for 999991:111
        rc_after_w2 = coding_io.mark_state(
            session, "interrupted", "stale reaper clobber", "stale-id-2", expected_owner="999991:111"
        )
        self.assertEqual(rc_after_w2, 0)

        # State and manifest still belong to live Run 2
        after_state = json.loads((session / "state.json").read_text(encoding="utf-8"))
        after_manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(after_state["status"], "running")
        self.assertEqual(after_state["worker_owner"], "999992:222")
        self.assertEqual(after_state["run"], t2)
        self.assertEqual(after_manifest["phase"], "started")
        self.assertEqual(after_manifest["run"], t2)
        self.assertFalse((session / "report.txt").exists())

    def test_stale_launch_exits_in_both_registration_orders(self):
        """Trace 2 (`staleLaunch` / `fixed_stale_launch_exits` and `supersededFirst` / `fixed_superseded_first_exits`):
        Two resumes produce tokens T1 and T2 (T2 supersedes T1).
        Order A (`staleLaunch`): Worker 2 runs with T2 and finishes (`done`); then delayed Worker 1
          starts with T1. Worker 1 must exit without writing `state.json`, `messages.jsonl`, or `report.txt`,
          making 0 provider calls, and appending one refusal line to `run.log`.
        Order B (`supersededFirst`): Delayed Worker 1 with T1 registers first before Worker 2 with T2.
          Worker 1 sees `manifest.run == T2 != T1`, exits immediately without consuming T2, and Worker 2
          then consumes T2 and runs to completion.
        """
        # --- Order A: T2 runs and finishes, then delayed T1 starts ---
        s_a = self.make_session("work-trace2a", status="stopped", owner="100:1")
        coding_io.freeze_runtime(ROOT, s_a)

        self.run_work_probe('!(println! (work-resume "work-trace2a"))\n')
        t1 = _manifest_token(s_a)
        self.run_work_probe('!(println! (work-resume "work-trace2a"))\n')
        t2 = _manifest_token(s_a)

        # Worker 2 runs with T2 and finishes
        self.server.requests.clear()
        self.run_worker(s_a, t2)
        self.assertEqual(len(self.server.requests), 1)
        state_after_w2 = (s_a / "state.json").read_bytes()
        msgs_after_w2 = (s_a / "messages.jsonl").read_bytes()
        guidance_after_w2 = (s_a / "guidance.json").read_bytes()
        report_after_w2 = (s_a / "report.txt").read_bytes()
        stream_after_w2 = {p.name: p.read_bytes() for p in self.stream_dir.glob("*.jsonl")}

        # Now delayed Worker 1 starts with superseded token T1
        self.server.requests.clear()
        self.run_worker(s_a, t1)
        self.assertEqual(len(self.server.requests), 0)
        self.assertEqual((s_a / "state.json").read_bytes(), state_after_w2)
        self.assertEqual((s_a / "messages.jsonl").read_bytes(), msgs_after_w2)
        self.assertEqual((s_a / "guidance.json").read_bytes(), guidance_after_w2)
        self.assertEqual((s_a / "report.txt").read_bytes(), report_after_w2)
        self.assertEqual({p.name: p.read_bytes() for p in self.stream_dir.glob("*.jsonl")}, stream_after_w2)
        self.assertNotEqual(t1, t2)
        log_a = (s_a / "run.log").read_text(encoding="utf-8")
        self.assertIn(f"refused run token {t1}", log_a)
        self.assertIn(f'(CodingSuperseded "{t1}")', log_a)

        # --- Order B: Superseded Worker 1 (T1) registers before Worker 2 (T2) ---
        s_b = self.make_session("work-trace2b", status="stopped", owner="200:1")
        coding_io.freeze_runtime(ROOT, s_b)

        self.run_work_probe('!(println! (work-resume "work-trace2b"))\n')
        tb1 = _manifest_token(s_b)
        self.run_work_probe('!(println! (work-resume "work-trace2b"))\n')
        tb2 = _manifest_token(s_b)

        # Superseded Worker 1 runs first with tb1 (kills Survivor 5: must not enter coding:start,
        # so messages.jsonl, guidance.json, and stream_sink are untouched before Worker 2 runs)
        stream_before_b = {p.name: p.read_bytes() for p in self.stream_dir.glob("*.jsonl")}
        self.server.requests.clear()
        self.run_worker(s_b, tb1)
        self.assertEqual(len(self.server.requests), 0)
        self.assertFalse((s_b / "messages.jsonl").exists())
        self.assertFalse((s_b / "guidance.json").exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.stream_dir.glob("*.jsonl")}, stream_before_b)
        log_b = (s_b / "run.log").read_text(encoding="utf-8")
        self.assertIn(f"refused run token {tb1}", log_b)
        self.assertIn(f'(CodingSuperseded "{tb1}")', log_b)
        # Manifest is still reserved for tb2!
        manifest_still_reserved = json.loads((s_b / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_still_reserved.get("phase"), "reserved")
        self.assertEqual(manifest_still_reserved.get("run"), tb2)

        # Now Worker 2 runs with tb2 and succeeds
        self.run_worker(s_b, tb2)
        self.assertEqual(len(self.server.requests), 1)
        final_b_state = json.loads((s_b / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(final_b_state["status"], "done")
        self.assertEqual(final_b_state.get("run"), tb2)

    def test_duplicate_start_with_same_token_refused(self):
        """`fixed_tokens_consumed_once`: a second start with the same token fails, both while
        the first run is `started` and after the first run has `ended` while its token is still newest.
        Also verifies conditional worker state writes drop stale owners/tokens.
        """
        session = self.make_session("work-dup", status="stopped", owner="300:1")
        coding_io.freeze_runtime(ROOT, session)
        token = _manifest_token(session)

        # First worker start with token succeeds and finishes (`phase == "ended"`)
        self.server.requests.clear()
        self.run_worker(session, token)
        self.assertEqual(len(self.server.requests), 1)
        before_state = (session / "state.json").read_bytes()

        # Second worker start with the SAME token fails (`phase == "ended"`, single-use token)!
        self.server.requests.clear()
        self.run_worker(session, token)
        self.assertEqual(len(self.server.requests), 0)
        self.assertEqual((session / "state.json").read_bytes(), before_state)

        # Reserve a fresh token to test consume_run and write_worker_state directly
        self.run_work_probe('!(println! (work-resume "work-dup"))\n')
        t2 = _manifest_token(session)
        self.assertEqual(coding_io.consume_run(session, t2, "300:1"), 1)
        self.assertEqual(coding_io.consume_run(session, t2, "300:2"), 0)
        self.assertEqual(
            coding_io.write_worker_state(session, t2, "wrong-owner", "running", 3, "bad", 0),
            0,
        )
        self.assertEqual(
            coding_io.write_worker_state(session, "wrong-token", "300:1", "running", 3, "bad", 0),
            0,
        )
        # Phase guard (kills Survivor 1): if runtime.json has phase == "ended" while its run is still t2
        # and state.json is still "running" with owner "300:1", write_worker_state must return 0
        # and leave state.json unchanged.
        running_state_bytes = (session / "state.json").read_bytes()
        ended_manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        ended_manifest["phase"] = "ended"
        (session / "runtime.json").write_text(json.dumps(ended_manifest), encoding="utf-8")
        self.assertEqual(
            coding_io.write_worker_state(session, t2, "300:1", "running", 4, "after ended", 0),
            0,
        )
        self.assertEqual((session / "state.json").read_bytes(), running_state_bytes)
        ended_manifest["phase"] = "started"
        (session / "runtime.json").write_text(json.dumps(ended_manifest), encoding="utf-8")
        self.assertEqual(
            coding_io.write_worker_state(
                session, t2, "300:1", "done", 3, "completed", 0, delivery_id="deliv-1", report="Final report"
            ),
            1,
        )
        manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["phase"], "ended")

    def test_launching_reservation_displayed_and_superseded_by_next_resume(self):
        """A reserved, unconsumed manifest (`phase == "reserved"`, scope inactive) displays
        `launching` / `launching since <date>` in `context_view` and `work-status`, and a second
        resume supersedes it with a fresh token and appends to `run.log` (`>>`).
        """
        session = self.make_session("work-launching", status="stopped", owner="400:1")
        coding_io.freeze_runtime(ROOT, session)
        t1 = _manifest_token(session)
        manifest1 = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest1.get("phase"), "reserved")

        view = coding_estate.context_view(self.directory, world=coding_estate.World(today="2026-11-06"))
        self.assertIn("1 launching", view)
        self.assertRegex(view, r"work-launching launching since \d{4}-\d{2}-\d{2}")

        status_out = self.run_work_probe('!(println! (work-status "work-launching"))\n').stdout
        self.assertRegex(status_out, r"work-launching · launching since \d{4}-\d{2}-\d{2}")

        # Second resume supersedes the unconsumed reservation without needing any timeout
        res2 = self.run_work_probe('!(println! (work-resume "work-launching"))\n')
        self.assertIn("WORK_RESUMING work-launching", res2.stdout)
        t2 = _manifest_token(session)
        self.assertNotEqual(t1, t2)
        manifest2 = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest2["phase"], "reserved")

        # Launch command uses `>>` so run.log is never truncated by a superseded launch
        deadline = time.monotonic() + 5.0
        while not self.launches.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.launches.exists())
        launches = [json.loads(line) for line in self.launches.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any("METTACLAW_CODING_RUN_TOKEN=" + t2 in " ".join(cmd) for cmd in launches))

    def test_legacy_pin_without_phase_displays_predates_run_tokens_and_refuses_plain_resume(self):
        """Pins frozen before this commit lack `"phase"` in `runtime.json`.
        Classifier reports `("needs-current", "its runtime predates run tokens")`, and plain
        `(work-resume "id")` refuses and offers `(work-resume "id" "current")`.
        """
        session = self.make_session("work-pretoken", status="stopped", owner="500:1")
        coding_io.freeze_runtime(ROOT, session)
        manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        for key in ("phase", "run", "checkpoint", "previous", "at"):
            manifest.pop(key, None)
        (session / "runtime.json").write_text(json.dumps(manifest), encoding="utf-8")

        self.assertEqual(
            coding_estate.classify(session),
            ("needs-current", "its runtime predates run tokens"),
        )
        res = self.run_work_probe('!(println! (work-resume "work-pretoken"))\n')
        self.assertIn(
            'work-resume: its runtime predates run tokens; this session cannot resume on its pin. '
            'Move it to the current runtime with (work-resume \\"work-pretoken\\" \\"current\\"), '
            'start a new session with (work \\"...\\"), or set it aside with (work-archive \\"work-pretoken\\" \\"why\\").',
            res.stdout,
        )

    def test_repin_replaces_manifest_keeps_one_previous_preserves_executions_and_logs_run(self):
        """`CodingSessionRepin.repin`, `repin_keeps_executions`, `repin_keeps_uncertain`, `repin_keeps_previous`:
        Re-pinning twice keeps only one level of `previous` (no chain), preserves `executions/*.json`
        so `dispatching`/`running` still read as `uncertain`, appends `{at, from, to, run}` to `runs.jsonl`,
        writes the re-pin inbox note, and displays `run N` and `re-pinned <date>`.
        """
        session = self.make_session("work-repin", status="stopped", owner="600:1")
        (session / "messages.jsonl").write_text(
            json.dumps({"role": "system", "content": "sys"}) + "\n"
            + json.dumps({"role": "user", "content": "Inspect workspace"}) + "\n",
            encoding="utf-8",
        )
        (session / "plan.json").write_text(
            json.dumps({"plan": [{"step": "step 1", "status": "completed"}]}),
            encoding="utf-8",
        )
        # Create an inflight execution record ("running") and a finished one ("exited")
        exec_running = "a" * 32
        exec_exited = "b" * 32
        coding_io.execution_create(session, exec_running, "sleep 10")
        (session / "executions" / (exec_running + ".log")).write_text("partial output\n", encoding="utf-8")
        coding_io.execution_create(session, exec_exited, "echo ok")
        (session / "executions" / (exec_exited + ".log")).write_text("ok\n", encoding="utf-8")
        coding_io.execution_finish(session, exec_exited, "exited", 0, 0.1)

        # Pin 1: old retired bundle with its own previous to test 1-level stripping
        old_bundle = self.directory / "memory" / "coding-runtimes" / ("1111" * 16)
        (old_bundle / "src").mkdir(parents=True)
        (old_bundle / "coding_main.metta").write_text("; old\n", encoding="utf-8")
        (session / "runtime.json").write_text(
            json.dumps(
                {
                    "root": str(old_bundle),
                    "digest": "1111" * 16,
                    "binary": "/nonexistent/cetta-old",
                    "binary_sha256": "2222" * 16,
                    "previous": {"root": "/ancient", "digest": "0000" * 16},
                }
            ),
            encoding="utf-8",
        )

        exec_running_before = (session / "executions" / (exec_running + ".json")).read_bytes()
        exec_exited_before = (session / "executions" / (exec_exited + ".json")).read_bytes()

        res = self.run_work_probe(
            '!(println! (work-resume "work-repin" "current"))\n'
            '!(println! (work-status "work-repin"))\n'
        )
        self.assertIn("WORK_RESUMING work-repin", res.stdout)

        manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("phase"), "reserved")
        self.assertEqual(manifest.get("checkpoint"), coding_io.checkpoint_revision(session))
        self.assertIsInstance(manifest.get("previous"), dict)
        self.assertEqual(manifest["previous"]["digest"], "1111" * 16)
        # Only one level of previous is kept (no nested "previous")
        self.assertNotIn("previous", manifest["previous"])

        # executions/ files are byte-for-byte untouched and uncertain stays uncertain
        self.assertEqual((session / "executions" / (exec_running + ".json")).read_bytes(), exec_running_before)
        self.assertEqual((session / "executions" / (exec_exited + ".json")).read_bytes(), exec_exited_before)
        self.assertIn(
            "uncertain: this runtime does not own the execution; it was not replayed",
            coding_io.execution_result(session, exec_running),
        )

        # runs.jsonl has 1 entry {at, from, to, run}
        runs = coding_estate.read_runs(session)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["from"], "1111" * 16)
        self.assertEqual(runs[0]["to"], manifest["digest"])
        self.assertEqual(runs[0]["run"], manifest["run"])

        # Inbox note records the re-pin
        inbox_lines = [json.loads(line) for line in (session / "inbox.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn(
            "Resume this session from its existing evidence and plan. It now runs on a newer runtime than its last run. Verify uncertain effects before choosing new actions.",
            inbox_lines,
        )

        # View and work-status show "run 2" and "re-pinned <date>"
        self.assertIn("run 2", res.stdout)
        self.assertRegex(res.stdout, r"re-pinned \d{4}-\d{2}-\d{2}")
        view = coding_estate.context_view(self.directory)
        self.assertIn("run 2", view)
        self.assertRegex(view, r"re-pinned \d{4}-\d{2}-\d{2}")

        # No coding-checks directory left behind
        checks_dir = self.directory / "memory" / "coding-checks"
        if checks_dir.exists():
            self.assertEqual(list(checks_dir.iterdir()), [])

    def test_repin_failed_check_leaves_manifest_unchanged_and_deletes_copy(self):
        """`CodingSessionRepin.failed_check_changes_nothing`:
        When the checkpoint has corrupted `guidance.json` (or unreadable messages) so `coding_check.metta`
        prints `CheckpointFailed`, `(work-resume "id" "current")` refuses, leaves `runtime.json`
        byte-for-byte unchanged, writes no `runs.jsonl`, and deletes the check copy in `memory/coding-checks/`.
        """
        session = self.make_session("work-bad-check", status="stopped", owner="700:1")
        (session / "messages.jsonl").write_text(
            json.dumps({"role": "user", "content": "hi", "_coding_guidance": "bad-digest"}) + "\n",
            encoding="utf-8",
        )
        (session / "guidance.json").write_text('{"version": 1, "payload": {}, "sha256": "wrong"}', encoding="utf-8")
        old_manifest = {
            "root": "/missing/old-bundle",
            "digest": "3333" * 16,
            "binary": "/missing/cetta",
            "binary_sha256": "4444" * 16,
        }
        (session / "runtime.json").write_text(json.dumps(old_manifest), encoding="utf-8")
        before_runtime = (session / "runtime.json").read_bytes()

        res = self.run_work_probe('!(println! (work-resume "work-bad-check" "current"))\n')
        self.assertIn(
            "work-resume: the current runtime cannot read this session's checkpoint:",
            res.stdout,
        )
        self.assertIn(
            '; its pin is unchanged. Start a new session for the task with (work \\"...\\") '
            'or set it aside with (work-archive \\"work-bad-check\\" \\"why\\").',
            res.stdout,
        )
        self.assertEqual((session / "runtime.json").read_bytes(), before_runtime)
        self.assertFalse((session / "runs.jsonl").exists())
        checks_dir = self.directory / "memory" / "coding-checks"
        if checks_dir.exists():
            self.assertEqual(list(checks_dir.iterdir()), [])

    def test_repin_crash_before_rename_leaves_manifest_unchanged(self):
        """`CodingSessionRepin.crash_reads_old_or_checked`:
        A crash after the check passes but before `os.replace` on `runtime.json` leaves `runtime.json`
        and `runs.jsonl` unchanged.
        """
        session = self.make_session("work-crash-repin", status="stopped", owner="750:1")
        old_manifest = {
            "root": "/missing/old-bundle",
            "digest": "5555" * 16,
            "binary": "/missing/cetta",
            "binary_sha256": "6666" * 16,
        }
        (session / "runtime.json").write_text(json.dumps(old_manifest), encoding="utf-8")
        before_runtime = (session / "runtime.json").read_bytes()

        def _simulate_crash(_dir):
            raise RuntimeError("simulated crash before manifest rename")

        with self.assertRaisesRegex(RuntimeError, "simulated crash before manifest rename"):
            coding_io.repin_session(ROOT, session, before_rename_hook=_simulate_crash)

        self.assertEqual((session / "runtime.json").read_bytes(), before_runtime)
        self.assertFalse((session / "runs.jsonl").exists())

    def test_repin_checkpoint_changed_during_check_refuses_while_inbox_append_succeeds(self):
        """`CodingSessionRepin.rechecked_pairs_current` & `rechecked_keeps_inbox`:
        1. Modifying `messages.jsonl` (or `plan.json`) while the check runs outside `.ending.lock`
           causes the recheck under `.ending.lock` to refuse (`work-resume: the session changed during the check; try again.`),
           leaving `runtime.json` unchanged.
        2. Appending to `inbox.jsonl` while the check runs succeeds because `inbox.jsonl` is outside
           the checkpoint revision, and the appended inbox line is preserved.
        """
        session = self.make_session("work-race-repin", status="stopped", owner="800:1")
        (session / "messages.jsonl").write_text(
            json.dumps({"role": "system", "content": "sys"}) + "\n"
            + json.dumps({"role": "user", "content": "Initial task"}) + "\n",
            encoding="utf-8",
        )
        old_manifest = {
            "root": "/missing/old-bundle",
            "digest": "7777" * 16,
            "binary": "/missing/cetta",
            "binary_sha256": "8888" * 16,
        }
        (session / "runtime.json").write_text(json.dumps(old_manifest), encoding="utf-8")
        before_runtime = (session / "runtime.json").read_bytes()

        # 1. Mutate messages.jsonl during the check -> refused
        def _mutate_checkpoint(target_dir):
            with open(Path(target_dir) / "messages.jsonl", "a", encoding="utf-8") as stream:
                stream.write(json.dumps({"role": "user", "content": "sneaky edit"}) + "\n")

        status, detail = coding_io.repin_session(ROOT, session, check_hook=_mutate_checkpoint)
        self.assertEqual(status, "changed")
        self.assertEqual(detail, "the session changed during the check; try again.")
        self.assertEqual((session / "runtime.json").read_bytes(), before_runtime)
        self.assertFalse((session / "runs.jsonl").exists())

        # 2. Append to inbox.jsonl during the check -> succeeds and keeps inbox
        def _append_inbox(target_dir):
            coding_io.append_line(Path(target_dir) / "inbox.jsonl", json.dumps("concurrent steering"))

        status2, token2 = coding_io.repin_session(ROOT, session, check_hook=_append_inbox)
        self.assertEqual(status2, "repinned")
        self.assertTrue(token2)
        inbox_text = (session / "inbox.jsonl").read_text(encoding="utf-8")
        self.assertIn("concurrent steering", inbox_text)

    def test_check_only_boundary_makes_no_provider_or_ledger_calls_and_keeps_live_files_identical(self):
        """The check-only entry point (`coding_check.metta` under `METTACLAW_CODING_CHECK=1`):
        - Repairs an inflight tool call in the copy (writing into the copy only);
        - Keeps every file in the live session directory byte-for-byte identical before and after;
        - Makes zero provider HTTP calls and creates no ledger files;
        - Refuses direct calls to provider and tool entry points when `METTACLAW_CODING_CHECK=1`.
        """
        session = self.make_session("work-check-boundary", status="stopped", owner="900:1")
        (session / "messages.jsonl").write_text(
            json.dumps({"role": "system", "content": "sys"}) + "\n"
            + json.dumps({"role": "user", "content": "Run tool"}) + "\n"
            + json.dumps(
                {
                    "role": "assistant",
                    "content": "Calling tool",
                    "tool_calls": [
                        {
                            "id": "call_inflight_1",
                            "type": "function",
                            "function": {"name": "exec_command", "arguments": '{"cmd":"echo hi"}'},
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (session / "run.log").write_text("live run log\n", encoding="utf-8")
        (session / "tmp").mkdir(exist_ok=True)
        (session / "tmp" / "scratch.txt").write_text("scratch\n", encoding="utf-8")
        exec_id = "c" * 32
        coding_io.execution_create(session, exec_id, "echo inflight")
        (session / "executions" / (exec_id + ".log")).write_text("big execution log\n", encoding="utf-8")

        def _snapshot_tree(root_path):
            snap = {}
            for p in sorted(root_path.rglob("*")):
                rel = str(p.relative_to(root_path))
                if p.is_file():
                    snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            return snap

        before_snap = _snapshot_tree(session)
        self.server.requests.clear()

        target_bundle, _ = coding_io.freeze_bundle(ROOT, self.directory / "memory" / "coding-runtimes")
        ok, detail = coding_io.check_checkpoint_copy(session, target_bundle)
        self.assertTrue(ok, detail)
        self.assertIn("CheckpointReady 4", detail)

        # Live session files are byte-for-byte identical (the repaired 4th message was written only in the copy)
        self.assertEqual(_snapshot_tree(session), before_snap)
        self.assertEqual(len(self.server.requests), 0)
        self.assertFalse((self.directory / "ledger.db").exists())

        # Direct provider and tool entry points refuse under METTACLAW_CODING_CHECK=1
        # and behave normally when the flag is absent (kills Survivor 6).
        with mock.patch.dict(os.environ, {**self.env, "METTACLAW_CODING_CHECK": "1"}):
            with self.assertRaisesRegex(RuntimeError, "METTACLAW_CODING_CHECK"):
                native_llm_host.gate()
            with self.assertRaisesRegex(RuntimeError, "METTACLAW_CODING_CHECK"):
                native_llm_host.for_coding(types.SimpleNamespace())
            with self.assertRaisesRegex(RuntimeError, "METTACLAW_CODING_CHECK"):
                native_llm_host.for_compaction(types.SimpleNamespace())
            with self.assertRaisesRegex(RuntimeError, "METTACLAW_CODING_CHECK"):
                apply_patch.apply("*** Begin Patch\n*** Add File: x.txt\n+hi\n*** End Patch", self.directory)
            with self.assertRaisesRegex(RuntimeError, "METTACLAW_CODING_CHECK"):
                coding_io.execution_create(session, "d" * 32, "echo forbidden")

        with mock.patch.dict(os.environ, {k: v for k, v in self.env.items() if k != "METTACLAW_CODING_CHECK"}, clear=True):
            self.assertTrue(native_llm_host.gate())
            plan_coding = types.SimpleNamespace()
            self.assertIs(native_llm_host.for_coding(plan_coding), plan_coding)
            self.assertEqual(plan_coding.purpose, "coding")
            plan_compact = types.SimpleNamespace()
            self.assertIs(native_llm_host.for_compaction(plan_compact), plan_compact)
            self.assertEqual(plan_compact.purpose, "compaction")

    def test_torn_runs_jsonl_tail_ignored_and_resume_setting_current(self):
        """A torn last line in `runs.jsonl` is ignored by readers, and
        `(setting "work" "resume" "current")` makes plain `(work-resume "id")` re-pin automatically.
        """
        session = self.make_session("work-torn-and-setting", status="stopped", owner="950:1")
        old_bundle = self.directory / "memory" / "coding-runtimes" / ("9999" * 16)
        (old_bundle / "src").mkdir(parents=True)
        (old_bundle / "coding_main.metta").write_text("; old\n", encoding="utf-8")
        (session / "runtime.json").write_text(
            json.dumps(
                {
                    "root": str(old_bundle),
                    "digest": "9999" * 16,
                    "binary": "/missing/cetta",
                    "binary_sha256": "aaaa" * 16,
                }
            ),
            encoding="utf-8",
        )
        (session / "runs.jsonl").write_text(
            json.dumps({"at": 1761200000.0, "from": "0000" * 16, "to": "9999" * 16, "run": "r1"})
            + "\n"
            + '{"at": 1761200100.0, "from": "torn-line-without-closing-brace',
            encoding="utf-8",
        )
        runs = coding_estate.read_runs(session)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run"], "r1")

        # Configure (setting "work" "resume" "current") and invoke 1-arg (work-resume "id")
        res = self.run_work_probe(
            '!(println! (setting "work" "resume"))\n'
            '!(println! (setting "work" "resume" "current"))\n'
            '!(println! (setting "work" "resume"))\n'
            '!(println! (work-resume "work-torn-and-setting"))\n'
        )
        self.assertIn("pinned", res.stdout)
        self.assertIn('(WorkConfigured "resume" "current"', res.stdout)
        self.assertIn("WORK_RESUMING work-torn-and-setting", res.stdout)

        runs_after = coding_estate.read_runs(session)
        self.assertEqual(len(runs_after), 2)

    def test_world_selection_map_supports_multiple_retirements(self):
        """`World.selection` is a map from retirement name to outcome, so adding a second entry
        in `RETIREMENTS` requires no change to `World` or `classify`.
        """
        session = self.make_session("work-multi-retire", status="stopped", owner="960:1")
        coding_io.freeze_runtime(ROOT, session)

        extra_retirement = coding_estate.Retirement(
            name="second-service",
            text="the retired second service",
            live_fact=lambda: "native",
            bundle_test=lambda bundle: True,
        )
        with mock.patch.object(
            coding_estate, "RETIREMENTS", coding_estate.RETIREMENTS + (extra_retirement,)
        ):
            w_retired = coding_estate.World(
                selection={"llm-ledger-sqlite": "legacy", "second-service": "native"}
            )
            self.assertEqual(
                coding_estate.classify(session, world=w_retired),
                ("needs-current", "it uses the retired second service"),
            )
            w_ok = coding_estate.World(
                selection={"llm-ledger-sqlite": "legacy", "second-service": "legacy"}
            )
            self.assertEqual(coding_estate.classify(session, world=w_ok), ("resumable", ""))

    def test_repin_manifest_changed_during_check_refuses(self):
        """Re-pin publication must recheck `(run, phase)` in `runtime.json` under `.ending.lock`,
        not only `checkpoint_revision`:
        1. If another resume calls `freeze_runtime` during `check_checkpoint_copy` (without changing
           any checkpoint file), `repin_session` returns `("changed", ...)` and preserves the other
           resume's token and `"reserved"` phase.
        2. If a worker calls `consume_run` on a reserved token during `check_checkpoint_copy`,
           `repin_session` returns `("changed", ...)` and preserves the live run's token and `"started"` phase.
        """
        old_bundle = self.directory / "memory" / "coding-runtimes" / ("7777" * 16)
        (old_bundle / "src").mkdir(parents=True)
        (old_bundle / "coding_main.metta").write_text("; old\n", encoding="utf-8")

        # Case 1: concurrent pinned resume reserves a token during the check
        s_a = self.make_session("work-repin-manifest-race-a", status="stopped", owner="810:1")
        (s_a / "runtime.json").write_text(
            json.dumps(
                {
                    "root": str(old_bundle),
                    "digest": "7777" * 16,
                    "binary": "/missing/cetta",
                    "binary_sha256": "8888" * 16,
                    "run": "tok-initial-a",
                    "phase": "ended",
                }
            ),
            encoding="utf-8",
        )
        concurrent_tokens = []

        def _concurrent_pinned_resume(target_dir):
            coding_io.freeze_runtime(ROOT, target_dir)
            concurrent_tokens.append(_manifest_token(target_dir))

        status_a, detail_a = coding_io.repin_session(ROOT, s_a, check_hook=_concurrent_pinned_resume)
        self.assertEqual(status_a, "changed")
        self.assertEqual(detail_a, "the session changed during the check; try again.")
        manifest_a = json.loads((s_a / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_a["run"], concurrent_tokens[0])
        self.assertEqual(manifest_a["phase"], "reserved")
        self.assertEqual(manifest_a["digest"], "7777" * 16)
        self.assertFalse((s_a / "runs.jsonl").exists())

        # Case 2: worker consumes a reserved token during the check
        s_b = self.make_session("work-repin-manifest-race-b", status="stopped", owner="820:1")
        (s_b / "runtime.json").write_text(
            json.dumps(
                {
                    "root": str(old_bundle),
                    "digest": "7777" * 16,
                    "binary": "/missing/cetta",
                    "binary_sha256": "8888" * 16,
                    "run": "tok-reserved-b",
                    "phase": "reserved",
                }
            ),
            encoding="utf-8",
        )

        def _worker_consumes_token(target_dir):
            self.assertEqual(coding_io.consume_run(target_dir, "tok-reserved-b", "820:2"), 1)

        status_b, detail_b = coding_io.repin_session(ROOT, s_b, check_hook=_worker_consumes_token)
        self.assertEqual(status_b, "changed")
        self.assertEqual(detail_b, "the session changed during the check; try again.")
        manifest_b = json.loads((s_b / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_b["run"], "tok-reserved-b")
        self.assertEqual(manifest_b["phase"], "started")
        self.assertFalse((s_b / "runs.jsonl").exists())

    def test_single_ending_implementation_shared_by_reap_stop_and_resume(self):
        """One implementation of the interrupted/stopped ending lives in `src/coding_io.py`
        (`settle_ending`), called by `work:reap-one`, `work:ended`, `freeze_runtime`, and `repin_session`.
        """
        metta_source = (ROOT / "src" / "coding_work.metta").read_text(encoding="utf-8")
        self.assertNotIn("Last progress:", metta_source)
        self.assertNotIn("Resume without replaying uncertain effects:", metta_source)
        self.assertTrue(hasattr(coding_io, "settle_ending"))

    def test_work_stop_revokes_reserved_launch_without_recording_ending(self):
        """Calling `(work-stop "id")` on a session whose manifest `phase` is `"reserved"` sets
        `phase = "ended"` and writes `stop` under `.ending.lock`, leaves non-running `state.json`
        unchanged, causes a subsequent `consume_run` with the reserved token to return `0`,
        and removes `launching` from `context_view` and `work-status`.
        """
        session = self.make_session("work-stop-reserved", status="stopped", owner="830:1")
        coding_io.freeze_runtime(ROOT, session)
        token = _manifest_token(session)
        before_state = (session / "state.json").read_bytes()

        world = coding_estate.World(today="2026-11-06")
        self.assertIn("1 launching", coding_estate.context_view(self.directory, world=world))

        res = self.run_work_probe(
            '!(println! (work-stop "work-stop-reserved"))\n'
            '!(println! (work-status "work-stop-reserved"))\n'
        )
        self.assertIn("WORK_STOPPED work-stop-reserved", res.stdout)
        self.assertTrue((session / "stop").is_file())
        self.assertEqual((session / "state.json").read_bytes(), before_state)
        self.assertFalse((session / "report.txt").exists())

        manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["run"], token)
        self.assertEqual(manifest["phase"], "ended")

        # Delayed launcher attempting to consume the revoked token is refused
        self.assertEqual(coding_io.consume_run(session, token, "830:2"), 0)
        self.assertEqual((session / "state.json").read_bytes(), before_state)

        # Neither context_view nor work-status shows "launching" anymore
        view_after = coding_estate.context_view(self.directory, world=world)
        self.assertNotIn("launching", view_after)
        self.assertNotIn("launching", res.stdout)


if __name__ == "__main__":
    unittest.main()

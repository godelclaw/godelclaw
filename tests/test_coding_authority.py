"""Coding sessions through the agent's verbs, where the estate views are involved.

The authority protocol itself (run tokens, endings, reservations, re-pinning,
checkpoint checks and durability faults) is tested natively in
scripts/test_coding_authority_cetta.metta. What stays here runs the agent's
verbs (coding_work.metta) through MeTTa probes; the estate verdicts are
tested natively in scripts/test_coding_estate_cetta.metta:
- `test_launching_reservation_displayed_and_superseded_by_next_resume`:
  `CodingSessionAuthority.reserve`, `fixed_reservation_is_newest`; a reserved, unconsumed
  manifest shows `launching since <date>`.
- `test_legacy_pin_without_phase_displays_predates_run_tokens_and_refuses_plain_resume`:
  pre-token bundles lack `"phase"` and require a `"current"` re-pin.
- `test_torn_runs_jsonl_tail_ignored_and_resume_setting_current`:
  `runs.jsonl` torn-tail resilience and `(setting "work" "resume" "current")`.
- `test_delayed_launch_keeps_own_descriptor_and_worker_checks_bundle_binding`:
  `CodingSessionLaunch.delayedLaunch`, `fixed_launch_keeps_own_token`, `fixed_live_authority_is_bound`.
"""

import errno
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import stat
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
import native_ledger  # noqa: E402
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
        self.socket = native_ledger.start(self, self.directory)

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
            "METTACLAW_INPUT_LEDGER_SOCKET": self.socket,
            "METTACLAW_STRATEGY_PATH": str(self.directory / "strategy"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_LLM_COMPLETION_PATH": str(self.directory / "completion.json"),
            "METTACLAW_PROVIDER_OBSERVATION_PATH": str(self.directory / "providers.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_WAKE_REQUEST_PATH": str(self.directory / "wake"),
            "METTACLAW_CODING_SESSION": "1",
            "METTACLAW_RUNTIME_ROOT": str(ROOT),
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

    def freeze(self, session, root=ROOT):
        """Reserve a launch with the native freeze, as (work ...) and (work-resume ...) do."""
        result = self.run_work_probe('!(println! (cio:freeze-runtime %s %s NoNote))\n'
                                     % (json.dumps(str(root)), json.dumps(str(session))))
        self.assertIn("(CodingLaunch ", result.stdout)
        return result.stdout

    def frozen_bundle(self, root, cache):
        result = self.run_work_probe('!(println! (cio:freeze-bundle %s %s))\n' % (json.dumps(str(root)), json.dumps(str(cache))))
        return Path(json.loads(result.stdout.split("(FrozenBundle ", 1)[1].split(" ", 1)[0]))

    def run_worker(self, session, token, *, cwd=None):
        worker_cwd = Path(cwd) if cwd is not None else Path(
            json.loads((Path(session) / "runtime.json").read_text(encoding="utf-8")).get("root") or ROOT)
        if not worker_cwd.is_dir():
            worker_cwd = ROOT
        main_metta = worker_cwd / "coding_main.metta"
        if not main_metta.is_file():
            main_metta = ROOT / "coding_main.metta"
        env = {**self.env, "METTACLAW_CODING_RUN_TOKEN": str(token)}
        with open(session / "run.log", "a", encoding="utf-8") as log_stream:
            result = subprocess.run(
                [
                    os.environ["CETTA_BIN"],
                    "--lang",
                    "petta",
                    "--import-mode",
                    "ancestor-walk",
                    str(main_metta),
                    str(session),
                ],
                cwd=worker_cwd,
                env=env,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
            )
        self.assertEqual(result.returncode, 0)
        return result

    def test_launching_reservation_displayed_and_superseded_by_next_resume(self):
        """A reserved, unconsumed manifest (`phase == "reserved"`, scope inactive) displays
        `launching` / `launching since <date>` in `context_view` and `work-status`, and a second
        resume supersedes it with a fresh token and appends to `run.log` (`>>`).
        """
        session = self.make_session("work-launching", status="stopped", owner="400:1")
        self.freeze(session)
        t1 = _manifest_token(session)
        manifest1 = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest1.get("phase"), "reserved")

        view = self.run_work_probe('!(println! (estate:context-view-root %s "2026-11-06"))\n'
                                   % json.dumps(str(self.directory))).stdout
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
        self.freeze(session)
        manifest = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        for key in ("phase", "run", "checkpoint", "previous", "at"):
            manifest.pop(key, None)
        (session / "runtime.json").write_text(json.dumps(manifest), encoding="utf-8")

        res = self.run_work_probe('!(println! (work-resume "work-pretoken"))\n')
        self.assertIn(
            'work-resume: its runtime predates run tokens; this session cannot resume on its pin. '
            'Move it to the current runtime with (work-resume \\"work-pretoken\\" \\"current\\"), '
            'start a new session with (work \\"...\\"), or set it aside with (work-archive \\"work-pretoken\\" \\"why\\").',
            res.stdout,
        )

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

        # The torn line is dropped; the re-pin's record follows the earlier one.
        runs_after = [json.loads(line) for line in (session / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["run"] for r in runs_after][:1], ["r1"])
        self.assertEqual(len(runs_after), 2)


    def test_delayed_launch_keeps_own_descriptor_and_worker_checks_bundle_binding(self):
        """`CodingSessionLaunch.delayedLaunch`, `current_launch_takes_other_token`,
        `fixed_launch_keeps_own_token`, `fixed_live_authority_is_bound` (P1-1):
        1. Create two distinct runtime sources `src_a` and `src_b` with different frozen bundle digests.
        2. In MeTTa, `freeze_runtime` reserves run A (`desc_a`, `token_a`, `bundle_a`); before
           `work:launch-at` runs, a concurrent `repin_session` reserves run B (`token_b`, `bundle_b`).
        3. Delayed `work:launch-at` for A must launch `bundle_a` with `token_a` (never `token_b`),
           so worker A exits as superseded while `token_b` remains `"reserved"` for worker B.
        4. Even if a mis-assembled launcher invokes `coding_main.metta` from `bundle_a` with `token_b`,
           `consume_run` checks that `manifest["root"]` matches the worker's bundle root and refuses (`0`).
        5. Resume file preparation (`stop`, `recycle.requested`, `report.txt`, `clear_lifecycle`,
           `inbox.jsonl` note) happens inside `.ending.lock` during reservation, never after releasing it.
        """
        session = self.make_session("work-launch-race", status="stopped", owner="840:1")
        (session / "messages.jsonl").write_text(
            json.dumps({"role": "system", "content": "sys"}) + "\n"
            + json.dumps({"role": "user", "content": "Complete task"}) + "\n",
            encoding="utf-8",
        )

        def _make_runtime_source(name, marker):
            src = self.directory / name
            src.mkdir(parents=True, exist_ok=True)
            manifest_rel = Path("config/runtime-files.json")
            names = json.loads((ROOT / manifest_rel).read_text(encoding="utf-8"))
            if isinstance(names, dict):
                names = names["files"]
            for rel in [str(manifest_rel), *names]:
                target = ROOT / rel
                dest = src / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dest)
            with open(src / "coding_main.metta", "a", encoding="utf-8") as stream:
                stream.write(f"\n; bundle-marker: {marker}\n")
            return src

        src_a = _make_runtime_source("runtime_src_a", "bundle-A")
        src_b = _make_runtime_source("runtime_src_b", "bundle-B")

        self.launches.unlink(missing_ok=True)
        # Execute the exact MeTTa race: freeze A, then repin to B before work:launch-at runs with A's return value
        probe_forms = (
            f'!(let $desc_a (cio:freeze-runtime "{src_a}" "{session}" NoNote)\n'
            f'   (let $_ (cio:repin "{src_b}" "{session}")\n'
            f'     (println! (work:launch-at "work-launch-race" $desc_a))))\n'
        )
        self.run_work_probe(probe_forms)
        for _ in range(100):
            if self.launches.is_file() and self.launches.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)

        manifest_b = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        token_b = manifest_b["run"]
        bundle_b = manifest_b["root"]

        launches = [
            json.loads(line)
            for line in self.launches.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(launches), 1)
        launched_cmd = " ".join(launches[0])
        # Delayed launch A must NOT carry B's run token!
        self.assertNotIn(f"METTACLAW_CODING_RUN_TOKEN={token_b}", launched_cmd)

        # Extract token_a and bundle_a from the launched command and verify worker A exits without consuming B
        bundle_a = self.frozen_bundle(src_a, self.directory / "memory" / "coding-runtimes")
        self.assertNotEqual(str(bundle_a), str(bundle_b))
        self.assertIn(str(bundle_a), launched_cmd)
        import re as _re
        m_tok = _re.search(r"METTACLAW_CODING_RUN_TOKEN=([0-9a-f]+)", launched_cmd)
        self.assertIsNotNone(m_tok)
        token_a = m_tok.group(1)
        self.assertNotEqual(token_a, token_b)

        # Running delayed worker A with its own (bundle_a, token_a) exits cleanly and leaves B reserved
        self.run_worker(session, token_a, cwd=bundle_a)
        manifest_after_a = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_after_a["run"], token_b)
        self.assertEqual(manifest_after_a["phase"], "reserved")

        # Even a mis-assembled worker running from bundle_a with token_b is refused by consume_run's bundle check
        self.run_worker(session, token_b, cwd=bundle_a)
        manifest_after_misassembled = json.loads((session / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_after_misassembled["run"], token_b)
        self.assertEqual(manifest_after_misassembled["phase"], "reserved")

        # Worker B running from bundle_b with token_b succeeds and completes the session
        self.server.replies.append(
            {
                "id": "r-b",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Completed by bundle B."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        )
        self.run_worker(session, token_b, cwd=bundle_b)
        final_state = json.loads((session / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(final_state["status"], "done")
        self.assertIn("Completed by bundle B.", (session / "report.txt").read_text(encoding="utf-8"))

        # Single delivery:preserve implementation: with a pending ending, two resumes (one of them
        # superseded) leave exactly one outbox message for that ending, byte-identical to what
        # (delivery:preserve $dir) alone writes.
        outbox_dir = session / "report-outbox"
        shutil.rmtree(outbox_dir, ignore_errors=True)
        self.run_work_probe(f'!(println! (delivery:preserve "{session}"))\n')
        expected_files = sorted(outbox_dir.glob("*.json"))
        self.assertEqual(len(expected_files), 1)
        expected_name = expected_files[0].name
        expected_bytes = expected_files[0].read_bytes()
        shutil.rmtree(outbox_dir)

        self.run_work_probe(
            '!(println! (work-resume "work-launch-race"))\n'
            '!(println! (work-resume "work-launch-race" "current"))\n'
        )
        actual_files = sorted(outbox_dir.glob("*.json"))
        self.assertEqual([p.name for p in actual_files], [expected_name])
        self.assertEqual(actual_files[0].read_bytes(), expected_bytes)






if __name__ == "__main__":
    unittest.main()

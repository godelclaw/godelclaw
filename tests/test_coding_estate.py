"""The coding-session verbs on the native coding worker: refusals, one ledger
query per listing, hold and archive labels, and the coding-sessions source.
Every verdict and view line is covered on the core engine by
scripts/test_coding_estate_cetta.metta."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _label(session, kind, why, review=None, at=None):
    """A hold or archive label as (work-hold) and (work-archive) record it."""
    payload = {"why": why, "at": time.time() if at is None else at}
    if kind == "hold":
        payload["review"] = review or None
    (Path(session) / "lifecycle.json").write_text(json.dumps({kind: payload}, sort_keys=True) + "\n", encoding="utf-8")


class CodingEstateFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

        self.engine_bytes = b"#!/bin/sh\nexit 0\n"
        self.engine_hash = _sha256_bytes(self.engine_bytes)
        self.engine = self.directory / "cetta-engine"
        self.engine.write_bytes(self.engine_bytes)
        self.engine.chmod(0o755)

        self.runtimes = self.directory / "memory" / "coding-runtimes"
        self.runtimes.mkdir(parents=True)
        self.coding = self.directory / "memory" / "coding"
        self.coding.mkdir(parents=True)

        # Current bundle (has src/llm_ledger.metta)
        self.bundle_current_digest = "aaaa1111" + "0" * 56
        self.bundle_current = self.runtimes / self.bundle_current_digest
        (self.bundle_current / "src").mkdir(parents=True)
        (self.bundle_current / "coding_main.metta").write_text("; main\n", encoding="utf-8")
        (self.bundle_current / "src" / "llm_ledger.metta").write_text("; native ledger\n", encoding="utf-8")

        # Old pre-0417b092e bundle (lacks src/llm_ledger.metta)
        self.bundle_old_digest = "8acffdbd" + "0" * 56
        self.bundle_old = self.runtimes / self.bundle_old_digest
        (self.bundle_old / "src").mkdir(parents=True)
        (self.bundle_old / "coding_main.metta").write_text("; old main\n", encoding="utf-8")

        # Unused bundle in coding-runtimes
        self.bundle_unused_digest = "cccc3333" + "0" * 56
        self.bundle_unused = self.runtimes / self.bundle_unused_digest
        (self.bundle_unused / "src").mkdir(parents=True)
        (self.bundle_unused / "coding_main.metta").write_text("; unused\n", encoding="utf-8")

        # Fixed timestamp 2026-10-23 12:00:00 local time
        self.fixed_ts = time.mktime(time.strptime("2026-10-23 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.pin_ts = time.mktime(time.strptime("2026-10-22 09:00:00", "%Y-%m-%d %H:%M:%S"))

    def make_session(
        self,
        name,
        *,
        status="stopped",
        steps=3,
        updated=None,
        pin=True,
        bundle=None,
        digest=None,
        binary="default",
        binary_sha256="default",
        phase="ended",
        pin_mtime=None,
        write_state=True,
    ):
        session = self.coding / name
        session.mkdir(parents=True, exist_ok=True)
        task_path = session / "task.json"
        task_path.write_text(
            json.dumps({"task": "Task for %s" % name, "workdir": str(self.directory),
                        "model": "fixture-model", "effort": "high"}),
            encoding="utf-8",
        )
        ts = self.fixed_ts if updated is None else updated
        os.utime(task_path, (ts, ts))
        if write_state:
            state = {
                "status": status,
                "steps": steps,
                "progress": "step %d" % steps,
                "inbox": 0,
                "updated": ts,
            }
            state_path = session / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            os.utime(state_path, (ts, ts))

        if pin:
            chosen_bundle = self.bundle_current if bundle is None else bundle
            chosen_digest = (
                self.bundle_current_digest
                if digest is None and chosen_bundle == self.bundle_current
                else (self.bundle_old_digest if digest is None and chosen_bundle == self.bundle_old else (digest or "deadbeef" * 8))
            )
            chosen_binary = str(self.engine) if binary == "default" else binary
            record = {
                "root": str(chosen_bundle),
                "digest": chosen_digest,
                "binary": chosen_binary,
            }
            if binary_sha256 == "default":
                record["binary_sha256"] = self.engine_hash
            elif binary_sha256 is not None:
                record["binary_sha256"] = binary_sha256
            if phase is not None:
                record["phase"] = phase
            rt_path = session / "runtime.json"
            rt_path.write_text(json.dumps(record), encoding="utf-8")
            pts = self.pin_ts if pin_mtime is None else pin_mtime
            os.utime(rt_path, (pts, pts))
        return session


class CodingEstateVerbTests(CodingEstateFixture):
    def setUp(self):
        super().setUp()
        self.env = {
            **isolated_environ(),
            "PYTHONHOME": sys.prefix,
            "LD_LIBRARY_PATH": str(Path(sys.prefix) / "lib"),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]),
            "METTACLAW_ACTIVE_ENGINE": "cetta",
            "METTACLAW_ENGINE": "cetta",
            "METTACLAW_LLM_TRANSPORT": "native",
            "METTACLAW_ENGINE_STATE_PATH": str(self.directory / "engine"),
            "METTACLAW_LLM_LEDGER_PATH": str(self.directory / "ledger.db"),
            "METTACLAW_COGNITIVE_HEALTH_PATH": str(self.directory / "health.json"),
            "METTACLAW_LLM_COMPLETION_PATH": str(self.directory / "completion.json"),
            "METTACLAW_PROVIDER_OBSERVATION_PATH": str(self.directory / "providers.json"),
            "METTACLAW_RECYCLE_REQUEST_PATH": str(self.directory / "recycle"),
            "METTACLAW_WAKE_REQUEST_PATH": str(self.directory / "wake"),
            "METTACLAW_LIFECYCLE_PATH": str(self.directory / "lifecycle.json"),
            "SYNTHETIC_MODEL": "fixture-model",
            "METTACLAW_MODEL_STATE_PATH": str(self.directory / "model"),
        }
        (self.directory / "lifecycle.json").write_text('{"schema":1,"state":"running"}', encoding="utf-8")
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
                '    if sys.argv[-1].endswith("-work-running-target.scope"):\n'
                '        sys.exit(0)\n'
                '    sys.exit(0 if os.path.exists(os.environ["FIXTURE_SCOPE_ACTIVE"]) else 3)\n'
                "sys.exit(0)\n"
            ),
            "systemd-run": (
                "import json, os, sys\n"
                "try:\n"
                '    fd = os.open(os.environ["FIXTURE_SCOPE_ACTIVE"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n'
                "except FileExistsError:\n"
                "    sys.exit(1)\n"
                "os.close(fd)\n"
                'with open(os.environ["FIXTURE_SCOPE_LAUNCHES"], "a") as stream:\n'
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

    def run_probe(self, forms, *, cwd=None, socket=None):
        probe = ROOT / ("estate_probe_%d.metta" % os.getpid())
        probe.write_text(
            "!(import! &self (library lib_import))\n"
            "!(import! &self ./src/coding_work.metta)\n" + forms,
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=self.directory if cwd is None else cwd,
                env=self.env if socket is None else {**self.env, "METTACLAW_INPUT_LEDGER_SOCKET": socket},
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            probe.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _counting_native_ledger(self, counter, outcome="native"):
        import socket as sockets
        import threading
        from test_input_ledger_remote import DbServer

        counter.write_text("", encoding="utf-8")
        if outcome == "unreachable":
            dead = self.directory / (counter.name + ".dead.sock")
            dead.write_text("", encoding="utf-8")
            return str(dead)

        if not hasattr(self, "_native_ledger_server"):
            ledger_root = self.directory / "native_ledger"
            ledger_root.mkdir(parents=True, exist_ok=True)
            server = DbServer(ledger_root)
            server.start()
            self.addCleanup(server.stop)
            self._native_ledger_server = server
            self._proxy_seq = 0

        self._proxy_seq += 1
        proxy_path = str(self.directory / ("ledger-proxy-%d.sock" % self._proxy_seq))
        listener = sockets.socket(sockets.AF_UNIX)
        listener.bind(proxy_path)
        listener.listen(16)
        upstream = str(self._native_ledger_server.socket)

        def close_quietly(*ends):
            for end in ends:
                try:
                    end.shutdown(sockets.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    end.close()
                except OSError:
                    pass

        def handle(client):
            if outcome == "timeout":
                buf = b""
                try:
                    data = client.recv(65536)
                    buf += data
                    if b"llm-selection" in buf:
                        with open(counter, "a", encoding="utf-8") as stream:
                            stream.write("llm-selection\n")
                    time.sleep(1.5)
                except OSError:
                    pass
                finally:
                    close_quietly(client)
                return

            server_conn = sockets.socket(sockets.AF_UNIX)
            try:
                server_conn.connect(upstream)
            except OSError:
                close_quietly(client, server_conn)
                return

            def back():
                try:
                    while reply := server_conn.recv(65536):
                        client.sendall(reply)
                except OSError:
                    pass
                finally:
                    close_quietly(client, server_conn)

            threading.Thread(target=back, daemon=True).start()
            recorded = False
            buf = b""
            try:
                while chunk := client.recv(65536):
                    buf += chunk
                    if not recorded and b"llm-selection" in buf:
                        recorded = True
                        with open(counter, "a", encoding="utf-8") as stream:
                            stream.write("llm-selection\n")
                    server_conn.sendall(chunk)
                try:
                    server_conn.shutdown(sockets.SHUT_WR)
                except OSError:
                    pass
            except OSError:
                close_quietly(client, server_conn)

        def accept_loop():
            while True:
                try:
                    client, _ = listener.accept()
                except OSError:
                    return
                threading.Thread(target=handle, args=(client,), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()
        self.addCleanup(close_quietly, listener)
        return proxy_path

    def native_view(self, output, today="2026-10-25", outcome="native"):
        """The context view through the native estate, with the ledger's queries counted."""
        counter = self.directory / (output.name + ".selection")
        socket_path = self._counting_native_ledger(counter, outcome=outcome)
        self.run_probe('!(import! &self ./src/coding_estate_cetta.metta)\n'
                       '!(let $v (estate:context-view-root "%s" "%s") (fs:write "%s" (if (== $v EstateMissing) "__MISSING__" $v)))\n'
                       % (self.directory, today, output), socket=socket_path)
        return output.read_text(encoding="utf-8"), counter

    def test_refused_work_resume_names_reason_and_launches_nothing(self):
        # 1. needs-current refusal (hash changed)
        s_changed = self.make_session("work-changed", status="stopped", binary_sha256="0" * 64)
        (s_changed / "run.log").write_text("original run log\n", encoding="utf-8")
        (s_changed / "inbox.jsonl").write_text('"prior guidance"\n', encoding="utf-8")
        before_state = (s_changed / "state.json").read_bytes()
        before_inbox = (s_changed / "inbox.jsonl").read_bytes()
        before_log = (s_changed / "run.log").read_bytes()

        # 2. retryable unverified refusal (a runtime from before the native
        #    LLM ledger while the ledger cannot be asked)
        s_unavailable = self.make_session("work-unavailable", status="stopped", bundle=self.bundle_old)
        (s_unavailable / "run.log").write_text("unavailable run log\n", encoding="utf-8")
        (s_unavailable / "inbox.jsonl").write_text('"prior unavailable"\n', encoding="utf-8")
        before_u_state = (s_unavailable / "state.json").read_bytes()
        before_u_inbox = (s_unavailable / "inbox.jsonl").read_bytes()
        before_u_log = (s_unavailable / "run.log").read_bytes()

        result = self.run_probe(
            '!(println! (work-resume "work-changed"))\n'
            '!(println! (work-resume "work-unavailable"))\n'
            '!(println! (work-status))\n'
        )
        self.assertIn(
            'work-resume: its engine binary has changed; this session cannot resume on its pin. '
            'Move it to the current runtime with (work-resume \\"work-changed\\" \\"current\\"), '
            'start a new session with (work \\"...\\"), or set it aside with (work-archive \\"work-changed\\" \\"why\\").',
            result.stdout,
        )
        self.assertIn(
            "work-resume: the LLM ledger's backend selection is unavailable (unreachable); "
            "its pin cannot be checked now. Try again later.",
            result.stdout,
        )
        self.assertIn("unverified: the LLM ledger's backend selection is unavailable (unreachable)", result.stdout)
        self.assertFalse(self.launches.exists())
        self.assertEqual((s_changed / "state.json").read_bytes(), before_state)
        self.assertEqual((s_changed / "inbox.jsonl").read_bytes(), before_inbox)
        self.assertEqual((s_changed / "run.log").read_bytes(), before_log)
        self.assertEqual((s_unavailable / "state.json").read_bytes(), before_u_state)
        self.assertEqual((s_unavailable / "inbox.jsonl").read_bytes(), before_u_inbox)
        self.assertEqual((s_unavailable / "run.log").read_bytes(), before_u_log)

    def test_work_status_asks_the_ledger_once_across_matched_sessions(self):
        from test_input_ledger_remote import available
        if not available():
            self.skipTest("native ledger worker binary unavailable")
        for index in range(5):
            self.make_session("work-matched-%d" % index, status="stopped", bundle=self.bundle_old)
        self.make_session("work-unpinned", status="failed", pin=False)
        counter = self.directory / "selection_calls.log"
        result = self.run_probe("!(println! (work-status))\n", socket=self._counting_native_ledger(counter))
        lines = [line for line in result.stdout.split("\n") if line.startswith("work-")]
        self.assertEqual([line.split(" · ")[0] for line in lines],
                         ["work-matched-%d" % index for index in range(5)] + ["work-unpinned"])
        for index in range(5):
            self.assertIn(
                "work-matched-%d · stopped · 3 steps · fixture-model high · report= · pin 8acffdbd 2026-10-22 · "
                "needs-current: it uses the retired SQLite LLM ledger" % index,
                result.stdout,
            )
        self.assertIn("work-unpinned · failed · 3 steps · fixture-model high · report= · unpinned · Task for work-unpinned",
                      result.stdout)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["llm-selection"])

        # (work-status "id") asks once for both its line and its detail.
        single = self.run_probe('!(println! (work-status "work-matched-0"))\n', socket=self._counting_native_ledger(counter))
        self.assertIn("needs-current: it uses the retired SQLite LLM ledger", single.stdout)
        self.assertIn("pin: bundle " + self.bundle_old_digest + " · engine cetta-engine (recorded " + self.engine_hash
                      + ", current " + self.engine_hash + ") · needs-current: it uses the retired SQLite LLM ledger",
                      single.stdout)
        self.assertEqual(counter.read_text(encoding="utf-8").splitlines(), ["llm-selection"])

    def test_native_view_through_a_real_ledger_shows_every_branch(self):
        from test_input_ledger_remote import available
        if not available():
            self.skipTest("native ledger worker binary unavailable")
        self.make_session("work-01-done", status="done")
        self.make_session("work-02-unpinned", status="failed", pin=False)
        self.make_session("work-03-retired", status="stopped", bundle=self.bundle_old)
        self.make_session("work-04-no-hash", status="interrupted", binary_sha256=None)
        self.make_session("work-05-resumable", status="running")
        self.make_session("work-06-changed", status="stopped", binary_sha256="f" * 64)
        self.make_session("work-07-engine-gone", status="stopped",
                          binary=str(self.directory / "absent-engine"))
        self.make_session("work-08-pretoken", status="stopped", phase=None)
        _label(self.make_session("work-09-held-future", status="stopped"), "hold", "waiting on upstream", "2026-11-10")
        _label(self.make_session("work-10-held-past", status="stopped"), "hold", "check after release", "2026-10-20")
        _label(self.make_session("work-11-held-indef", status="failed"), "hold", "parked indefinitely")
        _label(self.make_session("work-12-archived", status="stopped"), "archive", "superseded")
        self.make_session("work-13-launching", status="stopped", phase="reserved")
        self.make_session("work-14-nostate", write_state=False)

        first, counter = self.native_view(self.directory / "native-first.txt")
        second, _ = self.native_view(self.directory / "native-second.txt")
        self.assertEqual(first, second)
        self.assertEqual(counter.read_text().splitlines(), ["llm-selection"])
        lines = first.splitlines()
        self.assertEqual(lines[0], "CODING_SESSIONS 14: 1 done · 1 failed · 4 stopped · 1 interrupted · 1 running · "
                                   "1 launching · 1 unknown · 3 held · 1 archived · needs-current 4 · unverified 1 · "
                                   "resumable 8 · unpinned 1 · runtimes 3 (0 MB; 1 used by no open session)")
        for line in (
            "work-03-retired stopped 2026-10-23 · pin 8acffdbd 2026-10-22 · needs-current: it uses the retired SQLite LLM ledger",
            "work-04-no-hash interrupted 2026-10-23 · pin aaaa1111 2026-10-22 · unverified: engine not recorded",
            "work-06-changed stopped 2026-10-23 · pin aaaa1111 2026-10-22 · needs-current: its engine binary has changed",
            "work-07-engine-gone stopped 2026-10-23 · pin aaaa1111 2026-10-22 · needs-current: its engine binary is gone",
            "work-08-pretoken stopped 2026-10-23 · pin aaaa1111 2026-10-22 · needs-current: its runtime predates run tokens",
            "work-10-held-past stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held until 2026-10-20: check after release",
        ):
            self.assertIn(line, lines)
        self.assertIn("work-13-launching launching since", first)
        for hidden in ("work-01-done", "work-09-held-future", "work-11-held-indef", "work-12-archived"):
            self.assertNotIn("\n%s " % hidden, first)

        (self.directory / "memory" / "coding-settings.json").write_text('{"context": "summary"}', encoding="utf-8")
        summary, _ = self.native_view(self.directory / "native-summary.txt")
        self.assertEqual(summary, lines[0] + "\n" + lines[-1])
        (self.directory / "memory" / "coding-settings.json").write_text('{"context": "off"}', encoding="utf-8")
        result = self.run_probe('!(import! &self ./src/coding_estate_cetta.metta)\n'
                                '!(println! (estate:load))\n')
        self.assertIn('("missing")', result.stdout)

    def test_work_hold_work_archive_and_resume_clears_lifecycle(self):
        s_hold = self.make_session("work-hold-target", status="stopped")
        self.make_session("work-archive-target", status="failed")
        self.make_session("work-running-target", status="running")

        result = self.run_probe(
            '!(println! (work-hold "work-hold-target" "waiting for review" "2026-11-15"))\n'
            '!(println! (work-archive "work-archive-target" "obsolete experiment"))\n'
            '!(println! (work-hold "work-running-target" "note on running"))\n'
            '!(println! (work-hold "work-hold-target" "bad" "not-a-date"))\n'
            '!(println! (work-hold "missing-id" "why"))\n'
            '!(println! (work-archive "missing-id" "why"))\n'
            '!(println! (work-status "work-hold-target"))\n'
            '!(println! (setting "work" "context"))\n'
            '!(println! (setting "work" "context" "summary"))\n'
        )
        self.assertIn("WORK_HELD_UNTIL 2026-11-15 work-hold-target", result.stdout)
        self.assertIn("WORK_ARCHIVED work-archive-target", result.stdout)
        self.assertIn("WORK_HELD work-running-target", result.stdout)
        self.assertIn('work-hold: invalid review date \\"not-a-date\\"; use YYYY-MM-DD', result.stdout)
        self.assertIn("work-hold: no session missing-id", result.stdout)
        self.assertIn("work-archive: no session missing-id", result.stdout)
        self.assertIn("pin: bundle " + self.bundle_current_digest, result.stdout)
        self.assertIn("engine cetta-engine (recorded " + self.engine_hash, result.stdout)
        self.assertIn("lifecycle: hold until 2026-11-15: waiting for review", result.stdout)
        settings = self.directory / "memory" / "coding-settings.json"
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {"context": "summary"})
        self.assertEqual(json.loads((s_hold / "lifecycle.json").read_text(encoding="utf-8"))["hold"]["review"], "2026-11-15")

        settings.write_text('{"context": "open"}', encoding="utf-8")
        view_before, _ = self.native_view(self.directory / "view-before.txt", outcome="unreachable")
        # A future hold and an archive hide a session; a running session shows its label.
        self.assertNotIn("\nwork-hold-target ", view_before)
        self.assertNotIn("\nwork-archive-target ", view_before)
        self.assertIn("work-running-target running 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held: note on running", view_before)

        # Resuming moves lifecycle.json to lifecycle.previous.json and launches.
        resume_res = self.run_probe('!(println! (work-resume "work-hold-target"))\n')
        self.assertIn("WORK_RESUMING work-hold-target", resume_res.stdout)
        self.assertFalse((s_hold / "lifecycle.json").exists())
        prev = json.loads((s_hold / "lifecycle.previous.json").read_text(encoding="utf-8"))
        self.assertEqual((prev["hold"]["why"], prev["hold"]["review"]), ("waiting for review", "2026-11-15"))
        view_after, _ = self.native_view(self.directory / "view-after.txt", outcome="unreachable")
        self.assertRegex(
            view_after,
            r"\nwork-hold-target launching since \d{4}-\d{2}-\d{2} · pin aaaa1111 \d{4}-\d{2}-\d{2} · resumable\n",
        )

    def test_both_bundle_routes_place_the_coding_sessions_source_after_project_evidence(self):
        self.make_session("work-bundle-1", status="stopped")
        self.make_session("work-bundle-2", status="failed", pin=False)

        from test_context_bundle_native import CALLER, HEADER, guest
        from test_pins_context_native import q

        instructions = self.directory / "instructions.txt"
        instructions.write_text("Fixture instructions\n", encoding="utf-8")
        registry = self.directory / "registry.json"
        registry.write_text('{"schema":1,"projects":[]}', encoding="utf-8")
        (self.directory / "history.metta").write_text("", encoding="utf-8")
        (self.directory / "pins.txt").write_text("", encoding="utf-8")
        settings = {
            "METTACLAW_PROJECT_ROOT": str(self.directory),
            "METTACLAW_PROMPT_PATH": str(instructions),
            "METTACLAW_PROJECT_REGISTRY_PATH": str(registry),
            "METTACLAW_ITER_PROCESS_DIR": str(self.directory / "transformations"),
            "METTACLAW_OBSERVED_STRATEGY": "0",
        }
        # The guests run with this estate as their working directory.
        for subdir in ("src", "channels", "tests"):
            link = self.directory / subdir
            if not link.exists():
                link.symlink_to(ROOT / subdir)
        direct, caller = self.directory / "direct_bundle.txt", self.directory / "caller_bundle.txt"
        guest(self.directory, HEADER + [
            "!(import! &self ./src/context_sources_cetta.metta)",
            "!(py-call (context_bundle_probe.note %s (context-bundle:with-views () \"fixture conversation\")))" % q(direct),
        ], engine="cetta", runtime_root=self.directory, **settings)
        guest(self.directory, HEADER + CALLER + [
            '!(py-call (context_bundle_probe.trigger "idle"))',
            "!(py-call (context_bundle_probe.note %s (addition-context-sources)))" % q(caller),
        ], engine="cetta", runtime_root=self.directory, **settings)

        def block(text):
            for part in text.split("\n\n"):
                if part.startswith("SOURCE[coding-sessions]"):
                    return part
            self.fail(text)

        blocks = []
        for text in (direct.read_text(encoding="utf-8"), caller.read_text(encoding="utf-8")):
            stable_group = text.split("\n\n_cache_boundary_\n\n")[0]
            self.assertLess(stable_group.index("SOURCE[project-evidence]"), stable_group.index("SOURCE[coding-sessions]"))
            blocks.append(block(text))
        self.assertEqual(blocks[0], blocks[1])
        header, body = blocks[0].split("\n", 1)
        self.assertRegex(header, r"^SOURCE\[coding-sessions\] status=known revision=sha256:[0-9a-f]{64} ")
        self.assertEqual(body.splitlines()[1:3], [
            "work-bundle-1 stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            "work-bundle-2 failed 2026-10-23 · unpinned: a resume pins the current runtime"])


if __name__ == "__main__":
    unittest.main()

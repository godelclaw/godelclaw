"""Coding-session pin classification, lifecycle labels, and turn-context view."""

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT / "channels")]

import coding_estate  # noqa: E402
import context_sources  # noqa: E402
import input_ack  # noqa: E402


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def install_counting_selection_stub(counter_path, outcome="native"):
    """Install an input_ack._ledger_call stub inside a guest process that counts calls."""
    target = Path(counter_path)
    socket_path = target.parent / "ledger.sock"
    socket_path.write_text("", encoding="utf-8")
    os.environ["METTACLAW_INPUT_LEDGER_SOCKET"] = str(socket_path)

    def _stub(op, args=(), timeout=1.0, socket=None):
        with open(target, "a", encoding="utf-8") as stream:
            stream.write("%s\n" % op)
        if outcome == "importing":
            raise RuntimeError(
                "[(Error llm-ledger:selection (unsupported ((llm-ledger-importing 1762000000))))]"
            )
        if outcome == "unsupported":
            raise RuntimeError(
                "[(Error llm-ledger:selection (unsupported ((bogus-state))))]"
            )
        if outcome == "timeout":
            raise TimeoutError("timed out")
        if outcome == "unreachable":
            raise input_ack.LedgerUnavailable("connection refused")
        return str(outcome)

    input_ack._ledger_call = _stub
    return 1


class CodingEstateFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        coding_estate._engine_digest_cache.clear()
        coding_estate._runtime_blocks_cache.clear()
        self.socket_path = self.directory / "ledger.sock"
        self.socket_path.write_text("", encoding="utf-8")

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


class CodingEstateTests(CodingEstateFixture):
    def test_all_verdicts_and_reasons_and_guard_strengthening(self):
        # Docstring states CodingSessionLifecycle.guard / SafeOnPin scope without overclaiming
        self.assertIn("CodingSessionLifecycle.guard", coding_estate.__doc__)
        self.assertIn("SafeOnPin", coding_estate.__doc__)
        self.assertNotIn("nothing else is refused", coding_estate.__doc__)

        # World is a single frozen dataclass where None means live
        self.assertTrue(dataclasses.is_dataclass(coding_estate.World))
        w = coding_estate.World(selection={"llm-ledger-sqlite": "native"}, engines={}, today="2026-10-25")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            w.selection = {"llm-ledger-sqlite": "legacy"}

        # 1. No runtime.json -> unpinned
        s_unpinned = self.make_session("work-01-unpinned", pin=False)
        self.assertEqual(
            coding_estate.classify(s_unpinned),
            ("unpinned", "a resume pins the current runtime"),
        )
        self.assertEqual(coding_estate.resume_problem(s_unpinned), "")

        # 2. Bundle directory gone -> needs-current
        s_no_bundle = self.make_session(
            "work-02-bundle-gone",
            bundle=self.directory / "missing-bundle-dir",
            digest="11112222" * 8,
        )
        self.assertEqual(
            coding_estate.classify(s_no_bundle),
            ("needs-current", "its frozen runtime is gone"),
        )
        self.assertEqual(
            coding_estate.resume_problem(s_no_bundle),
            "its frozen runtime is gone",
        )

        # 3. Bundle without src/llm_ledger.metta:
        #    (a) "native" -> needs-current
        #    (b) "legacy" -> resumable (when hashed)
        #    (c) unknown (importing, unsupported, unreachable, timeout) ->
        #        view shows unverified, resume_problem refuses retryably
        s_old_bundle = self.make_session("work-03-old-bundle", bundle=self.bundle_old)
        # Missing or unconfigured socket path returns ("unknown", "unreachable") before any call
        with mock.patch.dict(os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": ""}, clear=False), mock.patch.object(
            input_ack, "_ledger_call", side_effect=AssertionError("must not call _ledger_call when socket missing")
        ):
            self.assertEqual(coding_estate.llm_ledger_selection(""), ("unknown", "unreachable"))
            self.assertEqual(
                coding_estate.llm_ledger_selection(self.directory / "absent.sock"),
                ("unknown", "unreachable"),
            )

        with mock.patch.dict(
            os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}, clear=False
        ):
            with mock.patch.object(input_ack, "_ledger_call", return_value="native"):
                self.assertEqual(
                    coding_estate.classify(s_old_bundle),
                    ("needs-current", "it uses the retired SQLite LLM ledger"),
                )
                self.assertEqual(
                    coding_estate.resume_problem(s_old_bundle),
                    "it uses the retired SQLite LLM ledger",
                )
            with mock.patch.object(input_ack, "_ledger_call", return_value="legacy"):
                self.assertEqual(coding_estate.classify(s_old_bundle), ("resumable", ""))
                self.assertEqual(coding_estate.resume_problem(s_old_bundle), "")

            # Real server exceptions and non-answers from llm_ledger.metta:72-77
            for outcome, exc in (
                ("importing", RuntimeError("[(Error llm-ledger:selection (unsupported ((llm-ledger-importing 1762000000))))]")),
                ("unsupported", RuntimeError("[(Error llm-ledger:selection (unsupported ((bogus-state))))]")),
                ("unreachable", input_ack.LedgerUnavailable("connection refused")),
                ("timeout", TimeoutError("timed out")),
            ):
                with self.subTest(outcome=outcome), mock.patch.object(input_ack, "_ledger_call", side_effect=exc):
                    self.assertEqual(
                        coding_estate.classify(s_old_bundle),
                        ("unverified", "the LLM ledger's backend selection is unavailable (%s)" % outcome),
                    )
                    self.assertEqual(
                        coding_estate.resume_problem(s_old_bundle),
                        "the LLM ledger's backend selection is unavailable (%s); its pin cannot be checked now. Try again later." % outcome,
                    )

            # Unrecognized non-error answers are never guessed: they classify as unsupported
            for bogus_answer in ("selected (llm-ledger-v1 sha256:abc 1.0)", "(llm-ledger-legacy why)", "importing", "unexpected"):
                with self.subTest(bogus_answer=bogus_answer), mock.patch.object(
                    input_ack, "_ledger_call", return_value=bogus_answer
                ):
                    self.assertEqual(coding_estate.llm_ledger_selection(), ("unknown", "unsupported"))

        # 4. No hash with binary present -> unverified (allowed)
        s_no_hash_present = self.make_session("work-04-no-hash-present", binary_sha256=None)
        self.assertEqual(
            coding_estate.classify(s_no_hash_present),
            ("unverified", "engine not recorded"),
        )
        self.assertEqual(coding_estate.resume_problem(s_no_hash_present), "")

        # 5. No hash with binary gone -> needs-current (refused; engine_problem passed it)
        s_no_hash_gone = self.make_session(
            "work-05-no-hash-gone",
            binary=str(self.directory / "absent-engine"),
            binary_sha256=None,
        )
        self.assertEqual(
            coding_estate.classify(s_no_hash_gone),
            ("needs-current", "its engine binary is gone"),
        )
        self.assertEqual(
            coding_estate.resume_problem(s_no_hash_gone),
            "its engine binary is gone",
        )

        # 6. Hash equal -> resumable
        s_hash_equal = self.make_session("work-06-hash-equal")
        self.assertEqual(coding_estate.classify(s_hash_equal), ("resumable", ""))
        self.assertEqual(coding_estate.resume_problem(s_hash_equal), "")

        # 7. Hash changed -> needs-current
        s_hash_changed = self.make_session("work-07-hash-changed", binary_sha256="f" * 64)
        self.assertEqual(
            coding_estate.classify(s_hash_changed),
            ("needs-current", "its engine binary has changed"),
        )
        self.assertEqual(
            coding_estate.resume_problem(s_hash_changed),
            "its engine binary has changed",
        )

    def test_exact_view_text_and_idempotence_and_settings(self):
        # Absent directory returns None
        empty_root = self.directory / "empty-agent"
        empty_root.mkdir()
        self.assertIsNone(coding_estate.context_view(empty_root))

        # Populate a mixed estate covering every status (including missing state.json -> unknown),
        # verdict, and hold/archive state
        self.make_session("work-01-done", status="done")
        self.make_session("work-02-unpinned", status="failed", pin=False)
        self.make_session("work-03-retired", status="stopped", bundle=self.bundle_old)
        self.make_session("work-04-no-hash", status="interrupted", binary_sha256=None)
        self.make_session("work-05-resumable", status="running")

        s_held_future = self.make_session("work-06-held-future", status="stopped")
        coding_estate.hold_session(s_held_future, "waiting on upstream", "2026-11-10", now=self.fixed_ts)

        s_held_past = self.make_session("work-07-held-past", status="stopped")
        coding_estate.hold_session(s_held_past, "check after release", "2026-10-20", now=self.fixed_ts)

        s_held_indef = self.make_session("work-08-held-indef", status="failed")
        coding_estate.hold_session(s_held_indef, "parked indefinitely", "", now=self.fixed_ts)

        s_archived = self.make_session("work-09-archived", status="stopped")
        coding_estate.archive_session(s_archived, "superseded by new work", now=self.fixed_ts)

        self.make_session("work-10-nostate", write_state=False)

        # Verify .lifecycle.lock and lifecycle.json are created 0o660 (shared group writable)
        self.assertEqual((s_held_future / ".lifecycle.lock").stat().st_mode & 0o777, 0o660)
        self.assertEqual((s_held_future / "lifecycle.json").stat().st_mode & 0o777, 0o660)

        world = coding_estate.World(today="2026-10-25")
        with mock.patch.dict(
            os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}, clear=False
        ), mock.patch.object(input_ack, "_ledger_call", return_value="native") as mocked_call:
            first = coding_estate.context_view(self.directory, world=world)
            second = coding_estate.context_view(self.directory, world=world)
            self.assertEqual(mocked_call.call_count, 2)

        self.assertEqual(first, second)
        expected = "\n".join([
            "CODING_SESSIONS 10: 1 done · 1 failed · 1 stopped · 1 interrupted · 1 running · 1 unknown · 3 held · 1 archived · "
            "needs-current 1 · unverified 1 · resumable 7 · unpinned 1 · "
            "runtimes 3 (0 MB; 1 used by no open session)",
            "work-02-unpinned failed 2026-10-23 · unpinned: a resume pins the current runtime",
            "work-03-retired stopped 2026-10-23 · pin 8acffdbd 2026-10-22 · needs-current: it uses the retired SQLite LLM ledger",
            "work-04-no-hash interrupted 2026-10-23 · pin aaaa1111 2026-10-22 · unverified: engine not recorded",
            "work-05-resumable running 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            "work-07-held-past stopped 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held until 2026-10-20: check after release",
            "work-10-nostate unknown 2026-10-23 · pin aaaa1111 2026-10-22 · resumable",
            'verbs: (work-status "id") (work-resume "id" ["current"]) (work-hold "id" "why" ["YYYY-MM-DD"]) (work-archive "id" "why")',
        ])
        self.assertEqual(first, expected)

        # "summary" setting gives only summary and verbs
        coding_estate.set_work_setting(self.directory, "context", "summary")
        with mock.patch.dict(
            os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}, clear=False
        ), mock.patch.object(input_ack, "_ledger_call", return_value="native"):
            summary_view = coding_estate.context_view(self.directory, world=world)
        self.assertEqual(
            summary_view,
            expected.splitlines()[0] + "\n" + expected.splitlines()[-1],
        )

        # "off" setting returns None
        coding_estate.set_work_setting(self.directory, "context", "off")
        self.assertIsNone(coding_estate.context_view(self.directory, world=world))

    def test_view_bound_with_two_hundred_sessions_keeps_summary_counts_and_verbs(self):
        for index in range(200):
            if index % 4 == 0:
                self.make_session("work-20261023-%04d" % index, status="failed", pin=False)
            elif index % 4 == 1:
                self.make_session("work-20261023-%04d" % index, status="stopped", bundle=self.bundle_old)
            elif index % 4 == 2:
                self.make_session("work-20261023-%04d" % index, status="interrupted", binary_sha256=None)
            else:
                self.make_session("work-20261023-%04d" % index, status="stopped")

        world = coding_estate.World(today="2026-10-25")
        with mock.patch.dict(
            os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}, clear=False
        ), mock.patch.object(input_ack, "_ledger_call", return_value="native") as mocked_call:
            view = coding_estate.context_view(self.directory, world=world)
            # Even with 50 matched old-bundle sessions, context_view makes only 1 selection call
            self.assertEqual(mocked_call.call_count, 1)

        self.assertLessEqual(len(view), 2000)
        lines = view.splitlines()
        self.assertTrue(lines[0].startswith(
            "CODING_SESSIONS 200: 50 failed · 100 stopped · 50 interrupted · "
            "needs-current 50 · unverified 50 · resumable 50 · unpinned 50 · "
        ))
        self.assertEqual(lines[-1], coding_estate.VERBS_LINE)
        self.assertRegex(lines[-2], r"^… \d+ more: \(work-status\)$")
        shown_count = len(lines) - 3
        omitted_count = int(lines[-2].split()[1])
        self.assertEqual(shown_count + omitted_count, 200)

    def test_lean_model_correspondence_reaches_every_branch(self):
        # Verify every branch of CodingSessionLifecycle.classify, guard, and listed
        # against coding_estate.classify, resume_problem, and listed using World.
        classify_branches = set()
        for pin_kind in ("unpinned", "bundle_missing", "retired", "unknown", "engine_gone", "no_hash", "hash_diff", "resumable"):
            name = "lean-classify-%s" % pin_kind
            if pin_kind == "unpinned":
                s = self.make_session(name, pin=False)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"})
            elif pin_kind == "bundle_missing":
                s = self.make_session(name, bundle=self.directory / "absent-bundle")
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"})
            elif pin_kind == "retired":
                s = self.make_session(name, bundle=self.bundle_old)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "native"})
            elif pin_kind == "unknown":
                s = self.make_session(name, bundle=self.bundle_old)
                world = coding_estate.World(selection={"llm-ledger-sqlite": ("unknown", "unreachable")})
            elif pin_kind == "engine_gone":
                s = self.make_session(name)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"}, engines={})
            elif pin_kind == "no_hash":
                s = self.make_session(name, binary_sha256=None)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"})
            elif pin_kind == "hash_diff":
                s = self.make_session(name)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"}, engines={str(self.engine): "b" * 64})
            else:
                s = self.make_session(name)
                world = coding_estate.World(selection={"llm-ledger-sqlite": "legacy"})

            verdict, _ = coding_estate.classify(s, world=world)
            problem = coding_estate.resume_problem(s, world=world)
            allowed = problem == ""
            classify_branches.add((pin_kind, verdict, allowed))

        self.assertEqual(
            classify_branches,
            {
                ("unpinned", "unpinned", True),
                ("bundle_missing", "needs-current", False),
                ("retired", "needs-current", False),
                ("unknown", "unverified", False),
                ("engine_gone", "needs-current", False),
                ("no_hash", "unverified", True),
                ("hash_diff", "needs-current", False),
                ("resumable", "resumable", True),
            },
        )

        # Every branch of listed: running (even archived/held), done, archived,
        # free, held future, held due, held indefinite.
        listed_branches = {}
        world_today = coding_estate.World(today="2026-10-25")
        for case_name, status, hold, archive in (
            ("running_free", "running", None, False),
            ("running_archived", "running", None, True),
            ("running_held_indef", "running", "", False),
            ("done_free", "done", None, False),
            ("archived_stopped", "stopped", None, True),
            ("stopped_free", "stopped", None, False),
            ("stopped_held_future", "stopped", "2026-10-30", False),
            ("stopped_held_today", "stopped", "2026-10-25", False),
            ("stopped_held_past", "stopped", "2026-10-20", False),
            ("stopped_held_indef", "stopped", "", False),
        ):
            s = self.make_session("lean-listed-%s" % case_name, status=status)
            if archive:
                coding_estate.archive_session(s, "archived")
            elif hold is not None:
                coding_estate.hold_session(s, "held", hold)
            listed_branches[case_name] = coding_estate.listed(s, world=world_today)

        self.assertEqual(
            listed_branches,
            {
                "running_free": True,
                "running_archived": True,
                "running_held_indef": True,
                "done_free": False,
                "archived_stopped": False,
                "stopped_free": True,
                "stopped_held_future": False,
                "stopped_held_today": True,
                "stopped_held_past": True,
                "stopped_held_indef": False,
            },
        )


@unittest.skipUnless(os.environ.get("CETTA_BIN"), "set CETTA_BIN to an HTTP-enabled Python build")
class CodingEstateProbeAndBundleTests(CodingEstateFixture):
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

    def run_probe(self, forms, *, cwd=None):
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
                env=self.env,
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

    def native_view(self, output, today="2026-10-25", outcome="native", sentinel=None):
        """Render through the real native snapshot/policy with old readers and policy forbidden."""
        if sentinel is None:
            sentinel = self.directory / "coding_estate_native_sentinel.py"
            sentinel.write_text('''import coding_estate
import coding_estate_snapshot
import input_ack
from pathlib import Path
import sys

FORBIDDEN_FILES = ("/coding_estate_snapshot.py", "/coding_estate.py")

def forbidden(*args, **kwargs):
    raise AssertionError("native view called forbidden Python observation/policy helper")

def _profile(frame, event, arg):
    if event != "call":
        return
    code = frame.f_code
    filename = code.co_filename.replace("\\\\", "/")
    if filename.endswith(FORBIDDEN_FILES) and code.co_name != "<module>":
        raise AssertionError(
            "native view entered %s:%s" % (Path(filename).name, code.co_name)
        )
    if filename.endswith("/input_ack.py") and code.co_name == "_ledger_call":
        op = frame.f_locals.get("op")
        if op == "llm-selection":
            raise AssertionError("native view called input_ack._ledger_call('llm-selection')")

def forbid():
    for attr in ("snapshot", "_session", "_date"):
        setattr(coding_estate_snapshot, attr, forbidden)
    for attr in (
        "classify", "listed", "context_view", "_read_json", "read_runs",
        "read_lifecycle", "_engine_sha256", "_dir_blocks", "llm_ledger_selection",
        "_matched_retirements", "snapshot_world", "snapshot_session_world",
        "_status_date", "launching_date", "work_setting", "_runtimes_summary",
    ):
        setattr(coding_estate, attr, forbidden)
    sys.setprofile(_profile)
    return 1

def record(path, text):
    Path(path).write_text(str(text), encoding="utf-8")
    return 1
''', encoding="utf-8")
        counter = self.directory / (output.name + ".selection")
        socket_path = self._counting_native_ledger(counter, outcome=outcome)
        probe = ROOT / ("estate_native_view_%d.metta" % os.getpid())
        probe.write_text(
            "!(import! &self (library lib_import))\n"
            '!(import! &self "%s")\n' % sentinel
            + "!(py-call (coding_estate_native_sentinel.forbid))\n"
            + "!(import! &self ./src/coding_estate_cetta.metta)\n"
            + '!(py-call (coding_estate_native_sentinel.record "%s" (estate:context-view-root "%s" "%s")))\n'
            % (output, self.directory, today),
            encoding="utf-8",
        )
        env = {**self.env, "METTACLAW_INPUT_LEDGER_SOCKET": socket_path}
        try:
            result = subprocess.run(
                [os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=self.directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            probe.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return output.read_text(encoding="utf-8"), counter

    def test_refused_work_resume_names_reason_and_launches_nothing(self):
        # 1. needs-current refusal (hash changed)
        s_changed = self.make_session("work-changed", status="stopped", binary_sha256="0" * 64)
        (s_changed / "run.log").write_text("original run log\n", encoding="utf-8")
        (s_changed / "inbox.jsonl").write_text('"prior guidance"\n', encoding="utf-8")
        before_state = (s_changed / "state.json").read_bytes()
        before_inbox = (s_changed / "inbox.jsonl").read_bytes()
        before_log = (s_changed / "run.log").read_bytes()

        # 2. retryable unverified refusal (bundle without src/llm_ledger.metta while selection call fails)
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

    def test_work_status_snapshots_selection_at_most_once_across_matched_sessions(self):
        for index in range(5):
            self.make_session("work-matched-%d" % index, status="stopped", bundle=self.bundle_old)
        counter = self.directory / "selection_calls.log"
        result = self.run_probe(
            "!(import! &self ./tests/test_coding_estate.py)\n"
            '!(py-call (test_coding_estate.install_counting_selection_stub "%s" "native"))\n'
            "!(println! (work-status))\n" % str(counter)
        )
        for index in range(5):
            self.assertIn(
                "work-matched-%d · stopped · 3 steps · fixture-model high · report= · pin 8acffdbd 2026-10-22 · "
                "needs-current: it uses the retired SQLite LLM ledger" % index,
                result.stdout,
            )
        calls = counter.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1)

        # Single-session (work-status "id") also makes at most 1 selection call for both line and detail
        counter.unlink()
        single = self.run_probe(
            "!(import! &self ./tests/test_coding_estate.py)\n"
            '!(py-call (test_coding_estate.install_counting_selection_stub "%s" "native"))\n'
            '!(println! (work-status "work-matched-0"))\n' % str(counter)
        )
        self.assertIn("needs-current: it uses the retired SQLite LLM ledger", single.stdout)
        self.assertEqual(len(counter.read_text(encoding="utf-8").splitlines()), 1)

    def test_native_view_matches_every_branch_and_never_calls_old_policy(self):
        self.make_session("work-01-done", status="done")
        self.make_session("work-02-unpinned", status="failed", pin=False)
        self.make_session("work-03-retired", status="stopped", bundle=self.bundle_old)
        self.make_session("work-04-no-hash", status="interrupted", binary_sha256=None)
        self.make_session("work-05-resumable", status="running")
        self.make_session("work-06-changed", status="stopped", binary_sha256="f" * 64)
        self.make_session("work-07-engine-gone", status="stopped",
                          binary=str(self.directory / "absent-engine"))
        self.make_session("work-08-pretoken", status="stopped", phase=None)
        held_future = self.make_session("work-09-held-future", status="stopped")
        coding_estate.hold_session(held_future, "waiting on upstream", "2026-11-10", now=self.fixed_ts)
        held_past = self.make_session("work-10-held-past", status="stopped")
        coding_estate.hold_session(held_past, "check after release", "2026-10-20", now=self.fixed_ts)
        held_indef = self.make_session("work-11-held-indef", status="failed")
        coding_estate.hold_session(held_indef, "parked indefinitely", "", now=self.fixed_ts)
        archived = self.make_session("work-12-archived", status="stopped")
        coding_estate.archive_session(archived, "superseded", now=self.fixed_ts)
        launching = self.make_session("work-13-launching", status="stopped", phase="reserved")
        self.make_session("work-14-nostate", write_state=False)

        first, counter = self.native_view(self.directory / "native-first.txt")
        second, _ = self.native_view(self.directory / "native-second.txt")
        world = coding_estate.World(today="2026-10-25")
        with mock.patch.dict(os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}), \
             mock.patch.object(input_ack, "_ledger_call", return_value="native") as selected:
            expected = coding_estate.context_view(self.directory, world=world)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(selected.call_count, 1)
        self.assertEqual(counter.read_text().splitlines(), ["llm-selection"])
        for verdict in ("needs-current", "unverified", "resumable", "unpinned"):
            self.assertIn(verdict, first)
        self.assertIn("work-13-launching launching since", first)
        self.assertNotIn("\nwork-09-held-future ", first)
        self.assertNotIn("\nwork-11-held-indef ", first)
        self.assertNotIn("\nwork-12-archived ", first)
        self.assertIn("work-10-held-past", first)

        coding_estate.set_work_setting(self.directory, "context", "summary")
        summary, _ = self.native_view(self.directory / "native-summary.txt")
        self.assertEqual(summary, expected.splitlines()[0] + "\n" + coding_estate.VERBS_LINE)
        coding_estate.set_work_setting(self.directory, "context", "off")
        result = self.run_probe('!(import! &self ./src/coding_estate_cetta.metta)\n'
                                '!(println! (estate:load))\n')
        self.assertIn('("missing")', result.stdout)

    def test_native_two_hundred_session_bound_and_single_selection_snapshot(self):
        for index in range(200):
            if index % 4 == 0:
                self.make_session("work-20261023-%04d" % index, status="failed", pin=False)
            elif index % 4 == 1:
                self.make_session("work-20261023-%04d" % index, status="stopped", bundle=self.bundle_old)
            elif index % 4 == 2:
                self.make_session("work-20261023-%04d" % index, status="interrupted", binary_sha256=None)
            else:
                self.make_session("work-20261023-%04d" % index, status="stopped")
        actual, counter = self.native_view(self.directory / "native-200.txt")
        with mock.patch.dict(os.environ, {"METTACLAW_INPUT_LEDGER_SOCKET": str(self.socket_path)}), \
             mock.patch.object(input_ack, "_ledger_call", return_value="native") as selected:
            expected = coding_estate.context_view(
                self.directory, world=coding_estate.World(today="2026-10-25"))
        self.assertEqual(actual, expected)
        self.assertEqual(selected.call_count, 1)
        self.assertEqual(counter.read_text().splitlines(), ["llm-selection"])
        self.assertLessEqual(len(actual), 2000)
        lines = actual.splitlines()
        self.assertEqual(lines[-1], coding_estate.VERBS_LINE)
        self.assertRegex(lines[-2], r"^… \d+ more: \(work-status\)$")
        shown_count = len(lines) - 3
        omitted_count = int(lines[-2].split()[1])
        self.assertEqual(shown_count + omitted_count, 200)

    def test_work_hold_work_archive_and_resume_clears_lifecycle(self):
        s_hold = self.make_session("work-hold-target", status="stopped")
        s_arch = self.make_session("work-archive-target", status="failed")
        s_run = self.make_session("work-running-target", status="running")

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
        self.assertIn('work-hold: invalid review date', result.stdout)
        self.assertIn("work-hold: no session missing-id", result.stdout)
        self.assertIn("work-archive: no session missing-id", result.stdout)
        self.assertIn("pin: bundle " + self.bundle_current_digest, result.stdout)
        self.assertIn("engine cetta-engine (recorded " + self.engine_hash, result.stdout)
        self.assertIn("lifecycle: hold until 2026-11-15: waiting for review", result.stdout)
        self.assertEqual(coding_estate.work_setting(self.directory, "context"), "summary")

        coding_estate.set_work_setting(self.directory, "context", "open")
        world_today = coding_estate.World(today="2026-10-25")
        view_before = coding_estate.context_view(self.directory, world=world_today)
        # Held until future date and archived are both excluded; running is still listed with its hold label
        self.assertNotIn("\nwork-hold-target ", view_before)
        self.assertNotIn("\nwork-archive-target ", view_before)
        self.assertIn("work-running-target running 2026-10-23 · pin aaaa1111 2026-10-22 · resumable · held: note on running", view_before)

        # Resuming work-hold-target moves lifecycle.json to lifecycle.previous.json and launches it
        resume_res = self.run_probe('!(println! (work-resume "work-hold-target"))\n')
        self.assertIn("WORK_RESUMING work-hold-target", resume_res.stdout)
        self.assertFalse((s_hold / "lifecycle.json").exists())
        self.assertTrue((s_hold / "lifecycle.previous.json").is_file())
        prev = json.loads((s_hold / "lifecycle.previous.json").read_text(encoding="utf-8"))
        self.assertEqual(prev["hold"]["why"], "waiting for review")
        self.assertEqual(prev["hold"]["review"], "2026-11-15")

        view_after = coding_estate.context_view(self.directory, world=world_today)
        self.assertRegex(
            view_after,
            r"\nwork-hold-target launching since \d{4}-\d{2}-\d{2} · pin aaaa1111 \d{4}-\d{2}-\d{2} · resumable\n",
        )

    def test_native_context_source_route_matches_python_with_policy_sentinel(self):
        self.make_session("work-bundle-1", status="stopped")
        self.make_session("work-bundle-2", status="failed", pin=False)
        self.native_view(self.directory / "native-sentinel-warmup.txt")
        sentinel = self.directory / "coding_estate_native_sentinel.py"

        from test_context_bundle_native import HEADER, guest
        from test_pins_context_native import guest_env, q
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
        for subdir in ("src", "channels", "tests"):
            link = self.directory / subdir
            if not link.exists():
                link.symlink_to(ROOT / subdir)
        out = self.directory / "native-direct-bundle.txt"
        guest(self.directory, HEADER + [
            '!(import! &self "%s")' % sentinel,
            '!(py-call (coding_estate_native_sentinel.forbid))',
            '!(import! &self ./src/context_sources_cetta.metta)',
            '!(py-call (context_bundle_probe.note %s (context-bundle:with-views ())))' % q(out),
        ], engine="cetta", runtime_root=self.directory, **settings)
        native_text = out.read_text(encoding="utf-8")

        import telegram
        env = guest_env(self.directory, "cetta", **settings)
        old_cwd = os.getcwd()
        try:
            os.chdir(self.directory)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                context_sources, "_PROJECTOR", context_sources.Projector()
            ), mock.patch.multiple(
                telegram, _reply_chat_id="", _last_chat_id="", _primary_chat_id="",
                _last_message_is_human=False, _last_from_bot=False,
            ):
                python_text = context_sources.bundle()
        finally:
            os.chdir(old_cwd)
        self.assertEqual(native_text, python_text)
        self.assertIn("SOURCE[coding-sessions] status=known", native_text)
        self.assertIn("work-bundle-1 stopped", native_text)
        self.assertIn("work-bundle-2 failed", native_text)

    def test_python_and_cetta_context_bundles_include_coding_sessions_identically(self):
        self.make_session("work-bundle-1", status="stopped")
        self.make_session("work-bundle-2", status="failed", pin=False)

        from test_context_bundle_native import CALLER, HEADER, guest
        from test_pins_context_native import guest_env, q

        instructions = self.directory / "instructions.txt"
        instructions.write_text("Fixture instructions\n", encoding="utf-8")
        registry = self.directory / "registry.json"
        registry.write_text('{"schema":1,"projects":[]}', encoding="utf-8")
        (self.directory / "history.metta").write_text("", encoding="utf-8")
        (self.directory / "pins.txt").write_text("", encoding="utf-8")

        out = self.directory / "native_bundle.txt"
        settings = {
            "METTACLAW_PROJECT_ROOT": str(self.directory),
            "METTACLAW_PROMPT_PATH": str(instructions),
            "METTACLAW_PROJECT_REGISTRY_PATH": str(registry),
            "METTACLAW_ITER_PROCESS_DIR": str(self.directory / "transformations"),
            "METTACLAW_OBSERVED_STRATEGY": "0",
        }
        lines = HEADER + CALLER + [
            '!(py-call (context_bundle_probe.trigger "idle"))',
            "!(py-call (context_bundle_probe.note %s (addition-context-sources)))" % q(out),
        ]
        # Run guest with cwd=self.directory by symlinking src, channels, tests into self.directory
        for subdir in ("src", "channels", "tests"):
            link = self.directory / subdir
            if not link.exists():
                link.symlink_to(ROOT / subdir)
        guest(self.directory, lines, engine="cetta", runtime_root=self.directory, **settings)
        native_text = out.read_text(encoding="utf-8")

        import telegram
        env = guest_env(self.directory, "cetta", **settings)
        old_cwd = os.getcwd()
        try:
            os.chdir(self.directory)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                context_sources, "_PROJECTOR", context_sources.Projector()
            ), mock.patch.multiple(
                telegram,
                _reply_chat_id="",
                _last_chat_id="",
                _primary_chat_id="",
                _last_message_is_human=False,
                _last_from_bot=False,
            ):
                python_text = context_sources.bundle()
        finally:
            os.chdir(old_cwd)

        def _extract_source_block(bundle_text, source_id):
            marker = "SOURCE[%s]" % source_id
            for block in bundle_text.split("\n\n"):
                if block.startswith(marker):
                    return block
            return None

        native_block = _extract_source_block(native_text, "coding-sessions")
        python_block = _extract_source_block(python_text, "coding-sessions")
        self.assertIsNotNone(native_block, native_text)
        self.assertEqual(native_block.encode("utf-8"), python_block.encode("utf-8"))
        self.assertEqual(native_text, python_text)
        self.assertIn("SOURCE[coding-sessions] status=known", native_text)
        for text in (native_text, python_text):
            stable_group = text.split("\n\n_cache_boundary_\n\n")[0]
            self.assertIn("SOURCE[coding-sessions]", stable_group)
            self.assertLess(
                stable_group.index("SOURCE[project-evidence]"),
                stable_group.index("SOURCE[coding-sessions]"),
            )


if __name__ == "__main__":
    unittest.main()

"""Phase A qualification suite for the CeTTa-backed execution-gateway queue store."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from execution_gateway import migration  # noqa: E402
from execution_gateway.cetta_worker import (  # noqa: E402
    E,
    S,
    WorkerUnavailable,
    describe_gateway_worker,
    gateway_cetta_ref,
    resolve_gateway_worker,
    validate_gateway_worker_bundle,
)
from execution_gateway.sqlite_store import SqliteStore  # noqa: E402
from execution_gateway.store import (  # noqa: E402
    Conflict,
    Store,
    StoreReplyUncertain,
    StoreUnavailable,
)

QUALIFIED_SPACE_JOB_QUEUE_SHA256 = (
    "9be25f30af6f2119d39fd8d024b49d77e5cd4ac6c2ed10a4156daf5b2b346000"
)
FAULT_MODULE = ROOT / "tests/fixtures/gateway_fault_worker.metta"


def make_request(
    identifier: str,
    *,
    tool: str = "synthetic",
    deadline: float | None = None,
    payload=None,
):
    resolved_deadline = (time.time() + 10_000.0) if deadline is None else float(deadline)
    request = {
        "id": identifier,
        "tool": tool,
        "arguments": [],
        "input": payload,
        "deadline": resolved_deadline,
    }
    intent = {k: request[k] for k in ("id", "tool", "arguments", "input")}
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
    return request, fingerprint


class GatewayMettaStorePhaseATest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gw-metta-phase-a-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        os.chmod(self.base, 0o700)

    def test_space_job_queue_is_unmodified_and_global_runtime_is_decoupled(self):
        vendored = ROOT / "src/execution_gateway/metta/kb/space_job_queue.metta"
        digest = hashlib.sha256(vendored.read_bytes()).hexdigest()
        self.assertEqual(digest, QUALIFIED_SPACE_JOB_QUEUE_SHA256)

        global_ref = (ROOT / "CETTA_REF").read_text().strip()
        self.assertEqual(global_ref, "1182239fd98c04c0ec9b8abc7019dc36a8ba3e07")
        self.assertEqual(gateway_cetta_ref(), "9273a379d3be9a07999bc427b6cf55eb4bf48c1a")

        verified = describe_gateway_worker()
        self.assertEqual(verified["revision"], gateway_cetta_ref())
        self.assertEqual(len(verified["sha256"]), 64)
        self.assertEqual(len(verified["proc_scope_sha256"]), 64)

        # Generic agent-runtime variables (CETTA_BIN, CETTA_VALUE_WORKER_BIN,
        # METTACLAW_MODE_CETTA_BIN) must never hijack gateway worker resolution.
        fake_old_dir = self.base / "old-cognition-engine"
        fake_old_dir.mkdir(mode=0o700)
        fake_cetta = fake_old_dir / "cetta"
        fake_cetta.write_text("#!/bin/sh\nexit 0\n")
        fake_cetta.chmod(0o755)
        fake_worker = fake_old_dir / "cetta-value-worker"
        fake_worker.write_text("#!/bin/sh\nexit 1\n")
        fake_worker.chmod(0o755)

        hostile_env = {
            "CETTA_BIN": str(fake_cetta),
            "CETTA_VALUE_WORKER_BIN": str(fake_worker),
            "METTACLAW_MODE_CETTA_BIN": str(fake_cetta),
        }
        old_pinned_worker = (
            Path.home() / f".local/lib/pettaclaw/cetta-ours-{global_ref[:8]}/cetta-value-worker"
        )
        if old_pinned_worker.is_file():
            hostile_env["CETTA_VALUE_WORKER_BIN"] = str(old_pinned_worker)
            hostile_env["CETTA_BIN"] = str(old_pinned_worker.parent / "cetta")

        with mock.patch.dict(os.environ, hostile_env, clear=False):
            os.environ.pop("METTACLAW_GATEWAY_WORKER_BIN", None)
            resolved = resolve_gateway_worker()
            self.assertEqual(resolved, verified["path"])
            with Store(self.base / "decoupled-store.db", "alpha") as store:
                req, fp = make_request("decoupled-1")
                store.submit(req, fp, "read")
                self.assertEqual(store.claim()["id"], "decoupled-1")

        # Negative controls: gateway-specific override or explicit worker_binary
        # must validate build-manifest.json (revision, ENABLE_DURABLE=1, .proc-scope, SHA-256).
        if old_pinned_worker.is_file():
            with mock.patch.dict(
                os.environ, {"METTACLAW_GATEWAY_WORKER_BIN": str(old_pinned_worker)}, clear=False
            ):
                with self.assertRaisesRegex(
                    WorkerUnavailable, "missing executable companion|revision mismatch"
                ):
                    resolve_gateway_worker()

        def make_fake_bundle(name: str) -> Path:
            bdir = self.base / name
            bdir.mkdir(mode=0o700)
            shutil.copy2(verified["path"], bdir / "cetta-value-worker")
            shutil.copy2(
                verified["path"].parent / "cetta-value-worker.proc-scope",
                bdir / "cetta-value-worker.proc-scope",
            )
            manifest = {
                "schema": 1,
                "revision": gateway_cetta_ref(),
                "features": ["BUILD=python", "ENABLE_DURABLE=1"],
                "files": {
                    "cetta-value-worker": verified["sha256"],
                    "cetta-value-worker.proc-scope": verified["proc_scope_sha256"],
                },
            }
            (bdir / "build-manifest.json").write_text(json.dumps(manifest))
            return bdir

        valid_copy = make_fake_bundle("bundle-valid")
        self.assertEqual(
            validate_gateway_worker_bundle(valid_copy / "cetta-value-worker")["sha256"],
            verified["sha256"],
        )

        tampered = make_fake_bundle("bundle-tampered")
        raw_bytes = bytearray((tampered / "cetta-value-worker").read_bytes())
        raw_bytes[-1] ^= 0xFF
        (tampered / "cetta-value-worker").write_bytes(bytes(raw_bytes))
        with self.assertRaisesRegex(WorkerUnavailable, "SHA-256 mismatch"):
            Store(self.base / "tampered.db", "alpha", worker_binary=tampered / "cetta-value-worker")

        no_manifest = make_fake_bundle("bundle-no-manifest")
        (no_manifest / "build-manifest.json").unlink()
        with self.assertRaisesRegex(WorkerUnavailable, "missing build-manifest.json"):
            resolve_gateway_worker(no_manifest / "cetta-value-worker")

        no_companion = make_fake_bundle("bundle-no-proc-scope")
        (no_companion / "cetta-value-worker.proc-scope").unlink()
        with self.assertRaisesRegex(WorkerUnavailable, "missing executable companion"):
            resolve_gateway_worker(no_companion / "cetta-value-worker")

        wrong_rev = make_fake_bundle("bundle-wrong-rev")
        m_wrong = json.loads((wrong_rev / "build-manifest.json").read_text())
        m_wrong["revision"] = global_ref
        (wrong_rev / "build-manifest.json").write_text(json.dumps(m_wrong))
        with self.assertRaisesRegex(WorkerUnavailable, "revision mismatch"):
            resolve_gateway_worker(wrong_rev / "cetta-value-worker")
        with mock.patch.dict(
            os.environ,
            {"METTACLAW_GATEWAY_WORKER_BIN": str(wrong_rev / "cetta-value-worker")},
            clear=False,
        ):
            with self.assertRaisesRegex(WorkerUnavailable, "revision mismatch"):
                resolve_gateway_worker()

        no_durable = make_fake_bundle("bundle-no-durable")
        m_nodur = json.loads((no_durable / "build-manifest.json").read_text())
        m_nodur["features"] = ["BUILD=python"]
        (no_durable / "build-manifest.json").write_text(json.dumps(m_nodur))
        with self.assertRaisesRegex(WorkerUnavailable, "lacks ENABLE_DURABLE=1"):
            resolve_gateway_worker(no_durable / "cetta-value-worker")

    def test_differential_parity_scripted_and_randomized_against_sqlite_oracle(self):
        native_dir = self.base / "native-queue.db"
        sqlite_file = self.base / "oracle-queue.sqlite3"
        known: set[str] = set()
        clock = 100.0

        # Initialize once and verify owner conflict parity on reopen after close
        with Store(native_dir, "alpha"):
            pass
        oracle = SqliteStore(sqlite_file, "alpha", conflict_cls=Conflict)
        with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
            Store(native_dir, "beta")
        with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
            SqliteStore(sqlite_file, "beta", conflict_cls=Conflict)

        with Store(native_dir, "alpha") as native:

            def step(method: str, *args, **kwargs):
                nonlocal clock
                clock += 1.0
                expected, expected_err = None, None
                actual, actual_err = None, None
                with mock.patch("time.time", return_value=clock):
                    try:
                        expected = getattr(oracle, method)(*args, **kwargs)
                    except Conflict as err:
                        expected_err = str(err)
                    try:
                        actual = getattr(native, method)(*args, **kwargs)
                    except Conflict as err:
                        actual_err = str(err)

                self.assertEqual(
                    actual_err,
                    expected_err,
                    f"Conflict mismatch on {method}{args}: native={actual_err!r} sqlite={expected_err!r}",
                )
                if expected_err is None:
                    self.assertEqual(
                        json.dumps(actual, sort_keys=True),
                        json.dumps(expected, sort_keys=True),
                        f"Return mismatch on {method}{args}:\nnative={actual}\nsqlite={expected}",
                    )
                for jid in sorted(known):
                    got = native.get(jid)
                    wanted = oracle.get(jid)
                    self.assertEqual(
                        json.dumps(got, sort_keys=True),
                        json.dumps(wanted, sort_keys=True),
                        f"Stored state mismatch on {jid} after {method}{args}:\nnative={got}\nsqlite={wanted}",
                    )
                    self.assertEqual(
                        native.cancellation_requested(jid, durable=False),
                        oracle.cancellation_requested(jid),
                    )
                    self.assertEqual(
                        native.cancellation_requested(jid, durable=True),
                        oracle.cancellation_requested(jid),
                    )
                return actual

            def sub(jid: str, *, effect: str = "read", deadline: float = 10_000.0, payload=None, tool="synthetic"):
                req, fp = make_request(jid, tool=tool, deadline=deadline, payload=payload)
                known.add(jid)
                return step("submit", req, fp, effect)

            # 1. Idempotent duplicate vs fingerprint conflict
            sub("j-a", payload={"v": 1})
            sub("j-a", payload={"v": 1})
            req_conflict, fp_conflict = make_request("j-a", payload={"v": 2})
            step("submit", req_conflict, fp_conflict, "read")

            # 2. FIFO order & running cancel + edge result=0 / outputs
            sub("j-b", effect="write", payload={"op": "w"})
            self.assertEqual(step("claim")["id"], "j-a")
            step("cancel", "j-a")
            step("cancel", "j-a")
            self.assertTrue(step("cancellation_requested", "j-a"))
            step("finish", "j-a", "succeeded", result=0, outputs={"stdout": {"bytes": 0}})
            # Finishing an already-finished or absent job raises exact Conflict
            step("finish", "j-a", "succeeded")
            step("finish", "never-existed", "failed")

            # 3. Recovery across multiple simultaneously running read & write jobs
            sub("j-c", effect="read")
            sub("j-d", effect="write")
            self.assertEqual(step("claim")["id"], "j-b")
            self.assertEqual(step("claim")["id"], "j-c")
            self.assertEqual(step("claim")["id"], "j-d")
            step("recover")
            self.assertEqual(native.get("j-b")["state"], "uncertain")
            self.assertEqual(native.get("j-c")["state"], "failed")
            self.assertEqual(native.get("j-d")["state"], "uncertain")
            self.assertIsNone(step("claim"))

            # 4. Exact single-expiration claim contract (must NOT skip ahead!)
            sub("exp-1", deadline=1.0)
            sub("exp-2", deadline=2.0)
            sub("valid-after-exp", deadline=99_999.0)
            self.assertIsNone(step("claim"))
            self.assertEqual(native.get("exp-1")["state"], "expired")
            self.assertEqual(native.get("exp-2")["state"], "queued")
            self.assertEqual(native.get("valid-after-exp")["state"], "queued")
            self.assertIsNone(step("claim"))
            self.assertEqual(native.get("exp-2")["state"], "expired")
            self.assertEqual(native.get("valid-after-exp")["state"], "queued")
            self.assertEqual(step("claim")["id"], "valid-after-exp")
            step("finish", "valid-after-exp", "failed", result=False, error="")

            # 5. Queued & absent cancellation
            self.assertIsNone(step("cancel", "absent-id"))
            sub("q-cancel")
            self.assertEqual(step("cancel", "q-cancel")["state"], "cancelled")

            # 6. 400-step seeded randomized differential trace
            rng = random.Random(20261101)
            terminal_Pool = ("succeeded", "failed", "cancelled", "expired", "uncertain")
            result_pool = (None, 0, False, "", [], {}, {"ok": True, "n": 42})
            error_pool = (None, "", "tool-exit", "deadline")
            for idx in range(400):
                action = rng.randrange(7)
                if action in (0, 1):
                    jid = f"fuzz-{rng.randrange(25)}"
                    eff = "write" if rng.randrange(2) else "read"
                    dl = clock - 1.0 if rng.randrange(6) == 0 else clock + 5000.0
                    payload = {"variant": rng.randrange(2)} if rng.randrange(4) == 0 else {"fixed": jid}
                    req, fp = make_request(jid, deadline=dl, payload=payload)
                    known.add(jid)
                    step("submit", req, fp, eff)
                elif action == 2:
                    step("claim")
                elif action == 3 and known:
                    step("cancel", rng.choice(sorted(known)))
                elif action == 4 and known:
                    target = rng.choice(sorted(known))
                    st = rng.choice(terminal_Pool)
                    res = rng.choice(result_pool)
                    err = rng.choice(error_pool)
                    out = {"step": idx} if rng.randrange(2) else None
                    step("finish", target, st, result=res, error=err, outputs=out)
                elif action == 5:
                    step("recover")
                elif known:
                    step("cancellation_requested", rng.choice(sorted(known)))

    def test_crash_cutpoint_before_publication_leaves_zero_torn_state(self):
        store_dir = self.base / "crash-before.db"
        req, fp = make_request("cutpoint-before", payload={"x": 1})
        ts = 500.0
        cmd = E("GatewaySubmitV1", req["id"], fp, json.dumps(req), S("write"), req["deadline"], ts)

        with Store(store_dir, "alpha", module=FAULT_MODULE, entry="fault:run") as store:
            armed = store._call(E("ArmFault", S("CrashBeforePublishV1")))
            self.assertEqual(armed, E("FaultArmedV1", S("CrashBeforePublishV1")))

            with mock.patch("time.time", return_value=ts):
                with self.assertRaises(StoreReplyUncertain) as ctx:
                    store.submit(req, fp, "write", operation_id="op-before-1")
            self.assertEqual(ctx.exception.operation_id, "op-before-1")

            # New worker spawns cleanly with NoFaultV1; receipt must be absent and job absent
            self.assertIsNone(store.resolve_command("op-before-1", cmd))
            self.assertIsNone(store.get("cutpoint-before"))

            # Retrying with the exact same operation_id commits cleanly once
            with mock.patch("time.time", return_value=ts):
                doc = store.submit(req, fp, "write", operation_id="op-before-1")
            self.assertEqual(doc["state"], "queued")
            self.assertEqual(store.last_envelope[0], S("GatewayCommittedV1"))
            self.assertEqual(len(doc["receipts"]), 1)

    def test_crash_cutpoint_after_publication_before_reply_and_no_replayed_claim(self):
        store_dir = self.base / "crash-after.db"
        req, fp = make_request("cutpoint-after", payload={"y": 2})
        ts = 600.0
        submit_cmd = E(
            "GatewaySubmitV1", req["id"], fp, json.dumps(req), S("write"), req["deadline"], ts
        )

        with Store(store_dir, "alpha", module=FAULT_MODULE, entry="fault:run") as store:
            store._call(E("ArmFault", S("CrashAfterPublishBeforeReplyV1")))
            with mock.patch("time.time", return_value=ts):
                with self.assertRaises(StoreReplyUncertain):
                    store.submit(req, fp, "write", operation_id="op-after-submit")

            # Durable receipt proves publication succeeded before worker exit
            resolved = store.resolve_command("op-after-submit", submit_cmd)
            self.assertEqual(resolved, E("GatewaySubmittedV1", "cutpoint-after"))
            self.assertEqual(store.get("cutpoint-after")["state"], "queued")

            # Explicit retry with same operation_id replays idempotently without duplicate receipts
            with mock.patch("time.time", return_value=ts + 10.0):
                replayed_doc = store.submit(req, fp, "write", operation_id="op-after-submit")
            self.assertEqual(store.last_envelope[0], S("GatewayReplayedV1"))
            self.assertEqual(len(replayed_doc["receipts"]), 1)
            self.assertEqual(replayed_doc["receipts"][0]["at"], ts)

            # Now test lost-reply on Claim: durable state becomes running, replay is refused,
            # and recover() conservatively settles the write job as uncertain.
            claim_ts = 620.0
            claim_cmd = E("GatewayClaimV1", claim_ts)
            store._call(E("ArmFault", S("CrashAfterPublishBeforeReplyV1")))
            with mock.patch("time.time", return_value=claim_ts):
                with self.assertRaises(StoreReplyUncertain):
                    store.claim(operation_id="op-after-claim")

            self.assertEqual(
                store.resolve_command("op-after-claim", claim_cmd),
                E("GatewayClaimedV1", "cutpoint-after"),
            )
            self.assertEqual(store.get("cutpoint-after")["state"], "running")

            with mock.patch("time.time", return_value=claim_ts):
                with self.assertRaisesRegex(Conflict, "replayed claim grants no dispatch right"):
                    store.claim(operation_id="op-after-claim")

            store.recover()
            settled = store.get("cutpoint-after")
            self.assertEqual((settled["state"], settled["error"]), ("uncertain", "gateway-restarted"))

    def test_worker_sigkill_respawns_and_reattaches_owner_cleanly(self):
        store_dir = self.base / "sigkill.db"
        with Store(store_dir, "alpha") as store:
            req, fp = make_request("survive-kill")
            store.submit(req, fp, "read")
            claimed = store.claim()
            self.assertEqual(claimed["id"], "survive-kill")
            self.assertFalse(store.cancellation_requested("survive-kill"))

            old_pid = store._worker.process.pid
            os.kill(old_pid, signal.SIGKILL)
            store._worker.process.wait(timeout=5)

            # Next operation transparently respawns worker, reopens store, and continues
            store.cancel("survive-kill")
            self.assertTrue(store.cancellation_requested("survive-kill", durable=False))
            self.assertTrue(store.cancellation_requested("survive-kill", durable=True))
            store.finish("survive-kill", "cancelled", error="cancelled")
            self.assertEqual(store.get("survive-kill")["state"], "cancelled")
            self.assertNotEqual(store._worker.process.pid, old_pid)

    def test_sqlite_backup_import_leaves_source_untouched_and_reverse_export_continues(self):
        legacy_dir = self.base / "agent-state"
        legacy_dir.mkdir(mode=0o700)
        sqlite_src = legacy_dir / "queue.sqlite3"

        # 1. Populate rich multi-state SQLite queue
        sql = SqliteStore(sqlite_src, "alpha", conflict_cls=Conflict)
        clock = 1_700_000_000.25

        def at(offset: float):
            return mock.patch("time.time", return_value=clock + offset)

        with at(1.0):
            r1, fp1 = make_request("s-succeeded", payload={"n": 1})
            sql.submit(r1, fp1, "read")
            sql.claim()
            sql.finish("s-succeeded", "succeeded", result=0, outputs={"stdout": {"bytes": 12}})
        with at(2.0):
            r2, fp2 = make_request("s-failed", payload={"n": 2})
            sql.submit(r2, fp2, "read")
            sql.claim()
            sql.finish("s-failed", "failed", error="")
        with at(3.0):
            r3, fp3 = make_request("s-uncertain", payload={"n": 3})
            sql.submit(r3, fp3, "write")
            sql.claim()
            sql.recover()
        with at(4.0):
            r4, fp4 = make_request("s-expired", deadline=clock - 10.0)
            sql.submit(r4, fp4, "read")
            self.assertIsNone(sql.claim())
        with at(5.0):
            r5, fp5 = make_request("s-cancelled")
            sql.submit(r5, fp5, "write")
            sql.cancel("s-cancelled")
        with at(6.0):
            r6, fp6 = make_request("s-running")
            sql.submit(r6, fp6, "write")
            self.assertEqual(sql.claim()["id"], "s-running")
            sql.cancel("s-running")
        with at(6.5):
            r6b, fp6b = make_request("s-running-uncancelled")
            sql.submit(r6b, fp6b, "read")
            self.assertEqual(sql.claim()["id"], "s-running-uncancelled")
        with at(7.0):
            r7, fp7 = make_request("s-queued-1")
            r8, fp8 = make_request("s-queued-2")
            sql.submit(r7, fp7, "read")
            sql.submit(r8, fp8, "write")

        pre_ids = (
            "s-succeeded",
            "s-failed",
            "s-uncertain",
            "s-expired",
            "s-cancelled",
            "s-running",
            "s-running-uncancelled",
            "s-queued-1",
            "s-queued-2",
        )
        expected_pre = {jid: sql.get(jid) for jid in pre_ids}
        src_sha_before = hashlib.sha256(sqlite_src.read_bytes()).hexdigest()
        src_stat_before = sqlite_src.stat()

        # 2. Import via CLI (and verify source SQLite file is 100% untouched byte-for-byte & mtime!)
        cetta_dir = legacy_dir / "queue.db"
        backup_copy = legacy_dir / "queue.sqlite3.backup"
        import_out = io.StringIO()
        with redirect_stdout(import_out):
            migration.main([
                "import",
                "--sqlite",
                str(sqlite_src),
                "--store",
                str(cetta_dir),
                "--agent",
                "alpha",
                "--backup",
                str(backup_copy),
            ])
        import_report = json.loads(import_out.getvalue())
        self.assertEqual(import_report["jobs"], len(pre_ids))

        self.assertTrue(sqlite_src.is_file())
        self.assertEqual(hashlib.sha256(sqlite_src.read_bytes()).hexdigest(), src_sha_before)
        self.assertEqual(sqlite_src.stat().st_mtime_ns, src_stat_before.st_mtime_ns)
        self.assertTrue(backup_copy.is_file())

        # 3. Verify 100% parity in CeTTa, then perform new post-cutover CeTTa writes!
        with Store(cetta_dir, "alpha") as store:
            for jid in pre_ids:
                self.assertEqual(
                    json.dumps(store.get(jid), sort_keys=True),
                    json.dumps(expected_pre[jid], sort_keys=True),
                )
            self.assertTrue(store.cancellation_requested("s-running", durable=True))
            self.assertFalse(store.cancellation_requested("s-running-uncancelled", durable=True))

            with at(19.0):
                # Prove cancelling an imported uncancelled running job hits (running False)
                cancelled_running = store.cancel("s-running-uncancelled")
                self.assertEqual(cancelled_running["receipts"][-1]["state"], "cancel-requested")
                self.assertTrue(store.cancellation_requested("s-running-uncancelled", durable=True))

            with at(20.0):
                store.recover()
                self.assertEqual(store.get("s-running")["state"], "uncertain")
                self.assertEqual(store.get("s-running-uncancelled")["state"], "failed")
                claimed1 = store.claim()
                self.assertEqual(claimed1["id"], "s-queued-1")
                store.finish("s-queued-1", "succeeded", result={"post_cutover": True})
                rn, fpn = make_request("cetta-new-write", payload={"fresh": "cetta"})
                store.submit(rn, fpn, "write")
                self.assertEqual(store.claim()["id"], "s-queued-2")
                store.finish("s-queued-2", "succeeded", result={"second": 2})

            all_ids = (*pre_ids, "cetta-new-write")
            cetta_snapshot = {jid: store.get(jid) for jid in all_ids}
            max_cetta_seq = max(
                r["sequence"] for doc in cetta_snapshot.values() for r in doc["receipts"]
            )

        # 4. Reverse-export CeTTa (including new post-cutover writes!) back to SQLite
        rollback_sqlite = self.base / "rolled-back-queue.sqlite3"
        export_out = io.StringIO()
        with redirect_stdout(export_out):
            migration.main([
                "export",
                "--store",
                str(cetta_dir),
                "--sqlite",
                str(rollback_sqlite),
            ])
        export_report = json.loads(export_out.getvalue())
        self.assertEqual(export_report["jobs"], len(all_ids))

        rolled = SqliteStore(rollback_sqlite, "alpha", conflict_cls=Conflict)
        for jid in all_ids:
            self.assertEqual(
                json.dumps(rolled.get(jid), sort_keys=True),
                json.dumps(cetta_snapshot[jid], sort_keys=True),
            )
        self.assertTrue(rolled.cancellation_requested("s-running"))
        self.assertTrue(rolled.cancellation_requested("s-cancelled"))

        # 5. Exercise post-rollback SQLite continuation: queued CeTTa job claims FIFO
        # and new receipt sequence numbers advance strictly beyond max_cetta_seq!
        with at(40.0):
            claimed_after_rollback = rolled.claim()
            self.assertEqual(claimed_after_rollback["id"], "cetta-new-write")
            rolled.finish("cetta-new-write", "succeeded", result={"rolled_back_ok": True})
            r_post, fp_post = make_request("post-rollback-job")
            post_doc = rolled.submit(r_post, fp_post, "read")
            self.assertGreater(post_doc["receipts"][0]["sequence"], max_cetta_seq)

    def test_cancellation_polling_benchmark_justifies_in_process_durable_backed_flag(self):
        store_dir = self.base / "bench-cancel.db"
        sqlite_path = self.base / "bench-cancel.sqlite3"
        req, fp = make_request("running-job")

        sql = SqliteStore(sqlite_path, "alpha", conflict_cls=Conflict)
        sql.submit(req, fp, "read")
        sql.claim()

        with Store(store_dir, "alpha") as store:
            store.submit(req, fp, "read")
            store.claim()

            # Warmup
            for _ in range(20):
                store.cancellation_requested("running-job", durable=False)
                store.cancellation_requested("running-job", durable=True)
                sql.cancellation_requested("running-job")

            rounds = 400
            t0 = time.perf_counter_ns()
            for _ in range(rounds):
                self.assertFalse(store.cancellation_requested("running-job", durable=False))
            cached_us = (time.perf_counter_ns() - t0) / rounds / 1000.0

            t0 = time.perf_counter_ns()
            for _ in range(rounds):
                self.assertFalse(store.cancellation_requested("running-job", durable=True))
            durable_rpc_us = (time.perf_counter_ns() - t0) / rounds / 1000.0

            t0 = time.perf_counter_ns()
            for _ in range(rounds):
                self.assertFalse(sql.cancellation_requested("running-job"))
            sqlite_us = (time.perf_counter_ns() - t0) / rounds / 1000.0

            # Now cancel and verify immediate flip on both cached and durable paths
            store.cancel("running-job")
            self.assertTrue(store.cancellation_requested("running-job", durable=False))
            self.assertTrue(store.cancellation_requested("running-job", durable=True))

            # At 100 Hz (10 ms poll), < 25 us/poll is < 0.25% of one CPU core.
            self.assertLess(cached_us, 25.0, (cached_us, durable_rpc_us, sqlite_us))
            self.assertLess(cached_us * 10, durable_rpc_us, (cached_us, durable_rpc_us, sqlite_us))

    def test_measurement_driven_quiescent_compaction_reclaims_bytes_and_preserves_state(self):
        store_dir = self.base / "compact.db"
        with Store(
            store_dir,
            "alpha",
            min_compact_bytes=32 * 1024,
            min_reclaim_bytes=16 * 1024,
            compact_ratio=1.8,
        ) as store:
            # Below physical floor: quiescent maintenance is a no-op
            r0, fp0 = make_request("job-0", payload={"blob": "a" * 256})
            store.submit(r0, fp0, "read")
            self.assertIsNone(store.maintain_if_needed())

            # Drive append-only CoW tree updates (submit -> claim -> cancel -> finish)
            expected = {"job-0": store.get("job-0")}
            for idx in range(1, 65):
                jid = f"job-{idx}"
                req, fp = make_request(jid, payload={"blob": "x" * 512, "idx": idx})
                store.submit(req, fp, "read")
                if idx == 1:
                    # Claim job-0 first to drain FIFO
                    self.assertEqual(store.claim()["id"], "job-0")
                    store.finish("job-0", "succeeded", result={"done": 0})
                    expected["job-0"] = store.get("job-0")
                claimed = store.claim()
                self.assertEqual(claimed["id"], jid)
                store.cancel(jid)
                store.finish(jid, "cancelled", error="cancelled")
                expected[jid] = store.get(jid)

            before_stats = store.live_stats()
            self.assertGreater(before_stats["physical_bytes"], 32 * 1024)

            maintenance = store.maintain_if_needed()
            self.assertIsNotNone(maintenance)
            self.assertLess(
                maintenance["after_physical_bytes"],
                maintenance["before_physical_bytes"],
                maintenance,
            )
            self.assertGreaterEqual(maintenance["removed_segments"], 1)
            self.assertLess(maintenance["ratio_after"], maintenance["ratio_before"])

            # Immediate second call at quiescent boundary does NOT re-compact
            self.assertIsNone(store.maintain_if_needed())

            # Every single job & receipt survives compaction & segment GC intact
            for jid, wanted in expected.items():
                self.assertEqual(
                    json.dumps(store.get(jid), sort_keys=True),
                    json.dumps(wanted, sort_keys=True),
                )

    def test_idle_claim_polling_performs_zero_durable_writes(self):
        idle_dir = self.base / "idle-poll.db"
        with Store(idle_dir, "alpha", min_compact_bytes=10**9) as store:
            before = store.live_stats()
            # 500 empty claims (25s equivalent at 50 ms gateway idle poll) must be
            # 100% read-only: zero durable revision, live-key, live-byte, or segment growth.
            for _ in range(500):
                self.assertIsNone(store.claim())
                self.assertEqual(store.last_envelope[0], S("GatewayEmptyReadOnlyV1"))
            after_empty = store.live_stats()
            for metric in ("revision", "live_keys", "live_bytes", "physical_bytes"):
                self.assertEqual(after_empty[metric], before[metric], (metric, before, after_empty))

            # Non-empty claim transitions (single expiration + running start) still commit
            # atomically with durable command receipts and refuse claim replay even after empty.
            now = 2_000.0
            with mock.patch("time.time", return_value=now):
                r_exp, fp_exp = make_request("exp-idle", deadline=now - 5.0)
                r_ok, fp_ok = make_request("ready-idle", deadline=now + 500.0)
                store.submit(r_exp, fp_exp, "read")
                store.submit(r_ok, fp_ok, "write")

            rev_after_submits = store.live_stats()["revision"]
            with mock.patch("time.time", return_value=now + 1.0):
                self.assertIsNone(store.claim(operation_id="claim-expire-1"))
            self.assertEqual(store.get("exp-idle")["state"], "expired")
            self.assertEqual(store.get("ready-idle")["state"], "queued")
            self.assertEqual(store.live_stats()["revision"], rev_after_submits + 1)
            self.assertEqual(
                store.resolve_command("claim-expire-1", E("GatewayClaimV1", now + 1.0)),
                S("GatewayEmptyV1"),
            )
            with mock.patch("time.time", return_value=now + 1.0):
                with self.assertRaisesRegex(Conflict, "replayed claim grants no dispatch right"):
                    store.claim(operation_id="claim-expire-1")

            with mock.patch("time.time", return_value=now + 2.0):
                claimed = store.claim(operation_id="claim-start-1")
            self.assertEqual(claimed["id"], "ready-idle")
            self.assertEqual(store.get("ready-idle")["state"], "running")
            self.assertEqual(store.live_stats()["revision"], rev_after_submits + 2)
            self.assertEqual(
                store.resolve_command("claim-start-1", E("GatewayClaimV1", now + 2.0)),
                E("GatewayClaimedV1", "ready-idle"),
            )
            # Even with zero queued jobs remaining, replaying claim-start-1 hits
            # durable receipt check BEFORE the read-only empty probe and refuses replay!
            with mock.patch("time.time", return_value=now + 2.0):
                with self.assertRaisesRegex(Conflict, "replayed claim grants no dispatch right"):
                    store.claim(operation_id="claim-start-1")

            with mock.patch("time.time", return_value=now + 3.0):
                store.finish("ready-idle", "succeeded", result={"done": True})

            post_work = store.live_stats()
            for _ in range(200):
                self.assertIsNone(store.claim())
                self.assertEqual(store.last_envelope[0], S("GatewayEmptyReadOnlyV1"))
            after_second_idle = store.live_stats()
            for metric in ("revision", "live_keys", "live_bytes", "physical_bytes"):
                self.assertEqual(
                    after_second_idle[metric], post_work[metric], (metric, post_work, after_second_idle)
                )

    def test_stale_sqlite_resurrection_prevented_and_missing_store_fails_closed(self):
        legacy_dir = self.base / "resurrection-guard"
        legacy_dir.mkdir(mode=0o700)
        sqlite_src = legacy_dir / "queue.sqlite3"
        cetta_dir = legacy_dir / "queue.db"

        sql = SqliteStore(sqlite_src, "alpha", conflict_cls=Conflict)
        r_old, fp_old = make_request("old-sqlite-job", payload={"era": "sqlite"})
        sql.submit(r_old, fp_old, "write")

        # 1. Unmigrated queue.sqlite3 without activated queue.db must fail closed
        with self.assertRaisesRegex(StoreUnavailable, "explicit execution_gateway.migration import"):
            Store(cetta_dir, "alpha")
        self.assertFalse(cetta_dir.exists())

        # 2. Explicit import activates queue.db and publishes durable marker
        report = Store.import_sqlite(sqlite_src, cetta_dir, "alpha")
        marker_path = Path(report["activation_marker"])
        self.assertTrue(marker_path.is_file())
        marker = json.loads(marker_path.read_text())
        self.assertEqual((marker["agent"], marker["mode"]), ("alpha", "imported-sqlite"))

        # 3. Settle old job in CeTTa and commit a new post-cutover CeTTa-only job
        with Store(cetta_dir, "alpha") as store:
            claimed = store.claim()
            self.assertEqual(claimed["id"], "old-sqlite-job")
            store.finish("old-sqlite-job", "succeeded", result={"settled_in": "cetta"})
            r_new, fp_new = make_request("post-cutover-cetta-job", payload={"era": "cetta"})
            store.submit(r_new, fp_new, "write")

        # Stale SQLite still has old-sqlite-job queued and lacks post-cutover-cetta-job
        self.assertEqual(sql.get("old-sqlite-job")["state"], "queued")
        self.assertIsNone(sql.get("post-cutover-cetta-job"))

        # 4. Simulate accidental loss of queue.db while stale queue.sqlite3 remains
        shutil.rmtree(cetta_dir)
        with self.assertRaisesRegex(StoreUnavailable, "activated CeTTa queue store is missing"):
            Store(cetta_dir, "alpha")
        self.assertFalse(cetta_dir.exists())

        # 5. Re-running import_sqlite after activation also fails closed
        with self.assertRaisesRegex(StoreUnavailable, "already activated"):
            Store.import_sqlite(sqlite_src, cetta_dir, "alpha")

        # 6. Empty directory or partial directory missing root.csl also fails closed
        cetta_dir.mkdir(mode=0o700)
        with self.assertRaisesRegex(StoreUnavailable, "missing or incomplete"):
            Store(cetta_dir, "alpha")

        # 7. Freshly initialized CeTTa store (without SQLite) also fails closed if deleted
        fresh_root = self.base / "fresh-guard"
        fresh_root.mkdir(mode=0o700)
        fresh_store = fresh_root / "queue.db"
        with Store(fresh_store, "alpha") as store:
            rf, fpf = make_request("fresh-job")
            store.submit(rf, fpf, "read")
        shutil.rmtree(fresh_store)
        with self.assertRaisesRegex(StoreUnavailable, "activated CeTTa queue store is missing"):
            Store(fresh_store, "alpha")

    def test_single_owner_boundary_blocks_concurrent_store_and_migration(self):
        store_dir = self.base / "single-owner.db"
        dummy_sqlite = self.base / "dummy.sqlite3"
        SqliteStore(dummy_sqlite, "alpha", conflict_cls=Conflict)

        with Store(store_dir, "alpha") as owner_a:
            req, fp = make_request("job-owner-1")
            owner_a.submit(req, fp, "read")
            self.assertEqual(owner_a.claim()["id"], "job-owner-1")
            self.assertFalse(owner_a.cancellation_requested("job-owner-1", durable=False))

            # 1. Second Store in same process is blocked (same or different agent)
            with self.assertRaisesRegex(StoreUnavailable, "active owner"):
                Store(store_dir, "alpha")
            with self.assertRaisesRegex(StoreUnavailable, "active owner"):
                Store(store_dir, "beta")

            # 2. Second Store in a separate child process is blocked by kernel flock
            probe_code = (
                "import sys; "
                f"sys.path.insert(0, {str(ROOT / 'src')!r}); "
                "from execution_gateway.store import Store, StoreUnavailable\n"
                "try:\n"
                f"    Store({str(store_dir)!r}, 'alpha')\n"
                "except StoreUnavailable as e:\n"
                "    print('LOCKED:' + str(e))\n"
                "    sys.exit(0)\n"
                "sys.exit(2)\n"
            )
            proc = subprocess.run(
                [sys.executable, "-c", probe_code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("LOCKED:", proc.stdout)

            # 3. Concurrent export_sqlite and import_sqlite are also blocked
            with self.assertRaisesRegex(StoreUnavailable, "active owner"):
                Store.export_sqlite(store_dir, self.base / "blocked-export.sqlite3")
            with self.assertRaisesRegex(StoreUnavailable, "active owner"):
                Store.import_sqlite(dummy_sqlite, store_dir, "alpha")

            # 4. Single-owner cancellation cache stays 100% coherent
            self.assertFalse(owner_a.cancellation_requested("job-owner-1", durable=False))
            self.assertFalse(owner_a.cancellation_requested("job-owner-1", durable=True))
            owner_a.cancel("job-owner-1")
            self.assertTrue(owner_a.cancellation_requested("job-owner-1", durable=False))
            self.assertTrue(owner_a.cancellation_requested("job-owner-1", durable=True))
            owner_a.finish("job-owner-1", "cancelled", error="cancelled")

        # 5. Once owner_a closes, lock releases cleanly: wrong agent gets Conflict,
        # export_sqlite succeeds, and alpha can reopen.
        with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
            Store(store_dir, "beta")
        exported_sql = self.base / "after-close.sqlite3"
        exp = Store.export_sqlite(store_dir, exported_sql)
        self.assertEqual(exp["jobs"], 1)
        with Store(store_dir, "alpha") as reopened:
            self.assertEqual(reopened.get("job-owner-1")["state"], "cancelled")
            self.assertTrue(reopened.cancellation_requested("job-owner-1", durable=False))

    def test_repeated_open_close_export_compaction_lifetime(self):
        store_dir = self.base / "lifetime.db"
        expected_docs: dict[str, dict] = {}
        fd_dir = Path("/proc/self/fd")

        def run_cycle(cycle: int):
            with Store(store_dir, "alpha") as store:
                r1, fp1 = make_request(f"life-{cycle}-ok", payload={"cycle": cycle, "k": 1})
                r2, fp2 = make_request(f"life-{cycle}-cancel", payload={"cycle": cycle, "k": 2})
                store.submit(r1, fp1, "read")
                store.submit(r2, fp2, "write")
                c1 = store.claim()
                self.assertEqual(c1["id"], f"life-{cycle}-ok")
                store.finish(c1["id"], "succeeded", result={"cycle": cycle})
                c2 = store.claim()
                self.assertEqual(c2["id"], f"life-{cycle}-cancel")
                store.cancel(c2["id"])
                store.finish(c2["id"], "uncertain", error="cancelled")
                stats = store.live_stats()
                self.assertGreater(stats["live_keys"], 0)
                store.compact()
                expected_docs[c1["id"]] = store.get(c1["id"])
                expected_docs[c2["id"]] = store.get(c2["id"])

            export_file = self.base / f"lifetime-export-{cycle}.sqlite3"
            report = Store.export_sqlite(store_dir, export_file)
            self.assertEqual(report["jobs"], len(expected_docs))
            sql = SqliteStore(export_file, "alpha", conflict_cls=Conflict)
            for jid, wanted in expected_docs.items():
                self.assertEqual(
                    json.dumps(sql.get(jid), sort_keys=True),
                    json.dumps(wanted, sort_keys=True),
                )
            export_file.unlink()

        # Warmup cycle before measuring steady-state open file descriptors
        run_cycle(0)
        fd_baseline = len(os.listdir(fd_dir)) if fd_dir.is_dir() else None

        for cycle in range(1, 13):
            run_cycle(cycle)
            if fd_baseline is not None:
                fd_now = len(os.listdir(fd_dir))
                self.assertLessEqual(
                    fd_now,
                    fd_baseline + 1,
                    f"FD leak detected at cycle {cycle}: baseline={fd_baseline}, now={fd_now}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

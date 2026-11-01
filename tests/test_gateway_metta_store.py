"""Phase A qualification suite for the CeTTa-backed execution-gateway queue store."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import random
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from execution_gateway import migration  # noqa: E402
from execution_gateway.cetta_worker import E, S, gateway_cetta_ref, resolve_gateway_worker  # noqa: E402
from execution_gateway.sqlite_store import SqliteStore  # noqa: E402
from execution_gateway.store import (  # noqa: E402
    Conflict,
    Store,
    StoreReplyUncertain,
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

        # Even if CETTA_BIN points to an older agent engine, gateway resolution
        # selects the frozen gateway value-worker rather than CETTA_BIN's sibling.
        fake_old_dir = self.base / "old-cognition-engine"
        fake_old_dir.mkdir(mode=0o700)
        fake_cetta = fake_old_dir / "cetta"
        fake_cetta.write_text("#!/bin/sh\nexit 0\n")
        fake_cetta.chmod(0o755)
        (fake_old_dir / "cetta-value-worker").write_text("#!/bin/sh\nexit 1\n")
        (fake_old_dir / "cetta-value-worker").chmod(0o755)
        with mock.patch.dict(os.environ, {"CETTA_BIN": str(fake_cetta)}, clear=False):
            resolved = resolve_gateway_worker()
            self.assertNotEqual(resolved.parent, fake_old_dir.resolve())

    def test_differential_parity_scripted_and_randomized_against_sqlite_oracle(self):
        native_dir = self.base / "native-queue.db"
        sqlite_file = self.base / "oracle-queue.sqlite3"
        known: set[str] = set()
        clock = 100.0

        with Store(native_dir, "alpha") as native:
            oracle = SqliteStore(sqlite_file, "alpha", conflict_cls=Conflict)

            # Owner conflict parity on reopen
            with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
                Store(native_dir, "beta")
            with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
                SqliteStore(sqlite_file, "beta", conflict_cls=Conflict)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

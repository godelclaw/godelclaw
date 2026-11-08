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

from execution_gateway.cetta_worker import (  # noqa: E402
    E,
    S,
    WorkerUnavailable,
    describe_gateway_worker,
    gateway_cetta_ref,
    resolve_gateway_worker,
    validate_gateway_worker_bundle,
)
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
        self.assertRegex(global_ref, r"^[0-9a-f]{40}$")
        self.assertRegex(gateway_cetta_ref(), r"^[0-9a-f]{40}$")

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
        with mock.patch.dict(os.environ, hostile_env, clear=False):
            os.environ.pop("METTACLAW_GATEWAY_WORKER_BIN", None)
            resolved = resolve_gateway_worker()
            self.assertEqual(resolved, verified["path"])
            with Store(self.base / "decoupled-store.db", "alpha") as store:
                req, fp = make_request("decoupled-1")
                store.submit(req, fp, "read")
                self.assertEqual(store.claim()["id"], "decoupled-1")

        self.assertEqual((ROOT / "CETTA_REF").read_text().strip(), global_ref)

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
        m_wrong["revision"] = "0" * 40 if gateway_cetta_ref() != "0" * 40 else "1" * 40
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

    def test_scripted_contract_and_randomized_trace_keep_the_cancellation_cache_coherent(self):
        native_dir = self.base / "native-queue.db"
        known: set[str] = set()
        clock = 100.0

        # Initialize once and verify the owner conflict on reopen after close
        with Store(native_dir, "alpha"):
            pass
        with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
            Store(native_dir, "beta")

        with Store(native_dir, "alpha") as native:

            def step(method: str, *args, tolerate=False, **kwargs):
                nonlocal clock
                clock += 1.0
                actual = None
                with mock.patch("time.time", return_value=clock):
                    try:
                        actual = getattr(native, method)(*args, **kwargs)
                    except Conflict:
                        if not tolerate:
                            raise
                # The in-process cancellation flag never disagrees with the durable one.
                for jid in sorted(known):
                    self.assertEqual(
                        native.cancellation_requested(jid, durable=False),
                        native.cancellation_requested(jid, durable=True),
                        f"cancellation cache incoherent on {jid} after {method}{args}",
                    )
                return actual

            def conflict(method: str, *args, **kwargs):
                with self.assertRaises(Conflict, msg=f"{method}{args}"):
                    step(method, *args, **kwargs)

            def sub(jid: str, *, effect: str = "read", deadline: float = 10_000.0, payload=None, tool="synthetic"):
                req, fp = make_request(jid, tool=tool, deadline=deadline, payload=payload)
                known.add(jid)
                return step("submit", req, fp, effect)

            # 1. Idempotent duplicate vs fingerprint conflict
            sub("j-a", payload={"v": 1})
            sub("j-a", payload={"v": 1})
            req_conflict, fp_conflict = make_request("j-a", payload={"v": 2})
            conflict("submit", req_conflict, fp_conflict, "read")

            # 2. FIFO order & running cancel + edge result=0 / outputs
            sub("j-b", effect="write", payload={"op": "w"})
            self.assertEqual(step("claim")["id"], "j-a")
            step("cancel", "j-a")
            step("cancel", "j-a")
            self.assertTrue(step("cancellation_requested", "j-a"))
            step("finish", "j-a", "succeeded", result=0, outputs={"stdout": {"bytes": 0}})
            # Finishing an already-finished or absent job raises exact Conflict
            conflict("finish", "j-a", "succeeded")
            conflict("finish", "never-existed", "failed")

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

            # 6. A 400-step seeded randomized trace
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
                    step("submit", req, fp, eff, tolerate=True)
                elif action == 2:
                    step("claim", tolerate=True)
                elif action == 3 and known:
                    step("cancel", rng.choice(sorted(known)), tolerate=True)
                elif action == 4 and known:
                    target = rng.choice(sorted(known))
                    st = rng.choice(terminal_Pool)
                    res = rng.choice(result_pool)
                    err = rng.choice(error_pool)
                    out = {"step": idx} if rng.randrange(2) else None
                    step("finish", target, st, result=res, error=err, outputs=out, tolerate=True)
                elif action == 5:
                    step("recover", tolerate=True)
                elif known:
                    step("cancellation_requested", rng.choice(sorted(known)), tolerate=True)

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

    def test_cancellation_polling_benchmark_justifies_in_process_durable_backed_flag(self):
        store_dir = self.base / "bench-cancel.db"
        req, fp = make_request("running-job")

        with Store(store_dir, "alpha") as store:
            store.submit(req, fp, "read")
            store.claim()

            # Warmup
            for _ in range(20):
                store.cancellation_requested("running-job", durable=False)
                store.cancellation_requested("running-job", durable=True)

            rounds = 400
            t0 = time.perf_counter_ns()
            for _ in range(rounds):
                self.assertFalse(store.cancellation_requested("running-job", durable=False))
            cached_us = (time.perf_counter_ns() - t0) / rounds / 1000.0

            t0 = time.perf_counter_ns()
            for _ in range(rounds):
                self.assertFalse(store.cancellation_requested("running-job", durable=True))
            durable_rpc_us = (time.perf_counter_ns() - t0) / rounds / 1000.0

            # Now cancel and verify immediate flip on both cached and durable paths
            store.cancel("running-job")
            self.assertTrue(store.cancellation_requested("running-job", durable=False))
            self.assertTrue(store.cancellation_requested("running-job", durable=True))

            # At 100 Hz (10 ms poll), < 25 us/poll is < 0.25% of one CPU core.
            self.assertLess(cached_us, 25.0, (cached_us, durable_rpc_us))
            self.assertLess(cached_us * 10, durable_rpc_us, (cached_us, durable_rpc_us))

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

    def test_unmigrated_old_queue_and_missing_store_fail_closed(self):
        legacy_dir = self.base / "resurrection-guard"
        legacy_dir.mkdir(mode=0o700)
        cetta_dir = legacy_dir / "queue.db"

        # 1. An old release's queue.sqlite3 without an activated queue.db fails closed:
        #    never a fresh empty queue beside unmigrated jobs.
        old_queue = legacy_dir / "queue.sqlite3"
        old_queue.write_bytes(b"an old release's queue")
        with self.assertRaisesRegex(StoreUnavailable, "never migrated"):
            Store(cetta_dir, "alpha")
        self.assertFalse(cetta_dir.exists())
        old_queue.unlink()

        # 2. A partial store directory (non-empty, no root.csl) fails closed
        cetta_dir.mkdir(mode=0o700)
        (cetta_dir / "stray").write_text("partial")
        with self.assertRaisesRegex(StoreUnavailable, "missing root.csl"):
            Store(cetta_dir, "alpha")

        # 3. An activated store that was deleted, or emptied, fails closed instead of
        #    reinitializing
        fresh_root = self.base / "fresh-guard"
        fresh_root.mkdir(mode=0o700)
        fresh_store = fresh_root / "queue.db"
        with Store(fresh_store, "alpha") as store:
            rf, fpf = make_request("fresh-job")
            store.submit(rf, fpf, "read")
        shutil.rmtree(fresh_store)
        with self.assertRaisesRegex(StoreUnavailable, "activated CeTTa queue store is missing"):
            Store(fresh_store, "alpha")
        fresh_store.mkdir(mode=0o700)
        with self.assertRaisesRegex(StoreUnavailable, "missing or incomplete"):
            Store(fresh_store, "alpha")

    def test_single_owner_boundary_blocks_a_concurrent_store(self):
        store_dir = self.base / "single-owner.db"

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

            # 3. Single-owner cancellation cache stays 100% coherent
            self.assertFalse(owner_a.cancellation_requested("job-owner-1", durable=False))
            self.assertFalse(owner_a.cancellation_requested("job-owner-1", durable=True))
            owner_a.cancel("job-owner-1")
            self.assertTrue(owner_a.cancellation_requested("job-owner-1", durable=False))
            self.assertTrue(owner_a.cancellation_requested("job-owner-1", durable=True))
            owner_a.finish("job-owner-1", "cancelled", error="cancelled")

        # 4. Once owner_a closes, the lock releases cleanly: the wrong agent gets
        # Conflict, and alpha can reopen.
        with self.assertRaisesRegex(Conflict, "queue belongs to another agent"):
            Store(store_dir, "beta")
        with Store(store_dir, "alpha") as reopened:
            self.assertEqual(reopened.get("job-owner-1")["state"], "cancelled")
            self.assertTrue(reopened.cancellation_requested("job-owner-1", durable=False))

    def test_repeated_open_close_compaction_lifetime(self):
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

            with Store(store_dir, "alpha") as reopened:
                for jid, wanted in expected_docs.items():
                    self.assertEqual(
                        json.dumps(reopened.get(jid), sort_keys=True),
                        json.dumps(wanted, sort_keys=True),
                    )

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

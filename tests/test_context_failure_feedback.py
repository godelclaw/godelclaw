"""Local preparation errors retain their stage, code and pending input."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import admission_frontier
import strategy_host
import synthetic_llm


class ContextFailureFeedbackTests(unittest.TestCase):
    def test_failed_preparation_cannot_settle_input_as_an_empty_success(self):
        import cognitive_health
        import helper
        import process_supervision
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.dict(os.environ, {
                 "METTACLAW_COGNITIVE_HEALTH_PATH": str(Path(temporary) / "health.json"),
                 "SYNTHETIC_EMPTY_BACKOFF": "0"}), \
             mock.patch("provider_rejection.blocked", return_value=False), \
             mock.patch.object(process_supervision, "progress"), \
             mock.patch.object(cognitive_health, "_turn_outcome", "none"), \
             mock.patch.object(helper, "_turn_commit", ((), None)), \
             contextlib.redirect_stderr(io.StringIO()):
            cognitive_health.expect_turn("godelclaw", now=100)
            self.assertEqual(strategy_host.preparation_failed("context-compaction-required"), "()")
            self.assertEqual(cognitive_health.turn_succeeded(), 0)
            helper.turn_commit_stage(cognitive_health.turn_succeeded(), 1, 1)
            self.assertEqual(helper.turn_commit_ready(), 0)
            import json
            health = json.loads((Path(temporary) / "health.json").read_text())
            self.assertEqual(health["obligation_since"], 100)
            self.assertEqual(health["last_failure_type"], "context-compaction-required")

    def test_typed_code_survives_the_actual_health_reporting_path(self):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"SYNTHETIC_EMPTY_BACKOFF": "0"}), \
             mock.patch("provider_rejection.blocked", return_value=False), \
             mock.patch.object(synthetic_llm.cognitive_health, "turn_failed") as failed, \
             contextlib.redirect_stderr(stderr):
            result = strategy_host.preparation_failed("context-compaction-required")
        self.assertEqual(result, "()")
        failed.assert_called_once_with("context-compaction-required", retry_after=0.0)
        self.assertIn("[context] preparation withheld:", stderr.getvalue())
        self.assertNotIn("failed provider turn", stderr.getvalue())

    def test_arbitrary_exception_text_is_not_trusted_as_a_reason_code(self):
        error = RuntimeError("context-budget fixture-secret")
        self.assertEqual(admission_frontier.failure_code(error), "RuntimeError")

    def test_malformed_context_reason_is_replaced_without_reflecting_it(self):
        for text in ["context-budget https://fixture-secret.test", "context-" + "x" * 100,
                     "context-private\nAuthorization: fixture-secret"]:
            error = admission_frontier.ContextPreparationFailure(text)
            self.assertEqual(admission_frontier.failure_code(error), "context-preparation-failed")
            self.assertNotIn("fixture-secret", str(error))

    def test_uncertain_conversation_rows_fails_closed_once_with_no_in_turn_repeat(self):
        """C2 + C3 regression test:
        When the ledger answers `StoreRemoteUncertain` for `conversation-rows`, context preparation
        inside a turn (`turnRequest` -> `addition-protected-request` -> `addition-model-call`) fails
        closed with `(ContextPreparationFailed "context-conversation-read-failed")`, emits one withheld
        report, and never repeats inside the turn (nor does `turnRequest` repeat on backtracking).
        """
        import json
        import pwd
        import socket
        import subprocess
        import time
        from contextlib import closing

        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "tests"))
        import test_command_dispatch_engine as dispatch
        from native_ledger_client import NativeRunner
        from observed_strategy import Ledger
        import task_context
        import input_ack

        engine_env = os.environ.get("CETTA_BIN")
        ENGINE = Path(engine_env) if engine_env else None
        WORKER = Path(
            os.environ.get("CETTA_VALUE_WORKER_BIN")
            or (ENGINE.with_name("cetta-value-worker") if ENGINE else "")
        )
        if not ENGINE or not ENGINE.is_file() or not WORKER.is_file():
            self.skipTest("set CETTA_BIN to the cetta-ours engine (cetta-value-worker beside it)")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            store_dir = tmp_dir / "store"
            sock_path = tmp_dir / "s.sock"
            if len(str(sock_path)) > 100:
                sock_dir = Path(tempfile.mkdtemp(dir="/tmp"))
                self.addCleanup(lambda: __import__("shutil").rmtree(sock_dir, ignore_errors=True))
                sock_path = sock_dir / "s.sock"

            work = tmp_dir / "repo"
            (work / ".git").mkdir(parents=True)
            for name in (
                "src",
                "channels",
                "modes",
                "identity",
                "lib_nal.metta",
                "lib_nal7.metta",
            ):
                os.symlink(root / name, work / name)
            (work / "cetta_bootstrap.metta").write_text(
                (root / "cetta_bootstrap.metta").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (work / "config").mkdir()
            (work / "config" / "channel.metta").write_text(
                "(= (configureChannel) (configure commchannel telegram))\n",
                encoding="utf-8",
            )
            (work / "memory").mkdir()
            soul_path = work / "memory" / "soul.md"
            soul_path.write_text("Test soul\n", encoding="utf-8")
            lib_lines = [
                line
                for line in (root / "lib_mettaclaw.metta").read_text(encoding="utf-8").splitlines()
                if "petta_lib_chromadb" not in line
            ]
            (work / "lib_mettaclaw.metta").write_text("\n".join(lib_lines) + "\n", encoding="utf-8")

            stub_server = tmp_dir / "stub_input_ledger_server.metta"
            server_src = (root / "src/input_ledger_server.metta").read_text(encoding="utf-8")
            old_call = (
                "(= (ledger-server:call (ServiceCallV1 (DbConfigurationV1 $owner $identity $incarnation $kind $artifacts) $peer $source\n"
                "    (LedgerCallV1 $verb $arguments)))\n"
                "  (if (== $kind directory)\n"
                "      (LedgerAnswersV1 (collapse (input-ledger:call $owner $verb $arguments)))\n"
                "      (DbRefusedV1 read-only)))"
            )
            self.assertIn(old_call, server_src)
            server_src = server_src.replace(
                "!(import! &self ./input_ledger)",
                f"!(import! &self {json.dumps(str(root / 'src/input_ledger.metta'))})",
            ).replace(
                old_call,
                "(= (ledger-server:call (ServiceCallV1 (DbConfigurationV1 $owner $identity $incarnation $kind $artifacts) $peer $source\n"
                "    (LedgerCallV1 $verb $arguments)))\n"
                '  (if (== $verb "conversation-rows")\n'
                "      (let $_ (println! STUB_CONVERSATION_ROWS)\n"
                '        (StoreRemoteUncertain "socket" "reply is not a value response"))\n'
                "      (if (== $kind directory)\n"
                "          (LedgerAnswersV1 (collapse (input-ledger:call $owner $verb $arguments)))\n"
                "          (DbRefusedV1 read-only))))",
            )
            stub_server.write_text(server_src, encoding="utf-8")

            server_env = dict(os.environ, CETTA_DB_SOURCE=str(store_dir), CETTA_DB_SOCKET=str(sock_path))
            server_log = open(tmp_dir / "server.log", "wb")
            with server_log:
                proc = subprocess.Popen(
                    [
                        str(WORKER),
                        "--value-bytes",
                        str(input_ack.LEDGER_VALUE_BYTES),
                        "--serve",
                        "--lang",
                        "petta",
                        str(stub_server),
                        "db-server:configuration",
                    ],
                    cwd=work,
                    env=server_env,
                    stdin=subprocess.DEVNULL,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                )
            self.addCleanup(lambda: (proc.kill(), proc.wait()) if proc.poll() is None else None)

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    self.fail("stub server exited: " + (tmp_dir / "server.log").read_text( errors="replace"))
                try:
                    with closing(socket.socket(socket.AF_UNIX)) as s:
                        s.settimeout(1)
                        s.connect(str(sock_path))
                    break
                except OSError:
                    time.sleep(0.02)
            else:
                self.fail("stub server did not listen")

            with mock.patch.dict(os.environ, {input_ack.LEDGER_SOCKET: str(sock_path)}):
                runner = NativeRunner.create(tmp_dir, sock_path)
                store = Ledger(runner.path)
                store.accept("guide", "Check ledger resilience", kind="message")
                task_context.begin(store, "guide", "operator-event", None, "private")
                task_context.focus(store, "guide")
                input_ack.close_ledger()

            real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            with mock.patch.dict(
                os.environ,
                {
                    "PETTA_ROOT": os.environ.get("PETTA_ROOT", str(real_home / "repos" / "PeTTa")),
                    "PETTA_PY_ENV": os.environ.get(
                        "PETTA_PY_ENV", str(real_home / "miniforge3" / "envs" / "petta")
                    ),
                },
            ):
                harness = dispatch.CommandDispatchEngineTest()
                env = harness._cetta_env()
            env.setdefault("PETTA_ROOT", str(real_home / "repos" / "PeTTa"))
            env.setdefault("PETTA_PY_ENV", str(real_home / "miniforge3" / "envs" / "petta"))
            (tmp_dir / "home").mkdir(exist_ok=True)
            env.update(
                HOME=str(tmp_dir / "home"),
                TMPDIR=str(tmp_dir),
                METTACLAW_OBSERVED_STRATEGY="1",
                METTACLAW_STRATEGY_PATH=str(runner.path),
                METTACLAW_INPUT_LEDGER_SOCKET=str(sock_path),
                METTACLAW_ENGINE_STATE_PATH=str(tmp_dir / "engine"),
                METTACLAW_COGNITIVE_HEALTH_PATH=str(tmp_dir / "health.json"),
                METTACLAW_CONTEXT_WINDOW_TOKENS="100000",
                METTACLAW_HISTORY_PATH=str(tmp_dir / "history"),
                METTACLAW_TELEGRAM_LOG_PATH=str(tmp_dir / "updates.jsonl"),
                METTACLAW_LOOP_MODE_PATH=str(tmp_dir / "loop-mode.json"),
                METTACLAW_WORKING_SET_PATH=str(tmp_dir / "working-set.json"),
                METTACLAW_PROMPT_PATH=str(soul_path),
                SYNTHETIC_EMPTY_BACKOFF="0",
            )

            probe = work / "probe.metta"
            probe.write_text(
                "!(import! &self ./cetta_bootstrap.metta)\n"
                "!(initMemory)\n"
                '!(py-call (strategy_host.focus_request "guide"))\n'
                "!(let* (($periphery (process-periphery (initLoop)))\n"
                '        ($request (turnRequest $periphery (NEW_ACTIVITY: "Check ledger resilience")))\n'
                "        ($send (addition-protected-request (coding-request-prompt $request) (maxOutputToken)))\n"
                "        ($_ (addition-request-observation $send))\n"
                "        ($rmode (policy-reasoning (loop-active-policy $periphery) (reasoningMode)))\n"
                "        ($respi (addition-model-call (LLM) (maxOutputToken) $rmode $send (policy-idle-wait (loop-active-policy $periphery)))))\n"
                "   (println! (TURN_OUTCOME $send $respi)))\n"
                "!(println! (BACKTRACK_CHECK (collapse (let* (($periphery (initialPeriphery))\n"
                '                                             ($req (turnRequest $periphery (NEW_ACTIVITY: "backtrack")))\n'
                "                                             ($_ (println! BACKTRACK_OBS_RAN))\n"
                "                                             ($_ (empty)))\n"
                "                                        $req))))\n",
                encoding="utf-8",
            )

            done = subprocess.run(
                [str(ENGINE), "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn(
                '(TURN_OUTCOME (ContextPreparationFailed "context-conversation-read-failed") "()")',
                done.stdout,
                done.stdout + done.stderr,
            )
            self.assertIn("(BACKTRACK_CHECK ())", done.stdout, done.stdout + done.stderr)
            self.assertEqual(
                done.stderr.count("[context] preparation withheld: context-conversation-read-failed"),
                1,
                done.stderr,
            )
            # One preparation -> one ledger-client:call ("conversation-rows") -> 3 session retries, never repeated in-turn.
            server_out = (tmp_dir / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()
            conv_calls = [line.strip() for line in server_out if "STUB_CONVERSATION_ROWS" in line]
            self.assertEqual(conv_calls, ["STUB_CONVERSATION_ROWS"] * 3, server_out)
            # Even when the consumer after turnRequest reduces to (empty), turnRequest runs once and never loops.
            obs_calls = [line.strip() for line in done.stdout.splitlines() if line.strip() == "BACKTRACK_OBS_RAN"]
            self.assertEqual(obs_calls, ["BACKTRACK_OBS_RAN"])

    def _run_cetta_probe(self, body: str) -> str:
        import pwd
        import subprocess

        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "tests"))
        import test_command_dispatch_engine as dispatch

        engine_env = os.environ.get("CETTA_BIN")
        engine = Path(engine_env) if engine_env else None
        if not engine or not engine.is_file():
            self.skipTest("set CETTA_BIN to the cetta-ours engine")

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            work = tmp_dir / "repo"
            (work / ".git").mkdir(parents=True)
            for name in ("src", "channels", "modes", "identity", "lib_nal.metta", "lib_nal7.metta"):
                os.symlink(root / name, work / name)
            with mock.patch.dict(
                os.environ,
                {
                    "PETTA_ROOT": os.environ.get("PETTA_ROOT", str(real_home / "repos" / "PeTTa")),
                    "PETTA_PY_ENV": os.environ.get(
                        "PETTA_PY_ENV", str(real_home / "miniforge3" / "envs" / "petta")
                    ),
                },
            ):
                harness = dispatch.CommandDispatchEngineTest()
                env = harness._cetta_env()
            env.setdefault("PETTA_ROOT", str(real_home / "repos" / "PeTTa"))
            env.setdefault("PETTA_PY_ENV", str(real_home / "miniforge3" / "envs" / "petta"))
            (tmp_dir / "home").mkdir(exist_ok=True)
            env.update(
                HOME=str(tmp_dir / "home"),
                TMPDIR=str(tmp_dir),
                METTACLAW_OBSERVED_STRATEGY="1",
                METTACLAW_INPUT_LEDGER_SOCKET=str(tmp_dir / "stub.sock"),
                METTACLAW_STRATEGY_PATH=str(tmp_dir / "strategy"),
                METTACLAW_ENGINE_STATE_PATH=str(tmp_dir / "engine"),
                METTACLAW_COGNITIVE_HEALTH_PATH=str(tmp_dir / "health.json"),
                METTACLAW_LOOP_MODE_PATH=str(tmp_dir / "loop-mode.json"),
                METTACLAW_WORKING_SET_PATH=str(tmp_dir / "working-set.json"),
            )
            probe = work / "unit_probe.metta"
            probe.write_text(body, encoding="utf-8")
            done = subprocess.run(
                [str(engine), "--lang", "petta", "--import-mode", "ancestor-walk", str(probe)],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return done.stdout

    def test_state_single_prepared_converts_empty_and_error_from_context_prepare(self):
        """When `context:prepare` yields no result (`(empty)`) or yields an `Error`,
        `state:prepare-context` returns exactly one `(ContextPreparationFailed "context-preparation-failed")`."""
        out = self._run_cetta_probe(
            "!(import! &self ./src/helper.py)\n"
            "!(import! &self ./src/state_io.metta)\n"
            "!(match &self (= (context:prepare $a $b $c $d $e $f $g) $body)\n"
            "   (remove-atom &self (= (context:prepare $a $b $c $d $e $f $g) $body)))\n"
            "(= (context:prepare $base $focus $trigger $capacity $model $tokens $effort)\n"
            '   (if (== $tokens 111) (empty) (Error stub-prepare "boom")))\n'
            '!(let $r1 (collapse (state:prepare-context "p" 111))\n'
            "   (println! (EMPTY_PREP (size-atom $r1) $r1)))\n"
            '!(let $r2 (collapse (state:prepare-context "p" 222))\n'
            "   (println! (ERROR_PREP (size-atom $r2) $r2)))\n"
        )
        self.assertIn('(EMPTY_PREP 1 ((ContextPreparationFailed "context-preparation-failed")))', out)
        self.assertIn('(ERROR_PREP 1 ((ContextPreparationFailed "context-preparation-failed")))', out)

    def test_store_remote_failed_limit_pages_reads_and_never_pages_writes(self):
        """When a session outcome is `(Error ledger-client:call (unanswered ((StoreRemoteFailed limit))))`
        (the client decode-capacity path), `ledger-client:call` pages a pageable read and never pages a write."""
        out = self._run_cetta_probe(
            "!(import! &self ./src/ledger_client.metta)\n"
            "!(match &self (= (ledger-client:attempt $s $route $req) $body)\n"
            "   (remove-atom &self (= (ledger-client:attempt $s $route $req) $body)))\n"
            "(= (ledger-client:attempt $s $route $req)\n"
            "   (if (== $route LedgerCall)\n"
            "       (let $_ (println! (CALL_ATTEMPT $req))\n"
            "         (Error ledger-client:call (unanswered ((StoreRemoteFailed limit)))))\n"
            "       (let $_ (println! (PAGE_ATTEMPT $req))\n"
            '         (page last (("r1" "active"))))))\n'
            '!(println! (READ_OUT (ledger-client:call "stub-sock" "request-rows" (all))))\n'
            '!(println! (WRITE_OUT (ledger-client:call "stub-sock" "ensure" ())))\n'
        )
        lines = [line.strip() for line in out.splitlines()]
        self.assertIn('(READ_OUT (("r1" "active")))', lines)
        self.assertIn(
            "(WRITE_OUT (Error ledger-client:call (unanswered ((StoreRemoteFailed limit)))))",
            lines,
        )
        call_attempts = [line for line in lines if line.startswith("(CALL_ATTEMPT ")]
        page_attempts = [line for line in lines if line.startswith("(PAGE_ATTEMPT ")]
        self.assertEqual(
            call_attempts,
            [
                '(CALL_ATTEMPT (LedgerCallV1 "request-rows" (all)))',
                '(CALL_ATTEMPT (LedgerCallV1 "ensure" ()))',
            ],
        )
        self.assertEqual(len(page_attempts), 1, page_attempts)

    def test_native_conversation_projection_rejects_non_conversation_rows_answers(self):
        """When `state:ledger` for `conversation-rows` returns an `Error` or another atom
        that is not `(conversation-rows ...)`, `task:native-conversation-projection` returns
        `(ContextPreparationFailed "context-conversation-read-failed")`, never the answer itself."""
        out = self._run_cetta_probe(
            "!(import! &self ./src/helper.py)\n"
            "!(import! &self ./src/context_compaction.metta)\n"
            "!(match &self (= (state:ledger $v $a) $body)\n"
            "   (remove-atom &self (= (state:ledger $v $a) $body)))\n"
            '(= (state:ledger "meta-get" ("owner")) "\\"test-owner\\"")\n'
            '(= (state:ledger "task-family" ($req)) (family $req ($req)))\n'
            '(= (state:ledger "conversation-rows" ($req $off $lim))\n'
            '   (if (== $req "err-req")\n'
            '       (Error ledger-client:call (unanswered (StoreRemoteUncertain "socket" "bad reply")))\n'
            "       (unexpected-ledger-atom 42)))\n"
            '!(println! (PROJ_ERR (task:native-conversation-projection "err-req" "operator-event" 0 4000)))\n'
            '!(println! (PROJ_ATOM (task:native-conversation-projection "atom-req" "operator-event" 0 4000)))\n'
        )
        self.assertIn('(PROJ_ERR (ContextPreparationFailed "context-conversation-read-failed"))', out)
        self.assertIn('(PROJ_ATOM (ContextPreparationFailed "context-conversation-read-failed"))', out)

    def test_state_json_set_on_non_object_returns_value_with_count_one(self):
        """`state:json-set` on a non-object (an `Error`) returns that value
        and does not reduce to `(empty)` (result count is 1 via `collapse`)."""
        out = self._run_cetta_probe(
            "!(import! &self ./src/helper.py)\n"
            "!(import! &self ./src/state_cetta.metta)\n"
            '!(let $res (collapse (state:json-set (Error ledger-client:call (unanswered "bad")) "transcript" (JsonArray ())))\n'
            "   (println! (JSON_SET_OUT (size-atom $res) $res)))\n"
        )
        self.assertIn('(JSON_SET_OUT 1 ((Error ledger-client:call (unanswered "bad"))))', out)


if __name__ == "__main__":
    unittest.main()

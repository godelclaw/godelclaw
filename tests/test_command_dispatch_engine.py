"""The real PeTTa boundary must not eagerly execute a command argument."""

import json
import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CommandDispatchEngineTest(unittest.TestCase):
    def _petta_root(self):
        return pathlib.Path(os.environ.get(
            "PETTA_ROOT", pathlib.Path.home() / "repos" / "PeTTa"))

    def _cetta_binary(self):
        return pathlib.Path(os.environ.get(
            "CETTA_BIN",
            pathlib.Path.home() / "repos" / "CeTTa-runtime" / "cetta"))

    def _cetta_env(self):
        env = dict(os.environ)
        # PeTTa's lib_llm builds an OpenAI client when it is imported, and a
        # client without a key raises, which ends the program. No request is
        # made here, so any key will do.
        env.setdefault("OPENAI_API_KEY", "test-only-unused")
        python_env = pathlib.Path(os.environ.get(
            "PETTA_PY_ENV",
            pathlib.Path.home() / "miniforge3" / "envs" / "petta"))
        env["PYTHONPATH"] = os.pathsep.join((
            os.fspath(ROOT / "src"), os.fspath(ROOT / "channels"),
            os.fspath(ROOT / "repos" / "petta_lib_chromadb"),
        ))
        if python_env.is_dir():
            env["PATH"] = os.pathsep.join((
                os.fspath(python_env / "bin"), env.get("PATH", ""),
            ))
            env["LD_LIBRARY_PATH"] = os.pathsep.join((
                os.fspath(python_env / "lib"),
                env.get("LD_LIBRARY_PATH", ""),
            ))
            env["PYTHONHOME"] = os.fspath(python_env)
            env["PYTHONNOUSERSITE"] = "1"
        return env

    def test_cetta_keeps_mode_read_fast_while_control_lane_is_blocked(self):
        cetta = self._cetta_binary()
        if not cetta.is_file():
            self.skipTest("CeTTa executable is unavailable")
        env = self._cetta_env()
        env["PYTHONPATH"] = os.pathsep.join((
            os.fspath(ROOT / "tests"), env["PYTHONPATH"],
        ))
        result = subprocess.run(
            [os.fspath(cetta), "--lang", "petta", "--import-mode",
             "ancestor-walk",
             os.fspath(ROOT / "tests" / "cetta_fast_path_probe.metta")],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertRegex(result.stdout, r"CETTA_FAST_PATH_OK:[0-9]+ms")
        self.assertNotIn("CETTA_FAST_PATH_FAIL", result.stdout)

    def test_cetta_live_launch_prepares_structured_request(self):
        """The live launch's program prepares a request, each equation once.

        The bootstrap imports the whole program; loading its modules a second
        time would double every recursive answer until the turn never ends.
        """
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (
                petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            probe = directory / "request.metta"
            probe.write_text(
                "(= (assertLive $A $B) (assert (== $A $B)))\n"
                "!(let* (($request (coding-prepare-request coding legacy "
                "system activity advertised)) "
                "($prompt (coding-request-prompt $request)) "
                "($observations (coding-request-observations $request)) "
                "($commands (model-response-commands "
                "(model-response-read "
                "(model-response-envelope protocol-failure)))) "
                "($before (coordinate loops 12 "
                "(coordinate sleep-interval 1 coordinates-empty))) "
                "($failed (applyProtocolPacing 1 60 $before no-commands)) "
                "($answered (applyProtocolPacing 1 60 $failed "
                "commands-parsed)) "
                "($_ (assertLive (== $prompt legacy) False)) "
                "($_ (assertLive $observations \"\")) "
                "($_ (assertLive $commands ())) "
                "($_ (assertLive (loop-budget $failed) 12)) "
                "($_ (assertLive (loop-sleep $failed) 60)) "
                "($_ (assertLive (loop-budget $answered) 12)) "
                "($_ (assertLive (loop-sleep $answered) 1)) "
                "($_ (assertLive (collapse (loop-budget $failed)) (12)))) "
                "(println! CETTA_LIVE_REQUEST_OK))\n",
                encoding="utf-8",
            )
            files = [ROOT / "cetta_bootstrap.metta", probe]
            environment = self._cetta_env()
            environment.update({
                "METTACLAW_HISTORY_PATH": os.fspath(
                    directory / "history.metta"),
                "METTACLAW_TELEGRAM_LOG_PATH": os.fspath(
                    directory / "updates.jsonl"),
            })
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", *(os.fspath(path) for path in files)],
                cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIn("CETTA_LIVE_REQUEST_OK", result.stdout)
        self.assertNotIn("coding-request-observations (coding-prepare-request",
                         result.stdout)

    def test_cetta_bridges_nested_python_from_imported_prolog(self):
        """Pin the former live-loop ``PettaSearchHostError`` boundary."""

        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (
                petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            fixture = directory / "nested_python.pl"
            fixture.write_text(
                "nested_python(Value, Result) :-\n"
                "    'py-call'(['builtins.str', Value], Result).\n",
                encoding="utf-8",
            )
            probe = directory / "nested_python.metta"
            probe.write_text(
                "!(import_prolog_functions_from_file %s (nested_python))\n"
                "!(println! (NESTED_PYTHON_OK "
                "(nested_python \"agent — bridge\")))\n"
                % json.dumps(os.fspath(fixture)),
                encoding="utf-8",
            )
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk",
                 os.fspath(petta / "lib" / "lib_import.metta"),
                 os.fspath(probe)],
                cwd=ROOT, env=self._cetta_env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("(NESTED_PYTHON_OK agent — bridge)", result.stdout)
        self.assertNotIn("PettaSearchHostError", result.stdout + result.stderr)

    def test_petta_executes_quoted_batch_once(self):
        petta = self._petta_root()
        if not (petta / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "effect.log"
            probe = directory / "probe.metta"
            command = "printf 'effect-once\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            response = "((shell %s))" % json.dumps(command)
            probe.write_text(
                "!(import! &self (library lib_import))\n"
                "!(import! &self ./src/skills)\n"
                "!(println! (run-command-batch-once 424242 5 "
                "(quote %s)))\n" % response,
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update({
                "METTACLAW_SKIP_INITIALIZE": "1",
                "METTACLAW_ENGINE": "petta",
                "METTACLAW_ENGINE_STATE_PATH": os.fspath(
                    directory / "engine-selection"),
                "METTACLAW_EFFECT_RECEIPT_PATH": os.fspath(
                    directory / "effect-receipts.jsonl"),
                "METTACLAW_STIMULUS_FRONTIER_PATH": os.fspath(
                    directory / "stimulus-frontier"),
            })
            (directory / "stimulus-frontier").write_text(
                "1 424242 0 0\n", encoding="ascii"
            )
            result = subprocess.run(
                ["./run.sh", os.fspath(probe)], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue(
                effect.is_file(),
                "Evaluator returned success without dispatching the shell effect:\n"
                f"stdout:\n{result.stdout[-4000:]}\n"
                f"stderr:\n{result.stderr[-2000:]}",
            )
            self.assertEqual(effect.read_text(encoding="utf-8"),
                             "effect-once\n")
            self.assertIn("COMMAND_RETURN:", result.stdout)
            self.assertNotRegex(result.stdout, r"\$V[0-9]+")
            receipts = [json.loads(line) for line in
                        (directory / "effect-receipts.jsonl").read_text(
                            encoding="utf-8").splitlines()]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["turn"], 424242)
            self.assertEqual(receipts[0]["disposition"], "returned")
            self.assertIn("shell", receipts[0]["command"])

    def test_cetta_grounds_every_record_in_a_mixed_command_batch(self):
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "effect.log"
            probe = directory / "probe.metta"
            command = "printf 'effect-once\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            probe.write_text(
                "!(println! (run-command-batch-once 424243 5 (quote ("
                "(py-call (str \"agent — a complete Unicode message\")) "
                "(shell %s) "
                "(py-call (str \"second complete result\"))"
                "))))\n" % json.dumps(command),
                encoding="utf-8",
            )
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", os.fspath(petta / "lib" / "lib_import.metta"),
                 os.fspath(ROOT / "src" / "skills.metta"), os.fspath(probe)],
                cwd=ROOT, env=self._cetta_env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue(
                effect.is_file(),
                "CeTTa returned success without dispatching the shell effect:\n"
                f"stdout:\n{result.stdout[-4000:]}\n"
                f"stderr:\n{result.stderr[-2000:]}",
            )
            self.assertEqual(effect.read_text(encoding="utf-8"),
                             "effect-once\n")
            self.assertEqual(result.stdout.count("COMMAND_RETURN:"), 3,
                             result.stdout)
            self.assertIn("agent — a complete Unicode message", result.stdout)
            self.assertIn("second complete result", result.stdout)
            self.assertNotRegex(result.stdout, r"\$V[0-9]+")

    def test_cetta_preserves_a_multiline_unicode_send_payload(self):
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            probe = directory / "probe.metta"
            payload = "agent — first complete line\n⋄⟨Cn:.9 Sp:.4⟩"
            probe.write_text(
                "!(println! (REPLACED (string-replace %s \"\\n\" \"\\\\n\")))\n"
                % json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", os.fspath(petta / "lib" / "lib_import.metta"),
                 os.fspath(ROOT / "src" / "utils.metta"), os.fspath(probe)],
                cwd=ROOT, env=self._cetta_env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn("agent — first complete line", result.stdout)
            self.assertIn("⋄⟨Cn:.9 Sp:.4⟩", result.stdout)
            self.assertNotRegex(result.stdout, r"\$V[0-9]+")

    def test_petta_runs_timed_continuation_once(self):
        petta = self._petta_root()
        if not (petta / "run.sh").is_file():
            self.skipTest("PeTTa checkout is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "continued.log"
            probe = directory / "continuation.metta"
            command = "printf 'continued-once\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            probe.write_text(
                "!(import! &self (library lib_import))\n"
                "!(import! &self ./lib_mettaclaw)\n"
                "!(let* (($p (coordinate iteration 7 "
                "(coordinate loops 0 (coordinate sleep-interval 1 "
                "(coordinate last-results before "
                "(coordinate last-heartbeat 9 "
                "(coordinate active-policy godelclaw "
                "(coordinate autonomous-ready 0 "
                "(coordinate pending-continuation "
                "(continuation-value (shell %s)) "
                "coordinates-empty))))))))) "
                "($resumed (applyTimedContinuation $p 1)) "
                "($_ (assert (== (loop-budget $resumed) 2))) "
                "($_ (assert (== (loop-pending-continuation $resumed) "
                "no-continuation)))) (println! REST_CONTINUATION_OK))\n"
                % json.dumps(command),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update({
                "METTACLAW_SKIP_INITIALIZE": "1",
                "METTACLAW_ENGINE": "petta",
                "METTACLAW_ENGINE_STATE_PATH": os.fspath(
                    directory / "engine-selection"),
            })
            result = subprocess.run(
                ["./run.sh", os.fspath(probe)], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn("REST_CONTINUATION_OK", result.stdout)
            self.assertEqual(effect.read_text(encoding="utf-8"),
                             "continued-once\n")

    def test_cetta_executes_quoted_batch_once(self):
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "effect.log"
            probe = directory / "probe.metta"
            command = "printf 'effect-once\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            probe.write_text(
                "!(println! (run-command-batch-once 424242 5 "
                "(quote ((shell %s)))))\n" % json.dumps(command),
                encoding="utf-8",
            )
            receipt_path = directory / "effect-receipts.jsonl"
            environment = self._cetta_env()
            environment["METTACLAW_EFFECT_RECEIPT_PATH"] = os.fspath(
                receipt_path)
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", os.fspath(petta / "lib" / "lib_import.metta"),
                 os.fspath(ROOT / "src" / "skills.metta"), os.fspath(probe)],
                cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue(
                effect.is_file(),
                "CeTTa returned success without dispatching the shell effect:\n"
                f"stdout:\n{result.stdout[-4000:]}\n"
                f"stderr:\n{result.stderr[-2000:]}",
            )
            self.assertEqual(effect.read_text(encoding="utf-8"),
                             "effect-once\n")
            receipts = [json.loads(line) for line in
                        receipt_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["turn"], 424242)
            self.assertEqual(receipts[0]["disposition"], "returned")
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)

    def test_cetta_queues_new_stimulus_without_discarding_emitted_call(self):
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "effect.log"
            frontier = directory / "stimulus-frontier"
            probe = directory / "probe.metta"
            command = "printf 'must-not-run\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            frontier.write_text("1 424244 3 4\n", encoding="ascii")
            probe.write_text(
                "!(println! (run-command-batch-once 424244 5 "
                "(quote ((shell %s)))))\n" % json.dumps(command),
                encoding="utf-8",
            )
            environment = self._cetta_env()
            environment["METTACLAW_STIMULUS_FRONTIER_PATH"] = os.fspath(
                frontier
            )
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", os.fspath(petta / "lib" / "lib_import.metta"),
                 os.fspath(ROOT / "src" / "skills.metta"), os.fspath(probe)],
                cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertTrue(effect.exists(), result.stdout)
            self.assertNotIn("COMMAND_BATCH_INTERRUPTED:", result.stdout)

    def test_plain_rest_command_survives_restart_and_runs_once(self):
        if not self._cetta_binary().is_file():
            self.skipTest('CeTTa unavailable')
        with tempfile.TemporaryDirectory() as td:
            env=self._cetta_env()
            env.update(METTACLAW_ENGINE='cetta',CETTA_BIN=str(self._cetta_binary()),
                METTACLAW_OBSERVED_STRATEGY='0',METTACLAW_SKIP_INITIALIZE='1',
                METTACLAW_WORKING_SET_PATH=td+'/working.json',METTACLAW_TIMER_PROBE=td+'/effect')
            p=subprocess.run(['./run.sh','tests/rest_timer_probe.metta'],cwd=ROOT,
                             env=env,capture_output=True,text=True,timeout=40)
            self.assertEqual(p.returncode,0,p.stderr[-1500:])
            self.assertIn('REST_TIMER_RESTART_OK',p.stdout)
            self.assertIn('NOT_EAGER',p.stdout)
            self.assertEqual(pathlib.Path(td+'/effect').read_text(),'scheduled')

    def test_cetta_runs_timed_continuation(self):
        petta = self._petta_root()
        cetta = self._cetta_binary()
        if not cetta.is_file() or not (petta / "lib" / "lib_import.metta").is_file():
            self.skipTest("CeTTa/PeTTa checkouts are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            directory = pathlib.Path(directory)
            effect = directory / "continued.log"
            probe = directory / "continuation.metta"
            command = "printf 'continued-once\\n' >> %s" % shlex.quote(
                os.fspath(effect))
            probe.write_text(
                "!(let* (($p (coordinate iteration 7 "
                "(coordinate loops 0 (coordinate sleep-interval 1 "
                "(coordinate last-results before "
                "(coordinate last-heartbeat 9 "
                "(coordinate active-policy godelclaw "
                "(coordinate autonomous-ready 0 "
                "(coordinate pending-continuation "
                "(continuation-value (shell %s)) "
                "coordinates-empty))))))))) "
                "($resumed (applyTimedContinuation $p 1)) "
                "($_ (assert (== (loop-budget $resumed) 2))) "
                "($_ (assert (== (loop-pending-continuation $resumed) "
                "no-continuation)))) (println! REST_CONTINUATION_OK))\n"
                % json.dumps(command),
                encoding="utf-8",
            )
            files = [
                petta / "lib" / "lib_import.metta",
                petta / "lib" / "lib_patrick.metta",
                petta / "lib" / "lib_llm.metta",
                petta / "lib" / "lib_vector.metta",
                petta / "lib" / "lib_combinatorics.metta",
                ROOT / "lib_nal.metta", ROOT / "lib_nal7.metta",
                ROOT / "src" / "utils.metta",
                ROOT / "src" / "channels.metta",
                ROOT / "src" / "weak_process_core.metta",
                ROOT / "src" / "open_assemblage.metta",
                ROOT / "src" / "loop_policy.metta",
                ROOT / "src" / "iter_process_policy.metta",
                ROOT / "src" / "metta_coding_policy.metta",
                ROOT / "src" / "skills.metta",
                ROOT / "src" / "command_pipeline.metta",
                ROOT / "src" / "turn_additions.metta",
                ROOT / "src" / "memory.metta",
                ROOT / "src" / "attention_graph.metta",
                ROOT / "src" / "loop.metta", probe,
            ]
            result = subprocess.run(
                [os.fspath(cetta), "--lang", "petta", "--import-mode",
                 "ancestor-walk", *(os.fspath(path) for path in files)],
                cwd=ROOT, env=self._cetta_env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn("REST_CONTINUATION_OK", result.stdout)
            self.assertEqual(effect.read_text(encoding="utf-8"),
                             "continued-once\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)

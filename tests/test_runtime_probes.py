"""Require semantic witnesses from the real PeTTa runtime probes."""

import os
from isolated_env import isolated_environ  # noqa: E402
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
ANSI_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Discovery happens before another test can install its fake HOME.  Child
# environments still inherit that isolated HOME; these are code locations, not
# state locations, and must not move with it.
_CAPTURED_HOME = pathlib.Path.home()
_CAPTURED_PATHS = {
    name: os.environ.get(name)
    for name in ("PETTA_ROOT", "PETTA_PY_ENV", "CETTA_BIN")
}


class RuntimeDiscoveryError(RuntimeError):
    pass


def _resolved_path(value):
    return pathlib.Path(value).expanduser().resolve()


def _runtime_paths(environ=None, home=None):
    source = _CAPTURED_PATHS if environ is None else environ
    base = _CAPTURED_HOME if home is None else pathlib.Path(home)
    python = source.get("PETTA_PY_ENV")
    return {
        "petta_root": _resolved_path(
            source.get("PETTA_ROOT") or base / "repos" / "PeTTa"),
        "python_env": _resolved_path(
            python or base / "miniforge3" / "envs" / "petta"),
        "python_explicit": bool(python),
        "cetta": _resolved_path(
            source.get("CETTA_BIN") or base / "repos" /
            "CeTTa-runtime" / "cetta"),
    }


def _python_minor(prefix):
    interpreter = prefix / "bin" / "python3"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise RuntimeDiscoveryError(
            "CeTTa bundle Python prefix has no executable bin/python3: %s"
            % prefix)
    result = subprocess.run(
        [os.fspath(interpreter), "-I", "-c",
         "import sys; print('%d.%d' % sys.version_info[:2])"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeDiscoveryError(
            "cannot inspect CeTTa bundle Python at %s: %s"
            % (interpreter, result.stderr.strip()))
    return result.stdout.strip()


def _require_cetta(paths):
    cetta = paths["cetta"]
    if not cetta.is_file() or not os.access(cetta, os.X_OK):
        raise RuntimeDiscoveryError(
            "required CeTTa executable is unavailable or not executable: %s; "
            "set CETTA_BIN to a Python-enabled CeTTa" % cetta)
    manifest_path = cetta.parent / "build-manifest.json"
    if not manifest_path.is_file():
        return paths
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeDiscoveryError(
            "cannot read CeTTa bundle manifest %s: %s"
            % (manifest_path, error)) from error
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise RuntimeDiscoveryError(
            "unsupported CeTTa bundle manifest schema: %s" % manifest_path)
    files = manifest.get("files")
    expected = files.get(cetta.name) if isinstance(files, dict) else None
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}",
                                                        expected):
        raise RuntimeDiscoveryError(
            "CeTTa bundle manifest lacks a SHA-256 for %s" % cetta.name)
    actual = hashlib.sha256(cetta.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeDiscoveryError(
            "CeTTa executable does not match its bundle manifest: %s" % cetta)
    prefix = manifest.get("python_prefix")
    version = manifest.get("python_version")
    if not isinstance(prefix, str) or not prefix or not isinstance(
            version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", version):
        raise RuntimeDiscoveryError(
            "CeTTa bundle manifest lacks Python prefix/version metadata: %s"
            % manifest_path)
    prefix = _resolved_path(prefix)
    if paths["python_explicit"] and paths["python_env"] != prefix:
        raise RuntimeDiscoveryError(
            "PETTA_PY_ENV is incompatible with the CeTTa bundle: requested "
            "%s, but its verified manifest requires Python %s at %s"
            % (paths["python_env"], version, prefix))
    observed = _python_minor(prefix)
    if observed != version:
        raise RuntimeDiscoveryError(
            "CeTTa bundle expects Python %s at %s, but bin/python3 reports %s"
            % (version, prefix, observed))
    return dict(paths, python_env=prefix)


class RuntimeProbeTest(unittest.TestCase):
    # Retained reference harness reads the registry used by the native driver.
    PROBES = tuple(map(tuple, json.loads((ROOT / "tests/runtime_probes.json").read_text())))

    # Probes of the MeTTa Telegram client, which needs CeTTa's native system
    # and environment operations.
    CETTA_ONLY = {"tests/allowance_wake_loop_probe.metta"}

    # Settings a probe needs from process start, which the clients read once.
    PROBE_ENV = {
        "tests/allowance_wake_loop_probe.metta": {
            "METTACLAW_TELEGRAM_GROUP_WAKE_EVERY": "-100:3",
            "METTACLAW_TELEGRAM_BOT_USERNAME": "GodelTestBot",
            "METTACLAW_TELEGRAM_OPERATOR_IDS": "777",
        },
    }

    @classmethod
    def paths(cls):
        if not hasattr(cls, "_paths"):
            cls._paths = _runtime_paths()
        return cls._paths

    def cetta_paths(self):
        try:
            return _require_cetta(self.paths())
        except RuntimeDiscoveryError as error:
            self.fail(str(error))

    def assert_witness(self, result, marker, engine):
        diagnostic = (result.stdout + "\n" + result.stderr)[-5000:]
        self.assertEqual(result.returncode, 0, diagnostic)
        witnessed = ANSI_CONTROL.sub("", result.stdout)
        self.assertRegex(
            witnessed,
            re.compile(r"^%s$" % re.escape(marker), re.MULTILINE),
            "%s exited successfully without reaching the semantic witness"
            % engine,
        )

    def test_each_probe_reaches_its_success_witness_on_cetta(self):
        """The same probes on CeTTa, the engine Lila runs on."""
        paths = self.cetta_paths()
        cetta = paths["cetta"]
        self.run_probes("cetta", {"CETTA_BIN": os.fspath(cetta),
                                  "CETTA_ROOT": os.fspath(cetta.parent)},
                        "CeTTa", paths)

    def test_explicit_paths_remain_valid_under_fake_home(self):
        paths = self.cetta_paths()
        cetta = paths["cetta"]
        probe = (("tests/weak_process_core_probe.metta",
                  "WEAK_PROCESS_CORE_OK"),)
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(RuntimeProbeTest, "PROBES", probe):
            self.run_probes("cetta", {"CETTA_BIN": os.fspath(cetta),
                                      "CETTA_ROOT": os.fspath(cetta.parent)},
                            "CeTTa", paths)

    def run_probes(self, engine, engine_env, label, paths):
        with tempfile.TemporaryDirectory() as directory:
            test_bin = pathlib.Path(directory) / "bin"
            test_bin.mkdir()
            systemctl = test_bin / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --user ] && [ \"$2\" = is-active ]; then\n"
                "  echo active\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)
            lifecycle = pathlib.Path(directory) / "lifecycle.json"
            lifecycle.write_text(
                json.dumps({"schema": 1, "state": "running"}) + "\n",
                encoding="utf-8",
            )
            deployment = pathlib.Path(directory) / "deployment.json"
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            deployment.write_text(json.dumps({
                "candidate": head,
                "last_observation": {
                    "observed_at": time.time(),
                    "head": head,
                    "active": True,
                    "problems": [],
                },
            }) + "\n", encoding="utf-8")
            for probe, marker in self.PROBES:
                if engine != "cetta" and probe in self.CETTA_ONLY:
                    continue
                with self.subTest(probe=probe):
                    probe_state = pathlib.Path(directory) / pathlib.Path(probe).stem
                    probe_state.mkdir(exist_ok=True)
                    probe_tmp = probe_state / "tmp"
                    probe_tmp.mkdir(exist_ok=True)
                    iter_directory = (
                        pathlib.Path(directory) / "native-iter-multiturn"
                        if probe == "tests/native_iter_multiturn_probe.metta"
                        else ROOT / "tests" / "fixtures" /
                        "native_iter_transformations"
                    )
                    prompt_path = pathlib.Path(directory) / "prompt.txt"
                    prompt_path.write_text(
                        "GODEL_NATIVE_ITER_PERSONA\n", encoding="utf-8")
                    pins_path = pathlib.Path(directory) / "pins.txt"
                    pins_path.write_text(
                        "GODEL_NATIVE_ITER_PIN\n", encoding="utf-8")
                    env = isolated_environ()
                    env.update({
                        "METTACLAW_SKIP_INITIALIZE": "1",
                        "METTACLAW_SKIP_CONFIG": "1",
                        "METTACLAW_ENGINE": engine,
                        "PETTA_ROOT": os.fspath(paths["petta_root"]),
                        "PETTA_PY_ENV": os.fspath(paths["python_env"]),
                        "METTACLAW_ENGINE_STATE_PATH": os.path.join(
                            probe_state, "engine"),
                        "METTACLAW_MODEL_STATE_PATH": os.path.join(
                            probe_state, "model.metta"),
                        "METTACLAW_LLM_LEDGER_PATH": os.path.join(
                            probe_state, "llm-budget.sqlite3"),
                        "METTACLAW_LLM_COMPLETION_PATH": os.path.join(
                            probe_state, "completion.json"),
                        "METTACLAW_PROVIDER_OBSERVATION_PATH": os.path.join(
                            probe_state, "providers.json"),
                        "METTACLAW_COGNITIVE_HEALTH_PATH": os.path.join(
                            probe_state, "health.json"),
                        "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(
                            probe_state, "recycle.requested"),
                        "METTACLAW_WAKE_REQUEST_PATH": os.path.join(
                            probe_state, "wake.requested"),
                        "METTACLAW_CHATGPT_POLICY_PATH": os.path.join(
                            probe_state, "chatgpt.json"),
                        "METTACLAW_LOOP_MODE_PATH": os.path.join(
                            probe_state, "loop-mode.json"),
                        "METTACLAW_CHROMA_DIR": os.path.join(
                            probe_state, "chroma"),
                        "METTACLAW_ITER_PROCESS_DIR": os.fspath(
                            iter_directory),
                        "METTACLAW_ITER_PROCESS_TIMEOUT_SECONDS": "0.2",
                        "METTACLAW_PROMPT_PATH": os.fspath(prompt_path),
                        "METTACLAW_HISTORY_PATH": os.path.join(
                            probe_state, "history.metta"),
                        "METTACLAW_WORKING_SET_PATH": os.path.join(
                            probe_state, "working-set.json"),
                        "METTACLAW_PINS_PATH": os.fspath(pins_path),
                        "METTACLAW_LIFECYCLE_PATH": os.fspath(lifecycle),
                        "METTACLAW_DEPLOYMENT_STATE_PATH": os.fspath(
                            deployment),
                        # The Telegram client runs with no channel service.
                        "METTACLAW_TELEGRAM_SERVICE_SOCKET": "",
                        "TMPDIR": os.fspath(probe_tmp),
                        "PATH": os.pathsep.join((
                            os.fspath(test_bin), env.get("PATH", ""),
                        )),
                    })
                    env.update(engine_env)
                    env.update(self.PROBE_ENV.get(probe, {}))
                    result = subprocess.run(
                        ["./run.sh", probe], cwd=ROOT, env=env,
                        stdin=subprocess.DEVNULL, capture_output=True,
                        text=True, timeout=30,
                    )
                    self.assert_witness(result, marker, label)
                    if probe == "tests/interactive_batch_policy_probe.metta":
                        mode_path = probe_state / "loop-mode.json"
                        history_path = probe_state / "history.metta"
                        self.assertTrue(mode_path.is_file())
                        self.assertIn("coding", mode_path.read_text(
                            encoding="utf-8"))
                        self.assertTrue(history_path.is_file())
                        self.assertIn("probe-stimulus", history_path.read_text(
                            encoding="utf-8"))
                    if probe == "tests/interrupted_cognitive_turn_probe.metta":
                        pins_path = pathlib.Path(directory) / "pins.txt"
                        pins = (pins_path.read_text(encoding="utf-8")
                                if pins_path.is_file() else "")
                        # New input queues while every emitted call runs.
                        self.assertIn("pin-after-new-input-lands", pins)
                        self.assertIn(
                            "pin-after-withheld-send-must-not-land", pins)
                        self.assertNotIn("COMMAND_BATCH_INTERRUPTED",
                                      result.stdout)

    def test_context_projection_crosses_cetta_provider_boundary(self):
        paths = self.cetta_paths()
        cetta = paths["cetta"]
        with tempfile.TemporaryDirectory() as directory:
            state = pathlib.Path(directory)
            tmp = state / "tmp"
            tmp.mkdir()
            env = isolated_environ()
            env.update({
                "CETTA_BIN": os.fspath(cetta),
                "METTACLAW_SKIP_INITIALIZE": "1",
                "METTACLAW_SKIP_CONFIG": "1",
                "METTACLAW_ENGINE": "cetta",
                "PETTA_ROOT": os.fspath(paths["petta_root"]),
                "PETTA_PY_ENV": os.fspath(paths["python_env"]),
                "METTACLAW_ENGINE_STATE_PATH": os.path.join(
                    directory, "engine"),
                "METTACLAW_MODEL_STATE_PATH": os.path.join(
                    directory, "model.metta"),
                "METTACLAW_LLM_LEDGER_PATH": os.path.join(
                    directory, "llm-budget.sqlite3"),
                "METTACLAW_LLM_COMPLETION_PATH": os.path.join(
                    directory, "completion.json"),
                "METTACLAW_PROVIDER_OBSERVATION_PATH": os.path.join(
                    directory, "providers.json"),
                "METTACLAW_COGNITIVE_HEALTH_PATH": os.path.join(
                    directory, "health.json"),
                "METTACLAW_RECYCLE_REQUEST_PATH": os.path.join(
                    directory, "recycle.requested"),
                "METTACLAW_WAKE_REQUEST_PATH": os.path.join(
                    directory, "wake.requested"),
                "METTACLAW_CHATGPT_POLICY_PATH": os.path.join(
                    directory, "chatgpt.json"),
                "METTACLAW_CHROMA_DIR": os.path.join(directory, "chroma"),
                "TMPDIR": os.fspath(tmp),
            })
            result = subprocess.run(
                ["./run.sh", "tests/context_sources_probe.metta"],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
            self.assert_witness(result, "CONTEXT_SOURCES_OK", "CeTTa")


class RuntimeDiscoveryTests(unittest.TestCase):
    def make_bundle(self, directory, version="3.12"):
        cetta = pathlib.Path(directory) / "cetta"
        cetta.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cetta.chmod(0o755)
        manifest = {
            "schema": 1,
            "revision": "test-only",
            "python_prefix": sys.prefix,
            "python_version": version,
            "files": {cetta.name: hashlib.sha256(
                cetta.read_bytes()).hexdigest()},
        }
        (cetta.parent / "build-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        return cetta, manifest

    def test_missing_requested_cetta_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _runtime_paths(
                {"CETTA_BIN": os.path.join(directory, "missing")}, directory)
            with self.assertRaisesRegex(RuntimeDiscoveryError,
                                        "required CeTTa executable.*CETTA_BIN"):
                _require_cetta(paths)

    def test_bundle_rejects_a_different_explicit_python_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            cetta, _ = self.make_bundle(directory)
            paths = _runtime_paths({
                "CETTA_BIN": os.fspath(cetta),
                "PETTA_PY_ENV": os.path.join(directory, "other-python"),
            }, directory)
            with self.assertRaisesRegex(
                    RuntimeDiscoveryError,
                    "PETTA_PY_ENV is incompatible.*verified manifest requires"):
                _require_cetta(paths)

    def test_bundle_rejects_a_mismatched_python_version(self):
        with tempfile.TemporaryDirectory() as directory:
            cetta, _ = self.make_bundle(directory, version="0.0")
            paths = _runtime_paths({"CETTA_BIN": os.fspath(cetta)}, directory)
            with self.assertRaisesRegex(
                    RuntimeDiscoveryError,
                    "expects Python 0.0.*bin/python3 reports"):
                _require_cetta(paths)

    def test_bundle_rejects_an_executable_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            cetta, manifest = self.make_bundle(directory)
            manifest["files"][cetta.name] = "0" * 64
            (cetta.parent / "build-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            paths = _runtime_paths({"CETTA_BIN": os.fspath(cetta)}, directory)
            with self.assertRaisesRegex(RuntimeDiscoveryError,
                                        "does not match its bundle manifest"):
                _require_cetta(paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)

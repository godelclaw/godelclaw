#!/usr/bin/env python3
"""Run native unittest modules in isolated processes against one verified bundle."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest

from cetta_profile import manifest_python_enabled, require_native_agent

ROOT = Path(__file__).resolve().parents[1]
ABORT = threading.Event()


def worker(module, result_path):
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT / name) for name in ("tests", "src", "scripts", "channels")]
    import hermetic_env  # noqa: F401: isolate state before importing any test
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=module + ".py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    outcome = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), expected_failures=len(result.expectedFailures),
                   unexpected_successes=len(result.unexpectedSuccesses))
    result_path.write_text(json.dumps(outcome) + "\n")
    return int(not result.wasSuccessful())


def bundle_environment(binary, require_core=False):
    binary = binary.expanduser().resolve(strict=True)
    bundle = binary.parent
    manifest = json.loads((bundle / "build-manifest.json").read_text())
    if manifest.get("schema") != 1:
        raise ValueError("unsupported bundle manifest")
    names = ("cetta", "cetta-value-worker", "cetta-space-service", "cetta-telegram-service")
    for name in names:
        path = bundle / name
        if (not os.access(path, os.X_OK) or
                hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"].get(name)):
            raise ValueError("bundle executable differs from its manifest: " + name)
    python_enabled = manifest_python_enabled(manifest)
    if require_core:
        require_native_agent(manifest)
    if python_enabled:
        interpreter = Path(manifest["python_prefix"]) / "bin/python3"
        if "%d.%d" % sys.version_info[:2] != manifest["python_version"]:
            if interpreter.resolve() == Path(sys.executable).resolve():
                raise ValueError("bundle Python version differs from its manifest")
            os.execv(str(interpreter), [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]])
    env = dict(os.environ)
    env.update(CETTA_BIN=str(binary), CETTA_ROOT=str(bundle), CETTA_CHANNEL_ROOT=str(bundle),
               CETTA_VALUE_WORKER=str(bundle / "cetta-value-worker"),
               CETTA_VALUE_WORKER_BIN=str(bundle / "cetta-value-worker"),
               CETTA_SPACE_SERVICE=str(bundle / "cetta-space-service"),
               CETTA_SERVICE_BIN=str(bundle / "cetta-telegram-service"),
               MODE_TEST_CETTA_BIN=str(binary), METTACLAW_MODE_CETTA_BIN=str(binary))
    if python_enabled:
        env['PETTA_PY_ENV'] = manifest['python_prefix']
    paths = [ROOT, ROOT / "tests", ROOT / "src", ROOT / "scripts", ROOT / "channels", bundle / "tools"]
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    # Code/tool locations survive the worker's deliberately fake HOME.
    if "ELAN_HOME" not in env and (Path.home() / ".elan").is_dir():
        env["ELAN_HOME"] = str(Path.home() / ".elan")
    if "LEAN_BIN" not in env and "ELAN_HOME" in env:
        lean = Path(env["ELAN_HOME"]) / "bin/lean"
        if lean.is_file():
            env["LEAN_BIN"] = str(lean)
    for name, folder, marker in (("PETTA_ROOT", "PeTTa", "run.sh"),
                                  ("CETTA_SOURCE", "CeTTa-ours", "Makefile")):
        checkout = ROOT.parent / folder
        if (checkout / marker).is_file():
            env.setdefault(name, str(checkout))
    source = env.get("CETTA_SOURCE")
    if source:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        dirty = subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=source, text=True)
        if revision != manifest["revision"] or dirty.strip():
            raise ValueError("CETTA_SOURCE must be a clean checkout of the bundle revision")
    return env, manifest


def run_module(module, directory, environment, timeout):
    started = time.monotonic()
    result_path = directory / (module + ".json")
    if ABORT.is_set():
        return dict(module=module, exit=130)
    with (directory / (module + ".log")).open("wb") as log:
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", module,
                   "--result", str(result_path)]
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while True:
                if ABORT.is_set():
                    raise InterruptedError
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    code = process.wait(timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (subprocess.TimeoutExpired, InterruptedError):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 130 if ABORT.is_set() else 124
    outcome = json.loads(result_path.read_text()) if result_path.exists() else {}
    outcome.update(module=module, exit=code, seconds=time.monotonic() - started)
    result_path.write_text(json.dumps(outcome, indent=2) + "\n")
    print(module, "OK" if code == 0 else "FAILED", flush=True)
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", nargs="*", help="unittest module names; defaults to all test_*.py")
    parser.add_argument("--cetta", type=Path, default=os.environ.get("CETTA_BIN"))
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--require-core", action="store_true",
                        help="require the complete Python-free native-agent engine profile")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.result)
    if args.cetta is None or args.log_dir is None:
        parser.error("--cetta (or CETTA_BIN) and --log-dir are required")
    if args.jobs < 1 or args.timeout <= 0:
        parser.error("--jobs and --timeout must be positive")
    modules = args.modules or sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    if len(set(modules)) != len(modules) or any(
            not name.startswith("test_") or not name.isidentifier() or
            not (ROOT / "tests" / (name + ".py")).is_file() for name in modules):
        parser.error("choose distinct existing unittest module names")
    environment, manifest = bundle_environment(args.cetta, args.require_core)
    directory = args.log_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    signal.signal(signal.SIGINT, lambda *_: ABORT.set())
    signal.signal(signal.SIGTERM, lambda *_: ABORT.set())
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda name: run_module(name, directory, environment, args.timeout), modules))
    summary = dict(engine_revision=manifest["revision"],
                   engine_profile=manifest.get('runtime_profile', 'python' if manifest_python_enabled(manifest) else 'core'),
                   python_enabled=manifest_python_enabled(manifest), jobs=args.jobs,
                   seconds=time.monotonic() - started, modules=results)
    for key in ("tests", "failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        summary[key] = sum(result.get(key, 0) for result in results)
    summary["failed_modules"] = sum(bool(result["exit"]) for result in results)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "modules"}))
    return int(bool(summary["failed_modules"]))


if __name__ == "__main__":
    raise SystemExit(main())

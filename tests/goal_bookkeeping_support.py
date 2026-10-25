"""Isolated project-04b subprocesses and full, generated-fixture receipts."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import resource
import re
import subprocess
import sys
import tempfile

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KIB = 25165824
FROZEN_EXE_SHA256 = "2c3a593f7482a60321e36b12a86e8fac788d0a690cda770af7b021a6df92fb7b"
FROZEN_MANIFEST_SHA256 = "1b1648b95ab4428cffb00316c4e732b28621228318ceba824a860ce05b01a627"
PATHS = {
    "METTACLAW_GOALS_PATH": "goals.metta",
    # Oracle kernel/compost paths are fixed neighbors, not environment knobs.
    "GOAL_BOOKKEEPING_KERNEL_PATH": "goals_kernel.json",
    "GOAL_BOOKKEEPING_COMPOST_PATH": "goals_compost.log",
    "GOAL_BOOKKEEPING_OBSERVATIONS": "observations.jsonl",
    "METTACLAW_HISTORY_PATH": "history.metta",
    "METTACLAW_TELEGRAM_LOG_PATH": "updates.jsonl",
    "METTACLAW_STRATEGY_PATH": "strategy.sqlite3",
    "METTACLAW_LLM_LEDGER_PATH": "ledger.db",
    "METTACLAW_RECYCLE_REQUEST_PATH": "recycle",
    "METTACLAW_WAKE_REQUEST_PATH": "wake",
    "METTACLAW_ENGINE_STATE_PATH": "engine",
    "METTACLAW_LIFECYCLE_PATH": "lifecycle.json",
    "METTACLAW_MEMORY_BACKEND_CONFIG": "memory-backend.json",
    "METTACLAW_LLM_POLICY_PATH": "llm-policy.json",
    "METTACLAW_MODEL_STATE_PATH": "model",
    "METTACLAW_COGNITIVE_HEALTH_PATH": "health.json",
    "METTACLAW_DEPLOYMENT_STATE_PATH": "deployment.json",
    "METTACLAW_LOOP_MODE_PATH": "loop-mode.json",
    "METTACLAW_ENERGY_PATH": "energy.json",
    "METTACLAW_FUEL_MODE_PATH": "fuel.json",
    "METTACLAW_PINS_PATH": "pins.txt",
    "METTACLAW_REPOS_DIR": "repos",
    "METTACLAW_CHROMA_DIR": "chroma",
    "METTACLAW_WORKING_SET_PATH": "working.json",
    "METTACLAW_EFFECT_RECEIPT_PATH": "receipts.jsonl",
    "METTACLAW_TELEGRAM_OFFSET_PATH": "offset",
    "METTACLAW_TELEGRAM_OUTBOX_PATH": "outbox.jsonl",
    "METTACLAW_TELEGRAM_SEQUENCE_PATH": "sequence",
    "METTACLAW_TELEGRAM_HEALTH_PATH": "telegram-health.json",
    "METTACLAW_TELEGRAM_ATTACHMENTS_DIR": "attachments",
    "METTACLAW_CHANNEL_PATH": "channel.metta",
}


def environment(directory):
    env = isolated_environ()
    for name in list(env):
        if name.startswith(("METTACLAW_", "TELEGRAM_", "OPENAI_", "ANTHROPIC_", "SYNTHETIC_",
                            "CETTA_", "DYLD_")) or name in (
                "LD_PRELOAD", "METTA_PATH", "METTA_LIBRARY_PATH") or any(
                part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")):
            env.pop(name)
    env.update({name: str(directory / filename) for name, filename in PATHS.items()})
    if os.environ.get("CETTA_BIN"):
        env["CETTA_BIN"] = os.environ["CETTA_BIN"]
    deny = directory / "deny-bin"
    deny.mkdir(exist_ok=True)
    (deny / "tmux").write_text("#!/bin/sh\necho 'project 04b: tmux forbidden' >&2\nexit 126\n")
    (deny / "tmux").chmod(0o700)
    env.update(HOME=str(directory), TMPDIR=str(directory),
               XDG_STATE_HOME=str(directory / "state"), XDG_CONFIG_HOME=str(directory / "config"),
               XDG_CACHE_HOME=str(directory / "cache"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONHOME=sys.prefix,
               PETTA_PY_ENV=sys.prefix, LD_LIBRARY_PATH=str(Path(sys.prefix) / "lib"),
               PYTHONPATH=os.pathsep.join(str(ROOT / part) for part in ("tests", "src", "channels")),
               PATH=str(deny) + os.pathsep + env.get("PATH", ""),
               METTACLAW_ENGINE="cetta", METTACLAW_ACTIVE_ENGINE="cetta",
               METTACLAW_SKIP_INITIALIZE="1", METTACLAW_OBSERVED_STRATEGY="0")
    return env


def limit():
    bound = LIMIT_KIB * 1024
    resource.setrlimit(resource.RLIMIT_AS, (bound, bound))


def receipt(kind, value):
    target = os.environ.get("GOAL_BOOKKEEPING_EVIDENCE")
    if target:
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", prefix=kind + "-", suffix=".json", dir=path,
                                         encoding="utf-8", delete=False) as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")


def frozen_identity(phase, edge):
    """Read-only full bundle identity; a mismatch must stop, never repair."""
    binary = Path(os.environ["CETTA_BIN"])
    assert not binary.is_symlink(), "frozen executable must not be a symlink"
    root = binary.resolve().parent
    manifest = (root / "SHA256SUMS").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == FROZEN_MANIFEST_SHA256, "frozen manifest changed"
    rows = []
    for line in manifest.decode("ascii").splitlines():
        expected, name = line.split(maxsplit=1)
        path = root / name
        assert path.resolve().is_relative_to(root) and not path.is_symlink(), name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, (name, expected, actual)
        rows.append({"file": name, "sha256": actual})
    assert len(rows) == 341
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == FROZEN_EXE_SHA256
    assert {r["file"] for r in rows} == {binary.name} | {
        str(p.relative_to(root)) for p in (root / "lib").rglob("*") if p.is_file()}
    result = {"phase": phase, "edge": edge, "binary": str(binary),
              "manifest_sha256": FROZEN_MANIFEST_SHA256, "files": rows,
              "provenance": "clean aec06044 supplied by reviewer; hashes are observations, not independent build certification"}
    receipt("frozen-identity", result)
    return result


def frozen_resolution(directory):
    libraries = ("lib_import", "fs", "str", "re", "json", "math", "system", "path")
    program = "".join('!(import! &self (library %s))\n' % name for name in libraries)
    # PeTTa's library descriptor uses the same native overlay resolver as
    # import!. Native fast-path imports need not enter module-inventory!.
    program += "".join('!(println! (FrozenLibrary (library %s)))\n' % name for name in libraries)
    result = engine(directory, program)
    assert result.returncode == 0, result.stdout + result.stderr
    paths = [p.strip('"') for p in re.findall(r'\(FrozenLibrary ([^\n]+)\)', result.stdout)]
    root = Path(os.environ["CETTA_BIN"]).resolve().parent
    assert paths, "native library inventory did not expose paths"
    assert len(paths) == len(libraries), result.stdout
    assert all(Path(p + ".metta").is_file() and Path(p).resolve().is_relative_to(root / "lib") for p in paths), paths
    assert all(any(Path(p).stem == name for p in paths) for name in libraries), paths
    receipt("frozen-resolution", {"paths": paths, "libraries": libraries,
        "environment": {k: v for k, v in environment(directory).items()
                        if k.startswith(("CETTA_", "PETTA_", "LD_", "DYLD_"))}})


@contextmanager
def frozen_phase(name, directory):
    frozen_identity(name, "START")
    try:
        frozen_resolution(directory)
        yield
    finally:
        frozen_identity(name, "END")


def run(command, directory, **kwargs):
    cwd = kwargs.pop("cwd", ROOT)
    env = environment(directory)
    env.update(kwargs.pop("extra_env", {}))
    result = subprocess.run(command, cwd=cwd, env=env,
                            capture_output=True, timeout=180, preexec_fn=limit, **kwargs)
    raw_out, raw_err = result.stdout, result.stderr
    result.stdout = raw_out.decode("utf-8", errors="backslashreplace")
    result.stderr = raw_err.decode("utf-8", errors="backslashreplace")
    receipt("command", {"command": list(map(str, command)), "cwd": str(cwd),
                        "stdout": result.stdout, "stderr": result.stderr,
                        "stdout_base64": base64.b64encode(raw_out).decode(),
                        "stderr_base64": base64.b64encode(raw_err).decode(),
                        "returncode": result.returncode, "ulimit_v_kib": LIMIT_KIB})
    return result


def engine(directory, program, runtime_root=ROOT, extra_env=None):
    # Probe sources live outside the checkout. Resolve only our explicit local
    # imports; ancestor-walk otherwise silently leaves missing calls unevaluated.
    runtime_root = Path(runtime_root)
    for part in ("src", "tests", "verification"):
        base = runtime_root if part == "src" else ROOT
        program = program.replace("./" + part + "/", str(base / part) + "/")
    path = directory / "probe.metta"
    path.write_text(program, encoding="utf-8")
    receipt("program", {"program": program})
    return run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(path)], directory,
               cwd=runtime_root, extra_env={"PYTHONPATH": os.pathsep.join((str(ROOT / "tests"), str(runtime_root / "src"), str(runtime_root / "channels"))), **(extra_env or {})})


def engine_info(directory):
    binary = Path(os.environ["CETTA_BIN"]).resolve()
    result = run([str(binary), "--version"], directory)
    info = {"binary": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "version_stdout": result.stdout, "version_stderr": result.stderr,
            "version_returncode": result.returncode, "python": sys.version,
            "python_executable": sys.executable,
            "provenance": "Caller-selected reviewer TEST artifact; no rebuild or installed selection change. "
                          "Source checkout HEAD is not a binary build-attestation."}
    receipt("engine", info)
    return info

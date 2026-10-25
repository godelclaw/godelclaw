"""Isolated project-04b subprocesses and full, generated-fixture receipts."""
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

from isolated_env import isolated_environ

ROOT = Path(__file__).resolve().parents[1]
LIMIT_KIB = 25165824
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
                            "CETTA_IO_AUTH_")) or any(
                part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")):
            env.pop(name)
    env.update({name: str(directory / filename) for name, filename in PATHS.items()})
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


def engine(directory, program):
    # Probe sources live outside the checkout. Resolve only our explicit local
    # imports; ancestor-walk otherwise silently leaves missing calls unevaluated.
    for part in ("src", "tests", "verification"):
        program = program.replace("./" + part + "/", str(ROOT / part) + "/")
    path = directory / "probe.metta"
    path.write_text(program, encoding="utf-8")
    receipt("program", {"program": program})
    return run([os.environ["CETTA_BIN"], "--lang", "petta", "--import-mode", "ancestor-walk", str(path)], directory)


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

"""One captured model activity, executed with the existing native LLM client.

The coordinator receives inert text and provenance. No model commands are
parsed or dispatched here. Linux parent-death handling scopes this process to
its worker controller; interrupting a started call leaves explicit uncertainty.
"""
import ctypes
import json
import os
from pathlib import Path
import signal


def arm():
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0):
        raise RuntimeError("cannot scope model activity to its parent")
    if os.getppid() != int(os.environ["METTACLAW_ACTIVITY_PARENT_PID"]):
        raise SystemExit(75)
    return 1


def prepare():
    import native_llm_host
    request = json.loads(Path(os.environ["METTACLAW_ACTIVITY_INPUT"]).read_text())
    return native_llm_host.command_plan(request["model"], request["tokens"],
                                        request["effort"], request["prompt"])


def finish(plan, content):
    import native_lifetime
    import llm_policy
    with native_lifetime.transaction() as db:
        row = db.execute("SELECT state,reason FROM native_calls WHERE id=? AND epoch=?",
                         (plan.correlation, getattr(plan, "epoch", ""))).fetchone()
    state = row[0] if row else "not-started"
    reason = row[1] if row else "provider-not-started"
    if row is None and hasattr(plan, "error"):
        error = plan.error
        reason = error.reason if isinstance(error, llm_policy.AdmissionDenied) else (
            "provider-request-invalid" if isinstance(error, ValueError) else "provider-configuration-error")
    status = "completed" if state == "completed" else ("unknown" if state == "uncertain" else "failed")
    result = {"kind": "model", "status": status,
              "call_id": plan.correlation, "value": str(content) if state == "completed" else "",
              "reason": reason}
    if hasattr(plan, "request"):
        result["request_fingerprint"] = llm_policy.fingerprint(json.loads(plan.request))
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > 65536:
        encoded = json.dumps({"kind": "model", "status": "failed", "call_id": plan.correlation,
                              "reason": "result-exceeds-channel-bound"}).encode()
    path = Path(os.environ["METTACLAW_ACTIVITY_RESULT"])
    temporary = path.with_name(path.name + ".part")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return 1

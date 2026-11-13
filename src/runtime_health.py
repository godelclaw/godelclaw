"""Privacy-safe runtime health status for the deployment watcher.

scripts/runtime_watch.py reads status(); the command responder's /health
and /activity are native (src/runtime_health_cetta.metta)."""

import json
import os
import subprocess
import time

import cognitive_health
import loop_modes
import memory_health


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _health_path():
    configured = os.environ.get("METTACLAW_TELEGRAM_HEALTH_PATH", "")
    if configured:
        return configured
    state_home = os.environ.get(
        "XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    instance = os.environ.get("METTACLAW_INSTANCE", "default")
    return os.path.join(state_home, "pettaclaw", instance,
                        "telegram-health.json")


def _generation():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=2, check=True)
        return result.stdout.strip()[:12]
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("METTACLAW_DEPLOYED_COMMIT", "unknown")[:12]


def _positive_seconds(name, default):
    try:
        return max(30, int(float(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def status(now=None):
    now = time.time() if now is None else float(now)
    channel = _read_json(_health_path())
    working = _read_json(os.environ.get(
        "METTACLAW_WORKING_SET_PATH", "memory/working_set.json"))
    mode = _read_json(os.environ.get(
        "METTACLAW_LOOP_MODE_PATH", "memory/loop_mode.json"))
    cognition = cognitive_health.snapshot()
    import lifecycle
    lifecycle_enabled = bool(lifecycle.cognition_enabled())
    poll_at = float(channel.get("last_poll_ok_at", 0) or 0)
    saved_at = float(working.get("saved_at", 0) or 0)
    waiting_until = float(channel.get("waiting_until", 0) or 0)
    legitimate_wait = (channel.get("loop_status") == "waiting"
                       and waiting_until >= now - 10)
    poll_age = None if not poll_at else max(0, int(now - poll_at))
    working_age = None if not saved_at else max(0, int(now - saved_at))
    schema = int(cognition.get("schema", 1) or 1)
    pending_at = float(cognition.get("in_flight_since", 0) or 0)
    obligation_at = float(cognition.get("obligation_since", 0) or 0)
    if schema < 2:
        legacy_pending = float(cognition.get("pending_since", 0) or 0)
        if cognition.get("last_outcome") == "failed":
            obligation_at = obligation_at or legacy_pending
        else:
            pending_at = pending_at or legacy_pending
    completed_at = float(cognition.get("last_completed_at", 0) or 0)
    pending_age = None if not pending_at else max(0, int(now - pending_at))
    obligation_age = (None if not obligation_at
                      else max(0, int(now - obligation_at)))
    turn_age = None if not completed_at else max(0, int(now - completed_at))
    try:
        loops = max(0, int(working.get("loops", 0) or 0))
    except (TypeError, ValueError):
        loops = 0
    active_mode = str(mode.get("mode", "default")).strip().lower()
    autonomous = loop_modes.autonomous(active_mode)
    control = {}
    state_path = os.environ.get("METTACLAW_ENGINE_STATE_PATH")
    if state_path:
        control = _read_json(os.path.join(os.environ.get("METTACLAW_MODE_STATE_ROOT",
            os.path.join(os.path.dirname(state_path), "modes")), "control-status.json"))
    control_age = (max(0, int(now - float(control.get("observed_at", 0)))) if control else None)
    # Upstream modes checkpoint their own history, not working_set.json.
    if active_mode in ("iter", "omega"):
        settled = float(cognition.get("last_settled_at", 0) or 0)
        if settled:
            working_age = max(0, int(now - settled))
    # An autonomous mode expects cognition, but a rest in flight is a
    # legitimate reason for there to be none. Without this, every long rest
    # under iter would report a stale model turn.
    cognition_required = lifecycle_enabled and bool(
        pending_at or obligation_at or loops > 0
        or (autonomous and not legitimate_wait))
    stale_after = _positive_seconds(
        "METTACLAW_COGNITIVE_STALE_SECONDS", 900)
    problems = []
    if control:
        if control_age > 30:
            problems.append("operator-control-stale")
        if not control.get("channel_ready"):
            problems.append("channel-service-unavailable")
    elif poll_age is None or poll_age > 120:
        problems.append("telegram-poll-stale")
    if (lifecycle_enabled and not legitimate_wait
            and (working_age is None or working_age > 900)):
        problems.append("cognitive-boundary-stale")
    if cognition_required and pending_age is not None:
        if pending_age > stale_after:
            problems.append("local-completion-blocked" if cognition.get("local_commit_pending") is True
                            else "model-turn-overdue")
    elif cognition_required and turn_age is not None and turn_age > stale_after:
        problems.append("model-turn-stale")
    elif cognition_required and not cognition:
        started_at = float(channel.get("started_at", 0) or saved_at or 0)
        if started_at and now - started_at > stale_after:
            problems.append("model-turn-receipt-missing")
    configured = memory_health.backend()
    if configured["mode"] != "native":
        problems.append("memory-not-native")
    elif not memory_health.native_reachable(configured["socket"]):
        problems.append("memory-service-unreachable")
    return {
        "state": "ok" if not problems else "problem",
        "lifecycle": lifecycle.view(),
        "problems": problems,
        "generation": _generation(),
        "telegram_poll_age_seconds": poll_age,
        "channel_health_source": "independent-control" if control else "loop-client",
        "operator_control_age_seconds": control_age,
        "channel_service_ready": control.get("channel_ready") if control else None,
        "loop": "waiting" if legitimate_wait else channel.get(
            "loop_status", "unknown"),
        "working_set_age_seconds": working_age,
        "cognition_required": cognition_required,
        "model_turn_age_seconds": turn_age,
        "model_turn_pending_age_seconds": pending_age,
        "model_turn_obligation_age_seconds": obligation_age,
        "model_turn_expected_count": int(
            cognition.get("expected_count", 0) or 0),
        "model_turn_completed_count": int(
            cognition.get("completed_count", 0) or 0),
        "model_turn_last_outcome": cognition.get("last_outcome", "none"),
        "memory": configured["mode"],
    }


if __name__ == "__main__":
    print(json.dumps(status(), sort_keys=True))

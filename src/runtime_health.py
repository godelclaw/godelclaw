"""Privacy-safe runtime health summary for operators and supervisors."""

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
    if not control and channel.get("menu_status") != "ok":
        problems.append("command-menu-not-registered")
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
            problems.append("model-turn-overdue")
    elif cognition_required and turn_age is not None and turn_age > stale_after:
        problems.append("model-turn-stale")
    elif cognition_required and not cognition:
        started_at = float(channel.get("started_at", 0) or saved_at or 0)
        if started_at and now - started_at > stale_after:
            problems.append("model-turn-receipt-missing")
    configured = memory_health.backend()
    if configured["mode"] == "native":
        if not memory_health.native_reachable(configured["socket"]):
            problems.append("memory-service-unreachable")
    else:
        docs, _indexed, behind = memory_health.drift()
        threshold = memory_health.threshold()
        if docs and behind > threshold:
            problems.append("memory-index-drift")
    return {
        "state": "ok" if not problems else "problem",
        "lifecycle": lifecycle.view(),
        "problems": problems,
        "generation": _generation(),
        "menu": channel.get("menu_status", "unknown"),
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
        "memories": docs,
        "memory_index_behind": behind,
        "memory_flush_threshold": threshold,
    }


def report():
    value = status()
    problems = ",".join(value["problems"]) or "none"
    return (
        "runtime-health: {state} | lifecycle={lifecycle} | "
        "generation={generation} | menu={menu} | "
        "telegram={transport} | loop={loop} | working-set-age={working}s | "
        "model-turn-age={turn}s | model-turn-pending={pending}s | "
        "memory={memories} ({behind}/{threshold} behind) | problems={problems}"
    ).format(
        state=value["state"], lifecycle=value["lifecycle"],
        generation=value["generation"],
        menu=value["menu"], transport=("service-ready=%s, control-age=%ss (polling owned by transport)" %
            (value["channel_service_ready"], value["operator_control_age_seconds"]))
            if value["channel_health_source"] == "independent-control" else
            "poll-age=%ss" % value["telegram_poll_age_seconds"],
        loop=value["loop"], working=value["working_set_age_seconds"],
        turn=value["model_turn_age_seconds"],
        pending=value["model_turn_pending_age_seconds"],
        memories=value["memories"], behind=value["memory_index_behind"],
        threshold=value["memory_flush_threshold"], problems=problems,
    )


def activity_report(now=None):
    """Human-facing cognitive activity, derived from existing receipts.

    This is observation only: it does not wake the loop, alter its budget, or
    infer whether the current work is semantically good.
    """
    now = time.time() if now is None else float(now)
    working = _read_json(os.environ.get(
        "METTACLAW_WORKING_SET_PATH", "memory/working_set.json"))
    cognition = cognitive_health.snapshot()
    try:
        loops = max(0, int(working.get("loops", 0) or 0))
    except (TypeError, ValueError):
        loops = 0
    try:
        banked = max(0, int(working.get("banked_loops", 0) or 0))
    except (TypeError, ValueError):
        banked = 0
    saved_at = float(working.get("saved_at", 0) or 0)
    completed_at = float(cognition.get("last_completed_at", 0) or 0)
    schema = int(cognition.get("schema", 1) or 1)
    pending_at = float(cognition.get("in_flight_since", 0) or 0)
    obligation_at = float(cognition.get("obligation_since", 0) or 0)
    if schema < 2:
        legacy_pending = float(cognition.get("pending_since", 0) or 0)
        if cognition.get("last_outcome") == "failed":
            obligation_at = obligation_at or legacy_pending
        else:
            pending_at = pending_at or legacy_pending
    checkpoint_age = None if not saved_at else max(0, int(now - saved_at))
    turn_age = None if not completed_at else max(0, int(now - completed_at))
    pending_age = None if not pending_at else max(0, int(now - pending_at))
    obligation_age = (None if not obligation_at
                      else max(0, int(now - obligation_at)))

    import engine_modes
    import loop_modes
    import synthetic_llm
    import telegram
    continuation = bool(working.get("continuation_pending"))
    actually_resting, rest_left = telegram.rest_status()
    import lifecycle
    lifecycle_enabled = bool(lifecycle.cognition_enabled())
    pending_inputs = telegram.pendingActivityCount()
    prepared_inputs = telegram.preparedActivityCount()
    rest_intent = str(working.get("intent", "") or "").strip()
    if not lifecycle_enabled:
        state = "stopped"
        wake = "/start"
        steps = 0
        steps_source = "operator-latch"
    elif pending_age is not None:
        state = "working"
        wake = "now"
        try:
            steps = max(0, int(cognition.get("budget_at_start", loops)))
        except (TypeError, ValueError):
            steps = loops
        steps_source = "turn-start"
    elif loops > 0:
        state = "working"
        wake = "now"
        steps = loops
        steps_source = "checkpoint"
    elif continuation:
        state = "scheduled"
        wake = "timer-or-human"
        steps = max(loops, banked)
        steps_source = "banked" if banked > loops else "checkpoint"
    elif actually_resting:
        # Rest banks the budget rather than spending it, so the energy the
        # agent wakes owing is the honest number to show while it sleeps.
        state = "resting"
        wake = "timer-or-human"
        steps = max(loops, banked)
        steps_source = "banked" if banked > loops else "checkpoint"
    else:
        state = "idle"
        wake = "human-or-heartbeat"
        steps = loops
        steps_source = "checkpoint"
    if state == "working" and pending_age is None and cognition.get("last_outcome") == "failed":
        state = "retrying"
        delay = max(0, int(float(cognition.get("retry_at", 0) or 0) - now))
        wake = "human-or-%ss" % delay if delay else "retry-ready"
    import process_supervision
    progress = process_supervision.observation(now)
    if lifecycle_enabled and state in ("working", "retrying"):
        if progress["state"] in ("stalled", "memory-pressure"):
            state, wake = progress["state"], "supervisor"
        elif progress["state"] == "waiting":
            state, wake = "waiting", "provider-or-deadline"
        elif (progress["state"] == "unknown" and checkpoint_age is not None
              and checkpoint_age > 180 and (pending_age is None or pending_age > 180)):
            state, wake = "unconfirmed", "check-supervisor"
    age = lambda value: "never" if value is None else "%ss" % value
    if pending_age is not None:
        turn = "pending:%s" % age(pending_age)
    elif cognition.get("last_outcome") == "failed":
        failed_at = float(cognition.get("last_failed_at", 0) or 0)
        failed_age = None if not failed_at else max(0, int(now - failed_at))
        turn = "failed:%s-ago" % age(failed_age)
    else:
        turn = "completed:%s-ago" % age(turn_age)
    obligation = ("retry:%s" % age(obligation_age)
                  if obligation_age is not None else "none")
    input_state = ("prepared:%d/pending:%d" %
                   (prepared_inputs, pending_inputs)
                   if prepared_inputs else
                   "pending:%d" % pending_inputs
                   if pending_inputs else "none")
    rest = "%ss-left" % rest_left if actually_resting else "none"
    # The stored intention is consumed at boot, so it outlives a rest that
    # ended by waking. Report it only while there is a rest to attach it to.
    if rest_intent and actually_resting:
        rest += ":" + " ".join(rest_intent.split())[:120]
    import fuel_modes
    import topic_jobs
    return (
        "activity: {state} | steps={steps}@{steps_source} | fuel={fuel} | "
        "mode={mode} | "
        "engine={engine} | model={model} | wake={wake} | "
        "rest={rest} | continuation={continuation} | "
        "checkpoint-age={checkpoint} | "
        "turn={turn} | input={input_state} | obligation={obligation} | "
        "last-outcome={outcome} | progress={progress} | background={background}"
    ).format(
        state=state, steps=steps, steps_source=steps_source,
        fuel=fuel_modes.current_fuel(),
        mode=loop_modes.current_mode(),
        engine=engine_modes.active_engine(),
        model=synthetic_llm.current_model(),
        wake=wake,
        rest=rest,
        continuation=("pending" if continuation else "none"),
        checkpoint=age(checkpoint_age), turn=turn,
        input_state=input_state,
        obligation=obligation,
        outcome=cognition.get("last_outcome", "none"),
        progress=progress["state"],
        background=topic_jobs.activity(),
    )


if __name__ == "__main__":
    print(json.dumps(status(), sort_keys=True))

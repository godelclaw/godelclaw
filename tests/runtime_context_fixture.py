"""Isolated real-engine context and omission controls."""
import json
import os
from pathlib import Path
import time
import cognitive_health
import helper
import mode_channel
import strategy_host
import synthetic_llm
import provider_protocol

_facts = []
_calls = 0
_omitted = False


def status(ready):
    now = time.time()
    mode_channel.store(mode_channel.state() / "control-status.json",
        {"observed_at": now, "channel_ready": bool(ready),
         "operator_inbox": {"pending": 1, "newestMessageAt": now-40,
                            "oldestMessageAt": now-40, "observedAt": now}})
    return 1


def chat(*args):
    global _calls
    _calls += 1
    prompt = str(args[-1])
    frame = provider_protocol.role_context(prompt)
    if frame is not None:
        prompt = "\n".join(message["content"] for message in frame["messages"])
    if _omitted:
        assert "RUNTIME_STATE " not in prompt
        cognitive_health.turn_started("probe")
        cognitive_health.turn_completed(10)
        return "((help vitals))"
    assert prompt.count("RUNTIME_STATE ") == 1
    line = next(line for line in prompt.splitlines() if line.startswith("RUNTIME_STATE "))
    facts = json.loads(line.removeprefix("RUNTIME_STATE "))
    assert facts["localChannelReady"] is (len(_facts) == 0)
    assert facts["call"]["phase"] == "returned" and facts["turnPending"] is True
    if len(_facts) == 0:
        assert facts["humanInbox"]["pending"] == 1
        assert facts["humanInbox"]["newestAgeSeconds"] >= 40
    else:
        assert facts["humanInbox"] is None  # failed channel gives no inbox assurance
    _facts.append(facts)
    cognitive_health.turn_started("probe")
    cognitive_health.turn_completed(10)
    return "((help vitals))"


def setup():
    global _calls, _omitted
    _calls, _omitted = 0, False
    _facts.clear()
    strategy_host.guard = lambda **kwargs: "runtime-view-control"
    synthetic_llm.chat = chat
    helper._working_boot_cache = {}
    helper.working_boot = lambda: 3
    cognitive_health.turn_started("previous-probe")
    cognitive_health.turn_completed(10)
    return status(True)


def source_changed():
    return status(False)


def omit():
    global _omitted
    _omitted = True
    return 1


def check():
    assert _calls == 3 and len(_facts) == 2, "missing telemetry must not invent a ready-channel fact"
    assert len(strategy_host.ledger().projection()["actions"]) == 3
    assert strategy_host.ledger().active(), "observations must not complete retained work"
    path = os.environ.get("METTACLAW_RUNTIME_VIEW_PROBE_OUTPUT")
    if path:
        Path(path).write_text(json.dumps(_facts, indent=2) + "\n")
    return 1

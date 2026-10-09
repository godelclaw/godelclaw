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

_facts = []


def status(ready):
    now = time.time()
    mode_channel.store(mode_channel.state() / "control-status.json",
        {"observed_at": now, "channel_ready": bool(ready),
         "operator_inbox": {"pending": 1, "newestMessageAt": now-40,
                            "oldestMessageAt": now-40, "observedAt": now}})
    return 1


def chat(*args):
    prompt = str(args[-1])
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
    strategy_host.prepare = lambda prompt: str(prompt) + "\nPROTECTED_STRATEGY " + strategy_host.view()
    return 1


def check():
    assert len(_facts) == 2, "a stripped packet must prevent another provider call"
    assert len(strategy_host.ledger().projection()["actions"]) == 2
    assert strategy_host.ledger().active(), "observations must not complete retained work"
    path = os.environ.get("METTACLAW_RUNTIME_VIEW_PROBE_OUTPUT")
    if path:
        Path(path).write_text(json.dumps(_facts, indent=2) + "\n")
    return 1

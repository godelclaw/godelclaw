"""Offline host boundaries; the pinned Omega loop is not rewritten here.

Each evaluator runs in a fresh process and a private copy of the core. This
module supplies configuration, channels, provider replies and a fixture clock.
It never performs real network I/O or opens account configuration.
"""

import json
import os
from pathlib import Path


ROOT = Path(os.environ["OMEGA_FIXTURE_ROOT"])
SCRIPT = json.loads((ROOT / "scenario.json").read_text())
TRACE = ROOT / "trace.jsonl"
_iteration = 0
_response_index = 0
_now = SCRIPT.get("start_time", 1000)


def record(kind, **fields):
    with TRACE.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": kind, **fields}, ensure_ascii=False) + "\n")


def config(key, default):
    # Janus supplies the declared boolean as bool; CeTTa can expose the PeTTa
    # symbol spelling. This is a typed host configuration field, not text.
    if str(key) == "spamShield" and str(default).lower() in ("true", "false"):
        default = str(default).lower() == "true"
    return SCRIPT.get("config", {}).get(str(key), default)


def clock():
    return _now


def timestamp():
    # Deliberately fixed in both engines, with the fixture clock recorded separately.
    return "2026-01-01 00:00:00"


def receive():
    events = SCRIPT.get("events", [])
    event = events[_iteration - 1] if _iteration <= len(events) else ""
    record("receive", event=event)
    return event


def provider(prompt, max_tokens, reasoning):
    global _response_index
    record("request", prompt=prompt, max_tokens=max_tokens, reasoning=reasoning)
    replies = SCRIPT["replies"]
    if _response_index >= len(replies):
        raise RuntimeError("fixture response script exhausted")
    reply = replies[_response_index]
    _response_index += 1
    if isinstance(reply, dict):
        record("provider_error", error=reply["raise"])
        raise RuntimeError(reply["raise"])
    return reply


def install_provider():
    """Register the current Omega tool-call provider against fixture replies."""
    import providers
    import tool_protocol

    class FixtureProvider:
        def start(self):
            return None

        def stop(self):
            return None

        def chat(self, request):
            global _response_index
            canonical = tool_protocol.request_from_omega(request)
            record("request", request=canonical,
                   tools=[item["function"]["name"] for item in canonical["tools"]],
                   max_tokens=canonical["max_tokens"],
                   reasoning=canonical["reasoning_effort"])
            replies = SCRIPT["replies"]
            if _response_index >= len(replies):
                raise RuntimeError("fixture response script exhausted")
            reply = replies[_response_index]
            _response_index += 1
            if isinstance(reply, dict) and "raise" in reply:
                record("provider_error", error=reply["raise"])
                raise RuntimeError(reply["raise"])
            calls = [tool_protocol.provider_call(tool_protocol.ToolCallV1(
                str(item.get("id", "")), str(item.get("name", "")),
                item.get("arguments", {}))) for item in reply.get("calls", [])]
            payload = {"choices": [{"message": {
                "role": "assistant", "tool_calls": calls}}]}
            return tool_protocol.response_to_omega(payload, providers)

    providers.registerLLMProvider("Fixture", FixtureProvider())
    return True


def heartbeat(iteration, loops, previous, results):
    global _iteration
    _iteration = iteration
    record("heartbeat", iteration=iteration, loops=loops, previous=previous,
           last_results=results, now=_now)
    return True


def sleep(seconds, loops, previous, results, wake):
    global _now
    history = ROOT / "repos/Omega/memory/history.metta"
    record("boundary", iteration=_iteration, loops=loops, previous=previous,
           last_results=results, wake=wake, now=_now,
           history=history.read_text() if history.exists() else "")
    _now += seconds
    if _iteration >= SCRIPT.get("iterations", 1):
        # Test-only process boundary after the upstream sleep point. The trace
        # has been flushed; no service or live process uses this adapter.
        os._exit(0)
    return True


def send(message):
    record("send", message=message)
    return True


def log(level, module, message):
    record("log", level=str(level), module=str(module), message=str(message))
    return True


def concat(values):
    return "".join(str(value) for value in values)


def get_prompt(provider):
    memory = ROOT / "repos/Omega/memory"
    path = memory / ("prompt_" + str(provider) + ".txt")
    if not path.exists():
        path = memory / "prompt.txt"
    return path.read_text() if path.exists() else ""


def get_history(limit):
    path = ROOT / "repos/Omega/memory/history.metta"
    if not path.exists():
        return ""
    content = path.read_bytes()
    return content[max(0, len(content) - limit):].decode("utf-8")


def string_replace(text, separators, replacement):
    # split_string/4 treats its separators argument as a set of characters;
    # atomic_list_concat joins the pieces. Padding is empty in utils.metta.
    return "".join(replacement if char in separators else char for char in text)


def noop(*args):
    return True


def snapshot_messages(messages):
    return [repr(message) for message in messages]

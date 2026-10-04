"""Host selection, admission and observations around the native MeTTa client.

This module performs no HTTP. Keys remain environment-owned host bytes;
only scoped credential aliases, ordinary headers and non-secret plans cross
the guest boundary. Upstream transformations remain upstream host helpers.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

# Standalone pinned-core runners need not configure the framework's PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cognitive_health
import llm_policy
import provider_protocol
import synthetic_llm

_foreign_events = []


def enabled():
    return "true" if (os.environ.get("METTACLAW_LLM_TRANSPORT", "python") == "native"
                      and os.environ.get("METTACLAW_ACTIVE_ENGINE") == "cetta") else "false"


def gate():
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        runtime_host.gate()
        runtime_host.provider_retry_boundary()
        return True
    import engine_modes
    import lifecycle
    if engine_modes.recycle_requested():
        raise SystemExit(0)
    # The independent control responder continues while cognition is paused.
    while not lifecycle.cognition_enabled():
        synthetic_llm.time.sleep(.25)
        if engine_modes.recycle_requested():
            raise SystemExit(0)
    return True


def _plan(kind, model, tokens, builder):
    ticket = None
    try:
        selected = synthetic_llm.current_model(str(model))
        provider = synthetic_llm._provider_for(selected)
        if not provider["key"]:
            raise RuntimeError("provider credential is not configured")
        endpoint, body = builder(provider["protocol"], provider.get("model", selected))
        ticket = llm_policy.admit(provider["name"], tokens)
        alias = "LLM_" + provider["name"].upper()
        prefix = "CETTA_IO_AUTH_" + alias
        credential_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                          "synthetic": "SYNTHETIC_API_KEY"}[provider["name"]]
        # This is native host configuration, never a guest-selected env name.
        os.environ[prefix + "_URL_PREFIX"] = provider["base"] + "/"
        os.environ[prefix + "_HEADER"] = "x-api-key" if provider["name"] == "anthropic" else "Authorization"
        os.environ[prefix + "_ENV"] = credential_env
        os.environ[prefix + "_PREFIX"] = "" if provider["name"] == "anthropic" else "Bearer "
        headers = [["http:header", "Content-Type", "application/json"], ["http:credential", alias]]
        if provider["name"] == "anthropic":
            headers.append(["http:header", "anthropic-version", "2023-06-01"])
        cognitive_health.turn_started(provider["name"])
        return SimpleNamespace(kind=kind, ready="true", correlation=ticket, protocol=provider["protocol"],
            provider=provider["name"], url=provider["base"]+endpoint, headers=headers,
            body=json.dumps(body, ensure_ascii=False), sent=False,
            timeout=int(synthetic_llm._float_env("SYNTHETIC_TIMEOUT", 120, 1)*1000),
            cap=int(os.environ.get("METTACLAW_LLM_RESPONSE_BYTES", 1048576)))
    except Exception as error:
        if ticket:
            llm_policy.fail(ticket, sent=False)
        return SimpleNamespace(kind=kind, ready="false", error=error,
                               correlation="unsubmitted", sent=False)


def command_plan(model, tokens, effort, prompt):
    return _plan("command", model, tokens, lambda protocol, selected:
        provider_protocol.command_body(protocol, selected, tokens, effort, prompt))


def structured_plan(request):
    value = getattr(request, "value", request)
    return _plan("structured", synthetic_llm.current_model(), value["max_tokens"],
        lambda protocol, selected: provider_protocol.structured_body(protocol, selected, value))


def field(plan, name):
    return getattr(plan, str(name))


def submitted(plan, transport_id):
    plan.sent = True
    plan.transport = int(transport_id)
    return True


def park(event):
    _foreign_events.append(event)
    return True


def take_foreign_events():
    events = list(_foreign_events)
    _foreign_events.clear()
    return events


def finish(plan, correlation, encoded):
    if str(correlation) != plan.correlation:
        return failed(plan, correlation, "correlation-error")
    result = json.loads(str(encoded))
    if not isinstance(result.get("usage"), dict):
        result["usage"] = {}
    if not llm_policy.complete(plan.correlation, result):
        return failed(plan, correlation, "duplicate-completion")
    result["transport"] = "native"
    synthetic_llm._completion_metadata(result)
    if plan.provider == "anthropic":
        synthetic_llm._tally_anthropic(result["usage"])
    if plan.kind == "structured":
        from iter_host import Box
        cognitive_health.turn_completed(len(str(encoded).encode()))
        return Box({"message": result["choices"][0]["message"],
                    "finish": result["choices"][0].get("finish_reason")})
    if result["choices"][0].get("finish_reason") == "length":
        return synthetic_llm._empty_action(synthetic_llm.OutputTruncated())
    content = result["choices"][0]["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        return synthetic_llm._empty_action(synthetic_llm.EmptyResponse())
    synthetic_llm._note_answered()
    cognitive_health.turn_completed(len(content.encode()))
    return content


def failed(plan, correlation, kind, status=0):
    if getattr(plan, "ready", "false") == "true":
        llm_policy.fail(plan.correlation, status=int(status) or None, sent=plan.sent)
    error = getattr(plan, "error", RuntimeError(str(kind)))
    if plan.kind == "command":
        return synthetic_llm._empty_action(error)
    cognitive_health.turn_failed(type(error).__name__, retry_after=getattr(error, "retry_after", 15))
    if llm_policy.configuration()["profile"] == "managed" and os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        runtime_host.sleep(min(getattr(error, "retry_after", 15), 15))
    raise RuntimeError("native provider failed: " + type(error).__name__) from None

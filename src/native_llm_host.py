"""Host selection, admission and observations around the native MeTTa client.

This module performs no HTTP. Keys remain environment-owned host bytes;
only scoped credential aliases, ordinary headers and non-secret plans cross
the guest boundary. Upstream transformations remain upstream host helpers.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import threading
import uuid

# Standalone pinned-core runners need not configure the framework's PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cognitive_health
import llm_policy
import provider_protocol
import synthetic_llm

_session_epoch = uuid.uuid4().hex
_claim_lock = threading.Lock()


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
        request = builder(provider["protocol"], provider.get("model", selected))
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
            epoch=_session_epoch, provider=provider["name"], base=provider["base"], headers=json.dumps(headers),
            request=json.dumps(request, ensure_ascii=False), sent=False, claimed=False,
            timeout=int(synthetic_llm._float_env("SYNTHETIC_TIMEOUT", 120, 1)*1000),
            cap=int(os.environ.get("METTACLAW_LLM_RESPONSE_BYTES", 1048576)))
    except Exception as error:
        if ticket:
            llm_policy.fail(ticket, sent=False)
        return SimpleNamespace(kind=kind, ready="false", error=error,
                               correlation="unsubmitted", sent=False)


def command_plan(model, tokens, effort, prompt):
    def conversation(protocol, selected):
        stable, user = provider_protocol.split_prompt(prompt)
        result = dict(model=selected, max_tokens=int(tokens), stable=stable, user=user,
                      reasoning={"effort": str(effort)})
        # Cache identity is an upstream caller policy, not provider encoding.
        if stable:
            result["cache_key"] = provider_protocol.cache_key("openai", selected, "".join(stable))
        return result
    return _plan("command", model, tokens, conversation)


def structured_plan(request):
    value = getattr(request, "value", request)
    def conversation(protocol, selected):
        result = copy.deepcopy(value)
        result["model"] = selected
        extra = result.pop("extra_body", {})
        if not isinstance(extra, dict):
            raise ValueError("extra_body must be an object")
        if protocol == "chat-completions":
            # Keep the imported compatible-provider request layout explicit.
            result["chat_profile"] = "compatible"
        if protocol in ("anthropic", "responses"):
            # The imported loops already use this mapping. The generic library
            # preserves developer roles unless a caller explicitly selects it.
            result["role_policy"] = "merge-system-developer"
        if protocol == "anthropic":
            thinking = extra.pop("enable_thinking", None)
            if thinking is False:
                raise ValueError("disabling thinking requires a model capability")
            if thinking is True:
                result["thinking"] = {"type": "adaptive"}
                result["tool_choice_policy"] = "auto-for-thinking"
            if "reasoning" in extra:
                result.setdefault("reasoning", extra.pop("reasoning"))
        # Native extensions are explicit. Known common options never get
        # overwritten by a provider-specific extra_body.
        extensions = result.pop("extensions", {})
        if not isinstance(extensions, dict):
            raise ValueError("extensions must be an object")
        if set(extensions).intersection(extra):
            raise ValueError("duplicate native extension")
        extensions.update(extra)
        if protocol == "responses":
            system = [m.get("content", "") for m in result["messages"]
                      if m.get("role") in ("system", "developer")]
            if system:
                extensions.setdefault("prompt_cache_key", provider_protocol.cache_key(
                    "openai", selected, "\n\n".join(system)))
        if extensions:
            result["extensions"] = extensions
        return result
    return _plan("structured", synthetic_llm.current_model(), value["max_tokens"],
                 conversation)


def field(plan, name):
    return getattr(plan, str(name))


def claim(plan):
    """One local submission authority; replaying the same plan cannot bill twice."""
    with _claim_lock:
        if plan.claimed:
            return "false"
        plan.claimed = True
        return "true"


def submitted(plan, transport_id):
    plan.sent = True
    plan.transport = int(transport_id)
    plan.submitted_at = time.monotonic()
    return True


def finish(plan, epoch, correlation, encoded, canonical):
    if str(epoch) != plan.epoch or str(correlation) != plan.correlation:
        return failed(plan, correlation, "correlation-error")
    result = json.loads(str(encoded))
    boundary = json.loads(str(canonical))
    reason = result["choices"][0].get("finish_reason", "unknown")
    if reason not in ("stop", "tool_calls", "length", "refusal", "content_filter"):
        return failed(plan, correlation, "unfinished-provider-response")
    if reason in ("refusal", "content_filter") and result["choices"][0]["message"].get("tool_calls"):
        return failed(plan, correlation, "refused-tool-call")
    if not isinstance(result.get("usage"), dict):
        result["usage"] = {}
    if not llm_policy.complete(plan.correlation, result):
        return failed(plan, correlation, "duplicate-completion")
    result["transport"] = "native"
    synthetic_llm._completion_metadata(result)
    if plan.provider == "anthropic":
        synthetic_llm._tally_anthropic(result["usage"])
    if plan.kind == "structured":
        if reason == "length" and result["choices"][0]["message"].get("tool_calls"):
            # The imported loop can dispatch calls before checking finish. Keep
            # that loop unchanged; reject this unsafe projection at its host
            # boundary after settling the authoritative usage observation.
            cognitive_health.turn_failed("OutputTruncated", retry_after=0)
            raise RuntimeError("native provider truncated a tool batch") from None
        from iter_host import Box
        cognitive_health.turn_completed(len(str(encoded).encode()))
        return Box({"message": result["choices"][0]["message"],
                    "finish": reason, "llm_result": boundary})
    if result["choices"][0].get("finish_reason") == "length":
        return synthetic_llm._empty_action(synthetic_llm.OutputTruncated())
    content = result["choices"][0]["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        return synthetic_llm._empty_action(synthetic_llm.EmptyResponse())
    synthetic_llm._note_answered()
    cognitive_health.turn_completed(len(content.encode()))
    return content


def duplicate_plan(plan):
    # Do not mutate the original plan's in-flight admission transaction.
    if plan.kind == "command":
        return synthetic_llm._empty_action(RuntimeError("duplicate native plan"))
    raise RuntimeError("duplicate native plan")


def _record_failure(plan, kind, status=0):
    """Keep diagnostic categories without logging request or response bytes."""
    known = {"admission-error", "submit-error", "transport-error", "decode-error",
             "http-status", "correlation-error", "duplicate-completion",
             "invalid-phase", "retired-handle", "unfinished-provider-response",
             "refused-tool-call"}
    code = str(kind)
    code = code if code in known else "provider-error"
    provider = getattr(plan, "provider", "unknown")
    if provider not in ("synthetic", "anthropic", "openai"):
        provider = "unknown"
    status = int(status)
    status = status if 100 <= status <= 599 else 0
    started = getattr(plan, "submitted_at", None)
    elapsed = max(0, int((time.monotonic() - started) * 1000)) if started is not None else 0
    deadline = int(getattr(plan, "timeout", 0))
    sent = "true" if getattr(plan, "sent", False) else "false"
    print(f"[native_llm] failed request: {code}; provider={provider}; "
          f"status={status}; sent={sent}; elapsed_ms={elapsed}; deadline_ms={deadline}",
          file=sys.stderr)


def failed(plan, correlation, kind, status=0):
    _record_failure(plan, kind, status)
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

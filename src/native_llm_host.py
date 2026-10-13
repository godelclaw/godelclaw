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
import native_lifetime
import provider_stream

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
    waiting = False
    while not lifecycle.cognition_enabled():
        if not waiting:
            import process_supervision
            process_supervision.progress("operator-wait", 60)
            waiting = True
        synthetic_llm.time.sleep(.25)
        if engine_modes.recycle_requested():
            raise SystemExit(0)
        # This bounded wait has returned and the operator latch was read
        # again. A frozen evaluator cannot reach this new wait boundary.
        if waiting and process_supervision._wait_deadline <= process_supervision.time.monotonic():
            process_supervision.progress("operator-wait", 60)
    if waiting:
        process_supervision.progress("operator-resume")
    return True


def _plan(kind, model, tokens, builder):
    ticket = None
    provider = None
    try:
        selected = synthetic_llm.current_model(str(model))
        provider = synthetic_llm._provider_for(selected)
        if not provider["key"]:
            raise RuntimeError("provider credential is not configured")
        request = builder(provider["protocol"], provider.get("model", selected))
        if provider["name"] == "synthetic":
            options = dict(request)
            for field in ("reasoning", "reasoning_effort"):
                if field in request.get("extensions", {}):
                    if field in options:
                        raise ValueError("conflicting Synthetic reasoning extension")
                    options[field] = request["extensions"][field]
            provider_protocol.synthetic_body(options)  # validate before budget admission
        reserve = {}
        if (os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE") and
                os.environ.get("METTACLAW_ACTIVITY_CALL_LANE") == "background"):
            reserve = dict(reserved_output=int(os.environ.get("METTACLAW_ACTIVITY_RESERVED_OUTPUT", "0")),
                           reserved_requests=int(os.environ.get("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", "1")))
        ticket = llm_policy.admit(provider["name"], tokens,
            request_fingerprint=llm_policy.fingerprint(request), **reserve)
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
        settings = wait_settings()
        stream = provider["protocol"] == "chat-completions" and settings["stream"]
        cap = int(os.environ.get("METTACLAW_LLM_RESPONSE_BYTES", 8 * 1048576 if stream else 1048576))
        if not 1024 <= cap <= 16 * 1048576:
            raise ValueError("model response bound must be between 1 KiB and 16 MiB")
        import strategy_host
        captured_guard = strategy_host.guard() if strategy_host.enabled() else None
        cognitive_health.turn_started(provider["name"])
        return SimpleNamespace(kind=kind, ready="true", correlation=ticket, protocol=provider["protocol"],
            activity=os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE", ""),
            lane=os.environ.get("METTACLAW_ACTIVITY_CALL_LANE", ""),
            epoch=_session_epoch, provider=provider["name"], base=provider["base"], headers=json.dumps(headers),
            request=json.dumps(request, ensure_ascii=False), sent=False, claimed=False,
            guard=captured_guard, stream=stream,
            timeout=settings["hard"], connect=settings["connect"], first=settings["first"],
            idle=settings["idle"], soft=settings["soft"],
            cap=cap)
    except Exception as error:
        if ticket:
            llm_policy.fail(ticket, sent=False)
        return SimpleNamespace(kind=kind, ready="false", error=error,
                               provider=provider["name"] if provider else "unknown",
                               correlation="unsubmitted", sent=False)


def command_plan(model, tokens, effort, prompt):
    def conversation(protocol, selected):
        stable, user = provider_protocol.split_prompt(prompt)
        result = dict(model=selected, max_tokens=int(tokens), stable=stable, user=user)
        if str(effort) not in ("default", "", "None"):
            result["reasoning"] = {"effort": str(effort)}
        # Cache identity is an upstream caller policy, not provider encoding.
        if stable:
            result["cache_key"] = provider_protocol.cache_key("openai", selected, "".join(stable))
        return result
    plan = _plan("command", model, tokens, conversation)
    plan.prompt_layout = provider_protocol.prompt_observation(prompt)
    plan.prompt_text = str(prompt)
    return plan


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
        if not native_lifetime.claim(plan):
            llm_policy.fail(plan.correlation, sent=False)
            return "false"
        plan.claimed = True
        return "true"


def submitted(plan, transport_id):
    plan.sent = True
    plan.transport = int(transport_id)
    plan.submitted_at = time.monotonic()
    import process_supervision
    process_supervision.provider_wait(plan.timeout / 1000)
    native_lifetime.submitted(plan, transport_id)
    return True


def owns(plan, epoch, correlation, transport):
    return "true" if (str(epoch) == plan.epoch and str(correlation) == plan.correlation
                       and int(transport) == getattr(plan, "transport", None)) else "false"


def wait_settings():
    """The old provider timeout becomes a soft wait for native callers."""
    def milliseconds(name, default, allow_zero=False):
        value = float(os.environ.get(name, str(default)))
        if not (0 if allow_zero else 0.001) <= value <= 2147483:
            raise ValueError("invalid model wait setting")
        return int(value * 1000)
    soft = milliseconds("METTACLAW_LLM_SOFT_SECONDS", os.environ.get("SYNTHETIC_TIMEOUT", "300"))
    hard = milliseconds("METTACLAW_LLM_HARD_SECONDS", 1800)
    if hard < soft:
        raise ValueError("hard model deadline precedes the soft wait")
    return dict(soft=soft, hard=hard,
                connect=milliseconds("METTACLAW_LLM_CONNECT_SECONDS", 30),
                first=milliseconds("METTACLAW_LLM_FIRST_BYTE_SECONDS", 900),
                idle=milliseconds("METTACLAW_LLM_IDLE_SECONDS", 180),
                stream=os.environ.get("METTACLAW_LLM_STREAM", "0") == "1")


def wire_body(plan, body):
    value = json.loads(str(body))
    if plan.provider == "synthetic":
        value = provider_protocol.synthetic_body(value)
    if plan.stream:
        value.update(stream=True, stream_options={"include_usage": True})
    return json.dumps(value, ensure_ascii=False)


def decoded_body(plan, body):
    try:
        value = provider_stream.chat(str(body), plan.cap) if plan.stream else str(body)
        return json.dumps({"ok": True, "body": value})
    except (ValueError, TypeError, KeyError, RecursionError):
        return json.dumps({"ok": False})


def waiting(plan, count=0, quiet=0):
    """An admitted call settles before routine recycling or a pause boundary.

    The independent responder still handles /stop and /activity; the broker
    rejects effects when paused or steered. No new model request is made here.
    """
    now = time.monotonic()
    soft = int((now - plan.submitted_at) * 1000 >= plan.soft)
    if (now - getattr(plan, "last_progress_record", -float("inf")) < 2
            and soft == getattr(plan, "last_soft", None)):
        return 1
    plan.last_progress_record = now
    plan.last_soft = soft
    native_lifetime.progress(plan, count, quiet, soft)
    cognitive_health.provider_progress(count, quiet, soft, plan.timeout)
    import process_supervision
    process_supervision.provider_data(count)
    return 1


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
    import strategy_host
    native_lifetime.settle(plan, "completed", str(canonical))
    result["transport"] = "native"
    synthetic_llm._completion_metadata(result, getattr(plan, "prompt_layout", None))
    if hasattr(plan, "prompt_text"):
        import context_budget
        context_budget.observe(plan.prompt_text, result.get("usage", {}))
    if plan.provider == "anthropic":
        synthetic_llm._tally_anthropic(result["usage"])
    if plan.kind == "structured":
        from iter_host import Box
        cognitive_health.turn_completed(len(str(encoded).encode()))
        return Box({"message": result["choices"][0]["message"],
                    "finish": reason, "llm_result": boundary})
    content = result["choices"][0]["message"].get("content")
    if reason == "length":
        import helper
        content = helper.complete_command_prefix(content or "")
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
             "refused-tool-call", "incomplete-stream", "ownership-error"}
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
    import admission_frontier
    error = getattr(plan, "error", None)
    reason = error.reason if isinstance(error, llm_policy.AdmissionDenied) else admission_frontier.failure_code(error)
    detail = "; reason=" + reason if (reason in admission_frontier.DENIAL_CODES or
                                     reason == "provider-outcome-unknown") else ""
    print(f"[native_llm] failed request: {code}; provider={provider}; "
          f"status={status}; sent={sent}; elapsed_ms={elapsed}; deadline_ms={deadline}{detail}",
          file=sys.stderr)


def response_retry_after(plan, seconds):
    value = float(seconds)
    plan.retry_after = value if value >= 0 else None
    return 1

def failed(plan, correlation, kind, status=0):
    _record_failure(plan, kind, status)
    if getattr(plan, "ready", "false") == "true":
        llm_policy.fail(plan.correlation, status=int(status) or None, sent=plan.sent,
                        retry_after=getattr(plan, "retry_after", None))
        uncertain = plan.sent and str(kind) == "transport-error"
        native_lifetime.settle(plan, "uncertain" if uncertain else "failed", reason=str(kind)[:64])
    error = getattr(plan, "error", TimeoutError(str(kind)) if str(kind) == "transport-error" else RuntimeError(str(kind)))
    error.status = int(status) or None
    if getattr(plan, "retry_after", None) is not None:
        error.retry_after = plan.retry_after
    if plan.kind == "command":
        return synthetic_llm._empty_action(error)
    synthetic_llm._empty_action(error)
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        wait = cognitive_health.retry_wait_seconds()
        if wait:
            runtime_host.sleep(wait)
    raise RuntimeError("native provider failed: " + type(error).__name__) from None

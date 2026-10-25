"""Host selection, admission and observations around the native MeTTa client.

This module performs no HTTP. Keys remain environment-owned host bytes;
only scoped credential aliases, ordinary headers and non-secret plans cross
the guest boundary. Upstream transformations remain upstream host helpers.
"""
import copy
import hashlib
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
import model_commentary
import stream_sink

_session_epoch = uuid.uuid4().hex
_claim_lock = threading.Lock()


def call_scope():
    configured = os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE", "")
    if configured or os.environ.get("METTACLAW_OBSERVED_STRATEGY") != "1":
        return configured
    identity = os.environ.get("METTACLAW_STRATEGY_PATH") or os.environ.get("METTACLAW_ENGINE_STATE_PATH", "")
    return "agent-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def call_lane():
    return os.environ.get("METTACLAW_ACTIVITY_CALL_LANE") or ("interactive" if call_scope() else "")


def enabled():
    return "true" if (os.environ.get("METTACLAW_LLM_TRANSPORT", "python") == "native"
                      and os.environ.get("METTACLAW_ACTIVE_ENGINE") == "cetta") else "false"


def coding_session():
    """Coding scopes outlive the agent process and have their own stop/resume.

    An engine or mode recycle belongs to the cognitive service. Applying it to
    an independent coding scope used to kill a useful session mid-task.
    """
    return os.environ.get("METTACLAW_CODING_SESSION") == "1"


def gate():
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        runtime_host.gate()
        runtime_host.provider_retry_boundary()
        return True
    import engine_modes
    import lifecycle
    if not coding_session() and engine_modes.recycle_requested():
        raise SystemExit(0)
    # The independent control responder continues while cognition is paused.
    waiting = False
    while not lifecycle.cognition_enabled():
        if not waiting:
            import process_supervision
            process_supervision.progress("operator-wait", 60)
            waiting = True
        synthetic_llm.time.sleep(.25)
        if not coding_session() and engine_modes.recycle_requested():
            raise SystemExit(0)
        # This bounded wait has returned and the operator latch was read
        # again. A frozen evaluator cannot reach this new wait boundary.
        if waiting and process_supervision._wait_deadline <= process_supervision.time.monotonic():
            process_supervision.progress("operator-wait", 60)
    if waiting:
        process_supervision.progress("operator-resume")
    return True


def _plan(kind, model, tokens, builder, *, native_ledger=False):
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
        if call_scope() and call_lane() == "background":
            reserve = dict(reserved_output=int(os.environ.get("METTACLAW_ACTIVITY_RESERVED_OUTPUT", "0")),
                           reserved_requests=int(os.environ.get("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", "1")))
        fingerprint = llm_policy.fingerprint(request)
        if native_ledger:
            if any(value < 0 for value in reserve.values()):
                raise ValueError("reserved capacity must be nonnegative")
            ticket = uuid.uuid4().hex
        else:
            ticket = llm_policy.admit(provider["name"], tokens,
                request_fingerprint=fingerprint, **reserve)
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
        return SimpleNamespace(kind=kind, encoding="default", ready="true", correlation=ticket, protocol=provider["protocol"],
            native_ledger=bool(native_ledger), max_output=int(tokens),
            request_fingerprint=fingerprint, wire_model=str(request.get("model", selected)),
            activity=call_scope(), lane=call_lane(),
            epoch=_session_epoch, provider=provider["name"], base=provider["base"], headers=json.dumps(headers),
            request=json.dumps(request, ensure_ascii=False), sent=False, claimed=False,
            guard=captured_guard, stream=stream, call_origin=strategy_host.accounting_origin(),
            timeout=settings["hard"], connect=settings["connect"], first=settings["first"],
            idle=settings["idle"], soft=settings["soft"],
            cap=cap)
    except Exception as error:
        if ticket and not native_ledger:
            llm_policy.fail(ticket, sent=False)
        return SimpleNamespace(kind=kind, encoding="default", ready="false", error=error,
                               native_ledger=bool(native_ledger),
                               provider=provider["name"] if provider else "unknown",
                               correlation="unsubmitted", sent=False)


def command_plan(model, tokens, effort, prompt, *, native_ledger=False):
    def conversation(protocol, selected):
        stable, user = provider_protocol.split_prompt(prompt)
        if protocol == "anthropic":
            # Keep complete transcript chunks as separate wire blocks. The
            # final wire adapter also marks the earlier stable region ends.
            messages = [{"role": "system", "content": block}
                        for part in stable for block in provider_protocol.chunk_blocks(part)]
            messages.append({"role": "user", "content": user})
            result = dict(model=selected, max_tokens=int(tokens), messages=messages, cache_system=True)
            ttl = os.environ.get("METTACLAW_ANTHROPIC_CACHE_TTL", "5m")
            if ttl not in ("5m", "1h"):
                raise ValueError("Anthropic cache TTL must be 5m or 1h")
            if ttl != "5m":
                result["cache_ttl"] = ttl
        elif model_commentary.enabled():
            stable = provider_protocol.joined(stable)
            messages = ([{"role": "system", "content": "".join(stable)}] if stable else [])
            messages.append({"role": "user", "content": user})
            result = dict(model=selected, max_tokens=int(tokens), messages=messages,
                          role_policy="merge-system-developer")
            if stable and protocol == "responses":
                result["extensions"] = {"prompt_cache_key":
                    provider_protocol.cache_key("openai", selected, stable[0])}
        else:
            stable = provider_protocol.joined(stable)
            result = dict(model=selected, max_tokens=int(tokens), stable=stable, user=user)
            # Cache identity is an upstream caller policy, not provider encoding.
            if stable:
                result["cache_key"] = provider_protocol.cache_key("openai", selected, stable[0])
        if str(effort) not in ("default", "", "None"):
            result["reasoning"] = {"effort": str(effort)}
        if model_commentary.enabled():
            result.update(tools=[model_commentary.tool_schema()], tool_choice="auto")
        return result
    plan = _plan("command", model, tokens, conversation, native_ledger=native_ledger)
    if getattr(plan, "protocol", "") == "anthropic" or model_commentary.enabled():
        plan.encoding = "messages"
    plan.prompt_layout = provider_protocol.prompt_observation(prompt)
    plan.cache_boundaries = provider_protocol.cache_boundaries(provider_protocol.split_prompt(prompt)[0])
    plan.prompt_text = str(prompt)
    return plan


def structured_json_plan(text):
    """A structured request given as JSON text, as the coding loop builds it."""
    return structured_plan(json.loads(str(text)))


def native_command_plan(model, tokens, effort, prompt):
    return command_plan(model, tokens, effort, prompt, native_ledger=True)


def native_structured_plan(request):
    return structured_plan(request, native_ledger=True)


def native_structured_json_plan(text):
    return structured_plan(json.loads(str(text)), native_ledger=True)


def ticket():
    """An identifier source, with no policy or database operation."""
    return uuid.uuid4().hex


def native_accounting(plan):
    return "true" if getattr(plan, "native_ledger", False) else "false"


def begin_native_claim(plan):
    """Protect an interop object from simultaneous/replayed begin calls."""
    with _claim_lock:
        if getattr(plan, "claim_started", False) or getattr(plan, "claimed", False):
            return "false"
        if not hasattr(plan, "call_origin"):
            import strategy_host
            plan.call_origin = strategy_host.accounting_origin()
        plan.claim_started = True
    return "true"


def policy_metadata(plan):
    """A small facts frame. Never parse or copy the prompt/request here."""
    started = getattr(plan, "submitted_at", None)
    soft = int(started is not None and
               (time.monotonic() - started) * 1000 >= getattr(plan, "soft", float("inf")))
    context = getattr(plan, "call_context", {})
    return json.dumps(dict(id=plan.correlation, epoch=plan.epoch,
        provider="chatgpt" if getattr(plan, "request_profile", "") == "chatgpt-plan" else plan.provider,
        maximum=getattr(plan, "max_output", 0), fingerprint=getattr(plan, "request_fingerprint", ""),
        model=context.get("model", getattr(plan, "wire_model", "")), protocol=plan.protocol,
        purpose=getattr(plan, "purpose", "turn"), activity=getattr(plan, "activity", ""),
        lane=getattr(plan, "lane", ""), guard=getattr(plan, "guard", None), cap=plan.cap,
        sent=bool(getattr(plan, "sent", False)), soft_elapsed=soft,
        progress_soft=getattr(plan, "last_soft", 0),
        retry_after=getattr(plan, "retry_after", None),
        origin=getattr(plan, "call_origin", {})), separators=(",", ":"))


def request_metadata(plan):
    """One request observation, separate from the small progress facts frame."""
    value = json.loads(policy_metadata(plan))
    value["origin"] = request_origin(plan)
    return json.dumps(value, separators=(",", ":"))


def request_origin(plan):
    """Cache evidence needs no native lease fields on fallback wire plans."""
    origin = dict(getattr(plan, "call_origin", {}) or {})
    if hasattr(plan, "cache_layout"):
        origin["cache_layout"] = plan.cache_layout
    return origin


def note_admitted(plan):
    try:
        import strategy_host
        strategy_host.provider_admitted(plan.correlation)
        return "true"
    except Exception as error:
        plan.error = error
        return "false"


def claim_effect(plan):
    plan.claimed = True
    try:
        stream_sink.begin(plan)
        model_commentary.begin(plan)
        return "true"
    except Exception as error:
        plan.error = error
        return "false"


def policy_rejected(plan, reason, retry=-1):
    code = str(reason)
    if code == "provider-cooldown":
        plan.error = llm_policy.AdmissionDenied(code, float(retry))
    elif not getattr(plan, "error", None):
        plan.error = RuntimeError("native provider accounting failed")
    plan.ready = "false"
    return "false"


def policy_committed(plan, evidence):
    usage = json.loads(str(evidence))
    with _claim_lock:
        if getattr(plan, "native_publication", "") in ("ready", "published"):
            return 0
        plan.native_publication = "ready"
        plan.native_usage = usage
    return 1


def rejection_code(plan):
    return getattr(plan, "command_rejection_code", "")


def accounting_observation_error(plan, stage):
    # A progress write is not submission authority. Keep the owned HTTP call
    # attached; terminal usage must still commit before any commands return.
    code = str(stage) if str(stage) in ("submitted", "progress", "completion", "rejection", "failure") else "metadata"
    plan.accounting_observation_error = code
    print("[native_accounting] ledger observation failed; stage=" + code, file=sys.stderr)
    return 0


class CompletionConflict(RuntimeError):
    """A received answer has no matching owned local receipt."""


def completion_duplicate(plan):
    if plan.kind == "command" and not _compaction(plan):
        return "()"
    raise CompletionConflict("completion already delivered; provider request not replayed")


def completion_wait(plan, attempt):
    """Only pace local recording; the MeTTa continuation owns its answer."""
    attempt = max(0, int(attempt))
    if attempt == 0:
        print("[native_accounting] received answer retained; waiting for local storage; call=" +
              plan.correlation, file=sys.stderr)
    if not _compaction(plan):
        cognitive_health.result_recording(attempt)
    try:
        import process_supervision
        process_supervision.progress("local-commit-wait", 60)
    except Exception:
        pass  # Observation storage must not discard the held provider answer.
    time.sleep(min(5.0, .1 * 2 ** min(attempt, 6)))
    return 1


def completion_conflict(plan):
    plan.error = CompletionConflict("local completion receipt conflict; provider request not replayed")
    return failed(plan, plan.correlation, "local-completion-conflict")


def for_compaction(plan):
    """Reuse the selected provider and its admission, without claiming a turn.

    This also works with subscription command plans: their wire encoding stays
    unchanged, while their complete plain-text reply is returned as a message.
    """
    plan.purpose = "compaction"
    return plan


def for_coding(plan):
    plan.purpose = "coding"
    return plan


def _compaction(plan):
    return getattr(plan, "purpose", None) == "compaction"


def _box(value):
    """The box iter mode unwraps; outside iter mode, such as in a coding
    session, iter_host is not importable and a plain holder does the same."""
    try:
        from iter_host import Box
    except ImportError:
        return SimpleNamespace(value=value)
    return Box(value)


def structured_json_result(box, path):
    """Write a structured call's message, finish reason and usage as JSON to a file.

    A file, because text returned through py-call becomes an interned symbol
    in the PeTTa lane, and model replies are large and never repeat."""
    value = getattr(box, "value", box)
    payload = {"message": value.get("message"), "finish": value.get("finish"),
               "usage": value.get("usage") or {}}
    if "call_id" in value:
        payload["call_id"] = value["call_id"]
    text = json.dumps(payload, ensure_ascii=False)
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.replace(temporary, str(path))
    return 1


def compaction_failure_result(plan, path):
    """Preserve maintenance error metadata without passing Error as a value."""
    failure = getattr(plan, "compaction_failure", dict(
        retryable=False, retry_after=-1, reason="local-provider-failure"))
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"compaction_failure": failure}, stream)
    os.replace(temporary, str(path))
    return 1


def structured_plan(request, *, native_ledger=False):
    value = getattr(request, "value", request)
    def conversation(protocol, selected):
        result = copy.deepcopy(value)
        result["model"] = selected
        if protocol != "anthropic":
            # Other providers reuse a repeated prefix without breakpoints.
            result.pop("cache_messages", None)
            result.pop("cache_ttl", None)
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
                 conversation, native_ledger=native_ledger)


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
        stream_sink.begin(plan)
        model_commentary.begin(plan)
        return "true"


def submitted(plan, transport_id):
    plan.sent = True
    plan.transport = int(transport_id)
    plan.submitted_at = time.monotonic()
    import process_supervision
    process_supervision.provider_wait(plan.timeout / 1000)
    if not getattr(plan, "native_ledger", False):
        native_lifetime.submitted(plan, transport_id)
    if not _compaction(plan):
        cognitive_health.turn_started(plan.provider)
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
    plan.call_context = dict(
        call_id=plan.correlation,
        provider="chatgpt" if getattr(plan, "request_profile", "") == "chatgpt-plan" else plan.provider,
        model=str(value.get("model", "")), purpose=getattr(plan, "purpose", "turn"))
    if plan.provider == "synthetic":
        value = provider_protocol.synthetic_body(value)
    if stream_sink.configured():
        value["stream"] = True
        if plan.protocol == "responses":
            value.setdefault("reasoning", {})["summary"] = "auto"
        elif plan.protocol == "anthropic":
            value.setdefault("thinking", {"type": "adaptive"})["display"] = "summarized"
        plan.stream = True
        plan.display_model = str(value.get("model", ""))
        stream_sink.emit(dict(kind="attempt.open", attempt=plan.correlation,
            item=plan.correlation, model=plan.display_model, provider=plan.provider,
            status="awaiting", epoch=getattr(plan, "display_epoch", "initial")))
    if plan.stream and plan.protocol == "chat-completions":
        value.update(stream=True, stream_options={"include_usage": True})
    if plan.protocol == "anthropic":
        if hasattr(plan, "cache_boundaries"):
            provider_protocol.anthropic_cache_system(value, plan.cache_boundaries)
        plan.cache_layout = provider_protocol.anthropic_cache_observation(value)
        plan.cache_layout["regions"] = list(getattr(plan, "cache_boundaries", ()))
    if not getattr(plan, "native_ledger", False):
        origin = request_origin(plan)
        llm_policy.describe(plan.correlation, model=plan.call_context["model"],
                            protocol=plan.protocol, purpose=plan.call_context["purpose"], origin=origin or None)
    return json.dumps(value, ensure_ascii=False)


def decoded_body(plan, body):
    try:
        text = str(body)
        streamed = plan.stream and not text.lstrip().startswith("{")
        if streamed and plan.protocol == "anthropic":
            value = provider_stream.anthropic(text, plan.cap)
        else:
            value = provider_stream.chat(text, plan.cap) if streamed else text
        return json.dumps({"ok": True, "body": value})
    except (ValueError, TypeError, KeyError, RecursionError):
        return json.dumps({"ok": False})


def commentary_readable(plan):
    observer = getattr(plan, "commentary_stream", None)
    return "true" if observer and not observer.disabled and not observer.prefix.done else "false"


def commentary_offset(plan):
    return plan.commentary_stream.offset


def commentary_chunk(plan, offset, text, phase):
    observer = getattr(plan, "commentary_stream", None)
    if observer is None or observer.disabled:
        return 1
    try:
        offset, text = int(offset), str(text)
        if (str(phase) not in ("waiting", "complete") or
                offset != observer.offset + len(text.encode("utf-8"))):
            raise ValueError("invalid observer cursor")
        observer.offset = offset
        observer.feed(text)
    except (ValueError, TypeError, UnicodeError):
        observer.disabled = True
    return 1


def commentary_unavailable(plan):
    observer = getattr(plan, "commentary_stream", None)
    if observer:
        observer.disabled = True
    return 1


def waiting(plan, count=0, quiet=0):
    """An admitted call settles before routine recycling or a pause boundary.

    The independent responder still handles /stop and /activity; the broker
    rejects effects when paused or steered. No new model request is made here.
    """
    now = time.monotonic()
    plan.progress_record_due = False
    soft = int((now - plan.submitted_at) * 1000 >= plan.soft)
    if (now - getattr(plan, "last_progress_record", -float("inf")) < 2
            and soft == getattr(plan, "last_soft", None)):
        return 1
    plan.last_progress_record = now
    plan.last_soft = soft
    plan.progress_record_due = True
    if not getattr(plan, "native_ledger", False):
        native_lifetime.progress(plan, count, quiet, soft)
    if not _compaction(plan):
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
    if getattr(plan, "native_ledger", False):
        with _claim_lock:
            accounted = getattr(plan, "native_publication", "") == "ready"
            if accounted:
                plan.native_publication = "published"
        if not accounted:
            return failed(plan, correlation, "uncommitted-native-accounting")
    elif not llm_policy.complete(plan.correlation, result):
        return failed(plan, correlation, "duplicate-completion")
    import strategy_host
    if not getattr(plan, "native_ledger", False):
        native_lifetime.settle(plan, "completed", str(canonical))
    result["transport"] = "native"
    try:
        if not _compaction(plan):
            synthetic_llm._completion_metadata(result, getattr(plan, "prompt_layout", None),
                                               call_context=getattr(plan, "call_context", None),
                                               usage_evidence=getattr(plan, "native_usage", None))
        if getattr(plan, "prompt_text", None) and not _compaction(plan):
            import context_budget
            context_budget.observe(plan.prompt_text, result.get("usage", {}))
        if plan.provider == "anthropic":
            synthetic_llm._tally_anthropic(result["usage"])
    except Exception:
        print("[native_llm] completion observation unavailable; committed answer retained", file=sys.stderr)
    if _compaction(plan):
        stream_sink.final(plan, reason=reason)
        return _box({"message": result["choices"][0]["message"], "finish": reason,
                     "llm_result": boundary, "usage": result.get("usage", {}),
                     "call_id": plan.correlation})
    if plan.kind == "structured":
        stream_sink.final(plan, result["choices"][0]["message"], reason)
        cognitive_health.turn_completed(len(str(encoded).encode()))
        return _box({"message": result["choices"][0]["message"],
                     "finish": reason, "llm_result": boundary, "usage": result.get("usage", {})})
    content = result["choices"][0]["message"].get("content")
    try:
        content = model_commentary.finish(plan, content, reason, result["choices"][0]["message"])
    except model_commentary.OutputError as error:
        stream_sink.final(plan, result["choices"][0]["message"], "rejected")
        code = {"unfinished command output": "incomplete-output",
                "invalid native tool output": "invalid-native-tool",
                "refused or structured command output": "refused-or-structured-output"}.get(str(error), "invalid-envelope")
        if getattr(plan, "native_ledger", False):
            plan.command_rejection_code = code
        else:
            native_lifetime.command_output_rejected(plan, code)
        observed_prompt = str(getattr(plan, "prompt_text", ""))
        print("[command_contract] rejected; code=" + code +
              "; feedback_visible=" + str("PREVIOUS_COMMAND_OUTPUT_REJECTED" in observed_prompt).lower() +
              "; footer_visible=" + str("REQUIRED_COMMAND_OUTPUT" in observed_prompt).lower(), file=sys.stderr)
        return synthetic_llm._empty_action(error)
    if reason == "length":
        import helper
        content = helper.complete_command_prefix(content or "")
    if not isinstance(content, str) or not content.strip():
        return synthetic_llm._empty_action(synthetic_llm.EmptyResponse())
    synthetic_llm._note_answered()
    stream_sink.final(plan, result["choices"][0]["message"], reason)
    cognitive_health.turn_completed(len(content.encode()))
    return content


def duplicate_plan(plan):
    # Do not mutate the original plan's in-flight admission transaction.
    if plan.kind == "command" and not _compaction(plan):
        return synthetic_llm._empty_action(RuntimeError("duplicate native plan"))
    raise RuntimeError("duplicate native plan")


def _one_line(text, limit):
    text = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return text if len(text) <= limit else text[:limit] + "…"


def provider_error(plan, body):
    """The provider's own code and message for a rejected request, as Codex
    shows them. Request bytes are never logged; long bodies are cut."""
    text = str(body)
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    code = message = ""
    if isinstance(value, dict):
        error = value.get("error", value)
        if isinstance(error, dict):
            code = str(error.get("code") or error.get("type") or "")
            message = str(error.get("message") or "")
        if not message and isinstance(value.get("detail"), str):
            message = value["detail"]
    elif text.strip():
        message = text
    plan.provider_error = (_one_line(code, 64), _one_line(message, 400))
    return 1


def _record_failure(plan, kind, status=0):
    """Keep diagnostic categories and the provider's stated reason, without
    logging request or response bytes."""
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
    stated_code, stated = getattr(plan, "provider_error", ("", ""))
    detail += ("; code=" + stated_code if stated_code else "") + ("; message=" + stated if stated else "")
    print(f"[native_llm] failed request: {code}; provider={provider}; "
          f"status={status}; sent={sent}; elapsed_ms={elapsed}; deadline_ms={deadline}{detail}",
          file=sys.stderr)


def response_retry_after(plan, seconds):
    value = float(seconds)
    plan.retry_after = value if value >= 0 else None
    return 1

def failed(plan, correlation, kind, status=0):
    stream_sink.final(plan, reason=str(kind))
    _record_failure(plan, kind, status)
    if getattr(plan, "ready", "false") == "true" and not getattr(plan, "native_ledger", False):
        llm_policy.fail(plan.correlation, status=int(status) or None, sent=plan.sent,
                        retry_after=getattr(plan, "retry_after", None))
        uncertain = plan.sent and str(kind) == "transport-error"
        native_lifetime.settle(plan, "uncertain" if uncertain else "failed", reason=str(kind)[:64])
    error = getattr(plan, "error", TimeoutError(str(kind)) if str(kind) == "transport-error" else RuntimeError(str(kind)))
    error.status = int(status) or None
    if getattr(plan, "retry_after", None) is not None:
        error.retry_after = plan.retry_after
    if _compaction(plan):
        # A received transient HTTP error is retryable. An uncertain remote
        # outcome stays accounted and is not automatically submitted again.
        status_code = int(status)
        plan.compaction_failure = dict(
            retryable=(status_code == 429 or status_code >= 500 or
                       isinstance(error, llm_policy.AdmissionDenied) and error.retry_after > 0),
            retry_after=getattr(error, "retry_after", -1), reason="provider-failure")
        raise RuntimeError("context compaction provider failed: " + type(error).__name__) from None
    if plan.kind == "command":
        return synthetic_llm._empty_action(error)
    synthetic_llm._empty_action(error)
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        wait = cognitive_health.retry_wait_seconds()
        if wait:
            runtime_host.sleep(wait)
    if synthetic_llm._rejected_status(error.status):
        raise RuntimeError("native provider rejected the request: HTTP %d" % error.status) from None
    raise RuntimeError("native provider failed: " + type(error).__name__) from None

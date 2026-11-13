"""Host facts and effect observers around the native MeTTa call owner.

This module performs no HTTP, admission or request preparation. Keys remain
environment-owned host bytes; only scoped credential aliases, ordinary headers
and small call facts cross the guest boundary.
"""
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
import synthetic_llm
import model_commentary
import stream_sink

_session_epoch = uuid.uuid4().hex
_claim_lock = threading.Lock()


def capture_request(plan, body):
    """Opt-in local wire capture; never log headers or affect call authority."""
    health = os.environ.get("METTACLAW_COGNITIVE_HEALTH_PATH")
    if not health:
        return
    directory = Path(health).parent / "request-capture"
    control = directory / "control.json"
    temporary = None
    try:
        if not control.is_file():
            return
        import fcntl
        import tempfile
        # The counter bounds diagnostic retention, not the agent's work.
        with control.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            setting = json.load(stream)
            remaining = setting.get("remaining")
            purpose = getattr(plan, "purpose", "turn")
            if type(remaining) is not int or remaining <= 0 or setting.get("purpose") != purpose:
                return
            record = dict(call_id=plan.correlation, protocol=plan.protocol,
                          purpose=purpose, captured_at=time.time(), wire_body=body)
            fd, temporary = tempfile.mkstemp(dir=directory, prefix=".capture-")
            with os.fdopen(fd, "w", encoding="utf-8") as captured:
                os.fchmod(captured.fileno(), 0o660)
                json.dump(record, captured, ensure_ascii=False)
            os.replace(temporary, directory / (uuid.uuid4().hex + ".json"))
            temporary = None
            setting["remaining"] = remaining - 1
            stream.seek(0)
            json.dump(setting, stream)
            stream.truncate()
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        # Missing storage or a broken diagnostic switch cannot suppress work.
        pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def observe_wire(plan, body):
    """Observe after the subscription profile too; return the same text."""
    capture_request(plan, str(body))
    return body


def call_scope():
    configured = os.environ.get("METTACLAW_ACTIVITY_CALL_SCOPE", "")
    if configured or os.environ.get("METTACLAW_OBSERVED_STRATEGY") != "1":
        return configured
    identity = os.environ.get("METTACLAW_STRATEGY_PATH") or os.environ.get("METTACLAW_ENGINE_STATE_PATH", "")
    return "agent-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def call_lane():
    return os.environ.get("METTACLAW_ACTIVITY_CALL_LANE") or ("interactive" if call_scope() else "")


def native_classified(plan, status, program, commentary, code, publish):
    """Attach the pure native decision to this plan's existing settlement."""
    if status not in ("accepted", "rejected"):
        raise ValueError("invalid native command classification")
    plan.native_command_classification = {
        "status": status, "program": str(program), "commentary": str(commentary),
        "code": str(code), "publish": str(publish) == "true"}
    return 1


def request_allowed():
    import provider_rejection
    return "false" if provider_rejection.blocked() else "true"


def rejection_hold():
    """A retained rejection's hold on the loop's wait (src/rejection_gate_cetta.metta).

    "repair" when a context repair is pending, "blocked" while the block waits
    for corrective input, "clear" when corrective state released it or there
    is none. The native gate asks only when a block is possible.
    """
    import provider_rejection
    if not provider_rejection.blocked():
        return "clear"
    return "repair" if provider_rejection.repair_pending() else "blocked"


def correction_context():
    import provider_rejection
    return provider_rejection.context()


def coding_session():
    """Coding scopes outlive the agent process and have their own stop/resume.

    An engine or mode recycle belongs to the cognitive service. Applying it to
    an independent coding scope used to kill a useful session mid-task.
    """
    return os.environ.get("METTACLAW_CODING_SESSION") == "1"


def gate():
    if os.environ.get("METTACLAW_CODING_CHECK") == "1":
        raise RuntimeError("provider calls are refused under METTACLAW_CODING_CHECK=1")
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


def _call_facts(plan):
    """The immutable facts a native call owner keeps; never request bytes."""
    facts = {name: getattr(plan, name) for name in (
        "epoch", "correlation", "protocol", "wire_model", "stream", "timeout",
        "cap", "connect", "first", "idle", "soft", "kind", "activity", "lane", "guard",
        "selected_model")}
    facts.update(ready=True, visible=plan.native_visible, maximum=plan.max_output,
                 purpose=getattr(plan, "purpose", "turn"), origin=plan.call_origin)
    return facts


def _visible(purpose):
    """Whether a call streams to the display. A compaction is maintenance:
    the display shows no attempt for it."""
    return purpose != "compaction" and stream_sink.configured()


def _response_cap(streams):
    """The bound on a call's received body. A streamed body carries every
    delta's event framing: a long reasoning summary alone is thousands of
    events, past a megabyte, though the answer it ends in is small. So a call
    that streams, for any protocol, has the stream bound."""
    cap = int(os.environ.get("METTACLAW_LLM_RESPONSE_BYTES", 8 * 1048576 if streams else 1048576))
    if not 1024 <= cap <= 16 * 1048576:
        raise ValueError("model response bound must be between 1 KiB and 16 MiB")
    return cap


def _host_plan(kind, purpose, selected, tokens, **fields):
    """One call's host handle: waits, scope, guard and origin. No request bytes."""
    import strategy_host
    settings = wait_settings()
    plan = SimpleNamespace(kind=kind, encoding="default", ready="preparing", native_ledger=True,
        max_output=int(tokens), selected_model=str(selected), sent=False, claimed=False,
        activity=call_scope(), lane=call_lane(),
        guard=strategy_host.guard() if strategy_host.enabled() else None,
        call_origin=strategy_host.accounting_origin(),
        correction_context=correction_context() if (kind, purpose) == ("command", "turn") else None,
        timeout=settings["hard"], connect=settings["connect"], first=settings["first"],
        idle=settings["idle"], soft=settings["soft"], **fields)
    if purpose != "turn":
        plan.purpose = purpose
    plan.native_visible = _visible(purpose)
    if call_scope() and call_lane() == "background":
        reserve = (int(os.environ.get("METTACLAW_ACTIVITY_RESERVED_OUTPUT", "0")),
                   int(os.environ.get("METTACLAW_ACTIVITY_INTERACTIVE_RESERVE", "1")))
        if any(value < 0 for value in reserve):
            raise ValueError("reserved capacity must be nonnegative")
    return plan


def _refused_seed(kind, purpose, error, provider):
    plan = SimpleNamespace(kind=kind, encoding="default", ready="false", error=error,
                           native_ledger=True, provider=provider, correlation="unsubmitted", sent=False)
    if purpose != "turn":
        plan.purpose = purpose
    return plan, '{"ready":false}'


# Each native call is one kind (command or structured) for one ledger purpose.
_NATIVE_CALLS = {("command", "turn"), ("command", "compaction"),
                 ("structured", "turn"), ("structured", "coding"), ("structured", "compaction")}


def _purpose(kind, purpose):
    kind, purpose = str(kind), str(purpose)
    if (kind, purpose) not in _NATIVE_CALLS:
        raise ValueError("unsupported native call purpose")
    if purpose != "turn" and os.environ.get("METTACLAW_CODING_CHECK") == "1":
        raise RuntimeError("provider calls are refused under METTACLAW_CODING_CHECK=1")
    return kind, purpose


def native_call_seed(selected, tokens, kind="command", purpose="turn"):
    """An API-key call: capture small host facts once; request and prompt
    bytes stay native. The credential alias is host configuration."""
    kind, purpose = _purpose(kind, purpose)
    provider = None
    try:
        selected = str(selected)
        provider = synthetic_llm._provider_for(selected)
        if not provider["key"]:
            raise RuntimeError("provider credential is not configured")
        alias = "LLM_" + provider["name"].upper()
        prefix = "CETTA_IO_AUTH_" + alias
        credential_env = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                          "kimi": "KIMI_API_KEY", "synthetic": "SYNTHETIC_API_KEY"}[provider["name"]]
        # This is native host configuration, never a guest-selected env name.
        os.environ[prefix + "_URL_PREFIX"] = provider["base"] + "/"
        os.environ[prefix + "_HEADER"] = "x-api-key" if provider["name"] == "anthropic" else "Authorization"
        os.environ[prefix + "_ENV"] = credential_env
        os.environ[prefix + "_PREFIX"] = "" if provider["name"] == "anthropic" else "Bearer "
        headers = [["http:header", "Content-Type", "application/json"], ["http:credential", alias]]
        if provider["name"] == "anthropic":
            headers.append(["http:header", "anthropic-version", "2023-06-01"])
        stream = provider["protocol"] == "chat-completions" and wait_settings()["stream"]
        # The request streams when its stream is configured or the display
        # shows it (preq:wire), whatever the protocol.
        cap = _response_cap(stream or _visible(purpose))
        plan = _host_plan(kind, purpose, selected, tokens, correlation=uuid.uuid4().hex,
            epoch=_session_epoch, protocol=provider["protocol"], provider=provider["name"],
            wire_model=str(provider.get("model", selected)), stream=stream, cap=cap)
    except Exception as error:
        return _refused_seed(kind, purpose, error, provider["name"] if provider else "unknown")
    facts = _call_facts(plan)
    command = (kind, purpose) == ("command", "turn")
    facts.update(provider=plan.provider, base=provider["base"], headers=json.dumps(headers),
                 tool=model_commentary.tool_schema() if command and model_commentary.enabled() else None,
                 ttl=os.environ.get("METTACLAW_ANTHROPIC_CACHE_TTL", "5m"))
    return plan, json.dumps(facts, ensure_ascii=False)


def subscription_seed(selected, tokens, kind, purpose, cap):
    """A ChatGPT-plan call. Its account, session, headers and request stay
    native; the host keeps the same effects handle as an API-key call."""
    kind, purpose = _purpose(kind, purpose)
    selected = str(selected)
    try:
        cap = int(cap)
        if cap <= 0:
            raise ValueError("model response bound must be positive")
        identity = uuid.uuid4().hex
        plan = _host_plan(kind, purpose, selected, tokens, correlation=identity, epoch=identity,
            protocol="responses", provider="openai", request_profile="chatgpt-plan",
            wire_model=selected[len("chatgpt:"):], stream=False, cap=cap)
    except Exception as error:
        return _refused_seed(kind, purpose, error, "openai")
    facts = _call_facts(plan)
    # Subscription quotas are independent of API-key billing and cooldowns.
    facts.update(provider="chatgpt")
    return plan, json.dumps(facts, ensure_ascii=False)


def bind_native_call(plan, seal, encoded):
    """Bind native preparation to this owner before any admission or dispatch."""
    with _claim_lock:
        if plan.ready != "preparing" or getattr(plan, "claim_started", False):
            return "false"
        return _bind_native_call(plan, seal, encoded)


def _bind_native_call(plan, seal, encoded):
    try:
        value = json.loads(str(encoded))
        fingerprint = value["fingerprint"]
        # A structured request has no prompt: no layout, size or contract marks.
        prompt = (type(value["prompt_layout"]) is dict if plan.kind == "command" else
                  value["prompt_layout"] is None and value["prompt_bytes"] == 0 and
                  value["feedback_visible"] is False and value["footer_visible"] is False)
        if (len(str(seal)) != 64 or len(fingerprint) != 64 or
                any(c not in "0123456789abcdef" for c in str(seal) + fingerprint) or
                value["model"] != plan.wire_model or
                value["encoding"] not in ("default", "messages") or
                type(value["prompt_bytes"]) is not int or value["prompt_bytes"] < 0 or
                type(value["stream"]) is not bool or not prompt or
                type(value["feedback_visible"]) is not bool or type(value["footer_visible"]) is not bool or
                (plan.protocol == "anthropic" and type(value["cache_layout"]) is not dict)):
            raise ValueError("invalid native preparation metadata")
        fields = dict(request_fingerprint=fingerprint, prepared_seal=str(seal),
            encoding=value["encoding"], prompt_layout=value["prompt_layout"],
            prompt_bytes=value["prompt_bytes"], prompt_feedback=value["feedback_visible"],
            prompt_footer=value["footer_visible"], stream=value["stream"],
            call_context=dict(call_id=plan.correlation, model=plan.wire_model,
                              provider="chatgpt" if getattr(plan, "request_profile", "") == "chatgpt-plan" else plan.provider,
                              purpose=getattr(plan, "purpose", "turn")))
        if plan.protocol == "anthropic":
            fields["cache_layout"] = value["cache_layout"]
        vars(plan).update(fields)
        plan.ready = "true"
        return "true"
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        plan.error, plan.ready = error, "false"
        return "false"


def native_preparation_failed(plan, reason):
    with _claim_lock:
        if plan.ready == "preparing":
            plan.error, plan.ready = ValueError(str(reason)), "false"
    return plan


def native_wire_started(plan):
    """Publish attempt metadata after the same claim boundary as legacy wire."""
    if plan.stream and plan.native_visible:
        plan.display_model = plan.wire_model
        stream_sink.emit(dict(kind="attempt.open", attempt=plan.correlation,
            item=plan.correlation, model=plan.display_model, provider=plan.provider,
            status="awaiting", epoch=getattr(plan, "display_epoch", "initial")))
    return True


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
    if code in ("provider-cooldown", "native-call-capacity"):
        plan.error = llm_policy.AdmissionDenied(code, float(retry))
    elif not getattr(plan, "error", None):
        plan.error = RuntimeError("native provider accounting failed")
    plan.ready = "false"
    return "false"


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


def _compaction(plan):
    return getattr(plan, "purpose", None) == "compaction"


def compaction_failure_result(plan, path):
    """Preserve maintenance error metadata without passing Error as a value."""
    failure = getattr(plan, "compaction_failure", dict(
        retryable=False, retry_after=-1, reason="local-provider-failure"))
    temporary = str(path) + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"compaction_failure": failure}, stream)
    os.replace(temporary, str(path))
    return 1


def submitted(plan, transport_id):
    plan.sent = True
    plan.transport = int(transport_id)
    plan.submitted_at = time.monotonic()
    import process_supervision
    # The native owner records the turn's start in the health record; the
    # provider wait is the last progress receipt before the answer.
    process_supervision.provider_wait(plan.timeout / 1000)
    return True


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


def decoding_wait(plan, attempt):
    """Pace a local allocation retry without resubmitting a received response."""
    attempt = max(0, int(attempt))
    if attempt == 0:
        print("[native_transport] received body retained; waiting for local decoding; call=" +
              plan.correlation, file=sys.stderr)
    try:
        import process_supervision
        process_supervision.progress("local-decode-wait", 60)
    except Exception:
        pass
    time.sleep(min(5.0, .1 * 2 ** min(attempt, 6)))
    return 1


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


def progress_observed(plan, count, quiet, soft):
    """The watchdog observer of a progress record the native owner paced."""
    import process_supervision
    process_supervision.provider_data(count)
    return 1


def _native_finish(plan):
    """Release only the decision computed by model_commentary_cetta.metta."""
    decision = getattr(plan, "native_command_classification", None)
    if not isinstance(decision, dict):
        raise model_commentary.OutputError("invalid command envelope")
    if decision["status"] == "rejected":
        message = {"incomplete-output": "unfinished command output",
                   "invalid-native-tool": "invalid native tool output",
                   "refused-or-structured-output": "refused or structured command output",
                   "invalid-envelope": "invalid command envelope"}.get(
                       decision["code"], "invalid command envelope")
        raise model_commentary.OutputError(message)
    publication = getattr(plan, "commentary", None)
    if publication and decision["publish"]:
        publication.publish(decision["commentary"])
    return decision["program"]


def finish(plan, encoded):
    """Host observers of a completion the native owner has published once.

    The ledger, its effects (completion observation, calibration, usage tally),
    turn health and the answer of a structured call or a compaction are
    native. This releases a command turn's program and feeds the display, and
    answers its decision: ("completed", program, bytes) or the failure facts
    the native owner paces (synthetic_llm.failure_facts) for a command turn,
    ("completed", bytes) for a structured call, 1 for a compaction.
    """
    result = json.loads(str(encoded))
    reason = result["choices"][0].get("finish_reason", "unknown")
    # A compaction or a structured call answers with its reply, which the
    # native owner builds; only the effects of its completion remain here.
    if _compaction(plan):
        stream_sink.final(plan, reason=reason)
        return 1
    if plan.kind == "structured":
        stream_sink.final(plan, result["choices"][0]["message"], reason)
        _answered()
        return ("completed", len(str(encoded).encode()))
    content = result["choices"][0]["message"].get("content")
    try:
        if model_commentary.enabled():
            content = _native_finish(plan)
    except model_commentary.OutputError as error:
        stream_sink.final(plan, result["choices"][0]["message"], "rejected")
        code = {"unfinished command output": "incomplete-output",
                "invalid native tool output": "invalid-native-tool",
                "refused or structured command output": "refused-or-structured-output"}.get(str(error), "invalid-envelope")
        print("[command_contract] rejected; code=" + code +
              "; feedback_visible=" + str(getattr(plan, "prompt_feedback", False)).lower() +
              "; footer_visible=" + str(getattr(plan, "prompt_footer", False)).lower(), file=sys.stderr)
        return synthetic_llm.failure_facts(error)
    if reason == "length":
        import helper
        content = helper.complete_command_prefix(content or "")
    if not isinstance(content, str) or not content.strip():
        return synthetic_llm.failure_facts(synthetic_llm.EmptyResponse())
    synthetic_llm._note_answered()
    stream_sink.final(plan, result["choices"][0]["message"], reason)
    _answered()
    return ("completed", content, len(content.encode()))


def _answered():
    """The provider's answer ends the provider wait for the watchdog."""
    import process_supervision
    process_supervision.progress("provider-result")


def duplicate_plan(plan):
    # Do not mutate the original plan's in-flight admission transaction.
    if plan.kind == "command" and not _compaction(plan):
        return synthetic_llm.failure_facts(RuntimeError("duplicate native plan"))
    raise RuntimeError("duplicate native plan")


def provider_error(plan, body):
    """The provider's own code and message for a rejected request, as Codex
    shows them. Request bytes are never logged; long bodies are cut."""
    import provider_rejection
    plan.provider_error = provider_rejection.error_fields(body)
    return 1


def transport_failure(plan, kind, code):
    """Retain an inert native observation, never its URL or error text.

    Diagnosis is best effort: malformed metadata cannot prevent the existing
    failure settlement. The native classifier supplies the category; this
    boundary rejects a category inconsistent with the native timeout code.
    """
    kind = str(kind)
    if (type(code) is not int or not 0 < code <= 65535
            or kind not in ("timeout", "transport")
            or (kind == "timeout") != (code == 28)):
        return 0
    plan.native_transport_code = code
    plan.native_transport_kind = kind
    error_type = TimeoutError if kind == "timeout" else OSError
    plan.error = error_type("native HTTP transport failure")
    return 1


class ResponseRefused(RuntimeError):
    """The provider answered; the engine refused to keep the answer."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason_code = reason


REFUSED_RESPONSES = ("response-too-large", "response-budget-exceeded",
                     "non-text-response", "credential-in-response")


def response_refused(plan, reason, bound):
    """Name a received body the engine refused, with the bound it passed."""
    reason = str(reason)
    if reason not in REFUSED_RESPONSES:
        return 0
    plan.error = ResponseRefused(reason)
    if type(bound) is int and bound > 0:
        plan.native_response_bound = bound
    return 1


def _record_failure(plan, kind, status=0):
    """Keep diagnostic categories and the provider's stated reason, without
    logging request or response bytes."""
    known = {"admission-error", "submit-error", "transport-error", "decode-error",
             "http-status", "correlation-error", "duplicate-completion",
             "invalid-phase", "retired-handle", "unfinished-provider-response",
             "refused-tool-call", "incomplete-stream", "ownership-error", *REFUSED_RESPONSES}
    code = str(kind)
    code = code if code in known else "provider-error"
    provider = getattr(plan, "provider", "unknown")
    if provider not in ("synthetic", "anthropic", "openai", "kimi"):
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
    native_code = getattr(plan, "native_transport_code", None)
    native_kind = getattr(plan, "native_transport_kind", None)
    if type(native_code) is int and 0 < native_code <= 65535 and native_kind in ("timeout", "transport"):
        detail += f"; transport_code={native_code}; transport_kind={native_kind}"
    bound = getattr(plan, "native_response_bound", None)
    if type(bound) is int and bound > 0:
        detail += f"; response_bound={bound}"
    print(f"[native_llm] failed request: {code}; provider={provider}; "
          f"status={status}; sent={sent}; elapsed_ms={elapsed}; deadline_ms={deadline}{detail}",
          file=sys.stderr)


def failed(plan, correlation, kind, status=0, retry_after=-1):
    """Report a failed call; retry_after is the owner's (-1 for none)."""
    if float(retry_after) >= 0:
        plan.retry_after = float(retry_after)
    stream_sink.final(plan, reason=str(kind))
    _record_failure(plan, kind, status)
    error = getattr(plan, "error", OSError(str(kind)) if str(kind) == "transport-error" else RuntimeError(str(kind)))
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
        import provider_rejection
        code, message = getattr(plan, "provider_error", ("", ""))
        provider_rejection.record(int(status), code, message, getattr(plan, "correction_context", None))
        return synthetic_llm.failure_facts(error)
    synthetic_llm._empty_action(error)
    if os.environ.get("METTACLAW_MODE_RUNTIME"):
        import runtime_host
        wait = cognitive_health.retry_wait_seconds()
        if wait:
            runtime_host.sleep(wait)
    if synthetic_llm._rejected_status(error.status):
        raise RuntimeError("native provider rejected the request: HTTP %d" % error.status) from None
    raise RuntimeError("native provider failed: " + type(error).__name__) from None

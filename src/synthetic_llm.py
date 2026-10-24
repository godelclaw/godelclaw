import http.client
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import cognitive_health
import llm_policy
import provider_protocol


def _open_connection(parts, timeout):
    """Create the one transport resource owned by a provider request."""
    if not parts.hostname:
        raise ValueError("provider URL has no host")
    if parts.scheme == "https":
        return http.client.HTTPSConnection(
            parts.hostname, port=parts.port, timeout=timeout)
    if parts.scheme == "http":
        return http.client.HTTPConnection(
            parts.hostname, port=parts.port, timeout=timeout)
    raise ValueError(f"unsupported provider URL scheme: {parts.scheme}")


def _request_json(request, timeout):
    """Execute one JSON request with total connection ownership.

    ``urllib.request.urlopen`` owns the connection internally until it returns
    a response.  A timeout while connecting or waiting for response headers is
    therefore outside the caller's response context manager.  Long-lived
    embedded runtimes must not depend on interpreter cleanup for that path.

    This adapter owns the connection before any blocking operation and closes
    it in ``finally`` after success, HTTP failure, transport failure, read
    failure, or decoding failure.
    """
    parts = urllib.parse.urlsplit(request.full_url)
    connection = _open_connection(parts, timeout)
    try:
        target = urllib.parse.urlunsplit((
            "", "", parts.path or "/", parts.query, ""))
        connection.request(
            request.get_method(),
            target,
            body=request.data,
            headers=dict(request.header_items()),
        )
        response = connection.getresponse()
        payload = response.read()
        if response.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status,
                response.reason,
                response.headers,
                None,
            )
        return json.loads(payload)
    finally:
        connection.close()


def _float_env(name, default, minimum):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(value, minimum)


_consecutive_empty = 0


def _note_answered():
    """A real answer clears the failure streak."""
    global _consecutive_empty
    _consecutive_empty = 0


def retry_after_seconds(headers):
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            from email.utils import parsedate_to_datetime
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, AttributeError, OverflowError):
            return None


def _rejected_status(status):
    """A client error the provider would return again for the same request.
    Timeouts, conflicts, too-early and rate limits can pass on a later try."""
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 425, 429)


def _empty_action(reason):
    """Record failure and let the independent, interruptible loop own waiting."""
    global _consecutive_empty
    status = getattr(reason, "code", getattr(reason, "status", None))
    transient = (isinstance(reason, (TimeoutError, ConnectionError, urllib.error.URLError))
                 and not isinstance(reason, urllib.error.HTTPError)) or status == 429 or (isinstance(status, int) and status >= 500)
    _consecutive_empty = _consecutive_empty + 1 if transient else 0
    base = _float_env("SYNTHETIC_EMPTY_BACKOFF", 15.0, 0.0)
    cap = _float_env("SYNTHETIC_EMPTY_BACKOFF_CAP", 300.0, 0.0)
    # A local or otherwise non-retriable failure still needs pacing.  Zero
    # delay turns one deterministic fault into an unbounded model/ledger loop.
    wait = (min(cap, base * (2 ** min(_consecutive_empty - 1, 20)))
            if transient and base else base)
    retry = getattr(reason, "retry_after", None)
    retry = retry if retry is not None else retry_after_seconds(getattr(reason, "headers", None))
    if retry is not None:
        wait = max(0, float(retry))
    import admission_frontier
    code = admission_frontier.failure_code(reason)
    if _rejected_status(status):
        # Sending the same request again cannot succeed: pause as long as the
        # longest backoff, and name the status where the operator looks.
        wait = max(wait, cap)
        code = "provider-rejected-%d" % status
    cognitive_health.turn_failed(code, retry_after=wait)
    # Exception messages may contain URLs, headers or response bodies.
    print("[synthetic_llm] failed provider turn: " + code +
          f"; retry in {wait:.0f}s", file=sys.stderr)
    return "()"


# --- provider routing --------------------------------------------------------
# Optional second provider: model names starting with "claude-" are served by
# Anthropic's native Messages API using ANTHROPIC_API_KEY. Every other
# model takes the synthetic path exactly as before. Switching is session-level
# via set_model(), so the configured default provider is untouched.

_ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com/v1"
_ANTHROPIC_DEFAULT_MODELS = (
    "claude-fable-5,claude-opus-5-5,claude-opus-5,claude-opus-4-8,"
    "claude-sonnet-5-5,claude-sonnet-5,claude-haiku-4-5-20251001"
)


def _is_anthropic_model(name):
    return str(name).strip().startswith("claude-")


# --- model persistence --------------------------------------------------------
# The active model is durable state: a switch (set_model / the /model command)
# writes it here, and process start restores it. Restarts therefore NEVER
# change the model; the config default applies only when no choice was ever
# made. This is a cost-safety property: an expensive model can only ever be
# active by explicit choice.

def _model_state_path():
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "memory", "persistent.metta")
    return os.environ.get("METTACLAW_MODEL_STATE_PATH", default)


_ACTIVE_MODEL_RE = None


import threading

_persist_lock = threading.Lock()


def _persist_model(name):
    """Durably store the choice as an (active-model ...) atom in the agent's
    standing persistent-state file, preserving any other atoms living there.
    Returns True only when the write landed; callers must not activate a
    model whose persistence failed."""
    with _persist_lock:
        return _persist_model_locked(name)


def _persist_model_locked(name):
    try:
        import re as _re
        path = _model_state_path()
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [l for l in fh.read().splitlines()
                         if not _re.match(r"\s*\(active-model\s", l)]
        except OSError:
            lines = []
        lines.append(f"(active-model {str(name).strip()})")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"[synthetic_llm] could not persist model choice: {exc}",
              file=sys.stderr)
        return False


def _restore_persisted_model():
    try:
        import re as _re
        with open(_model_state_path(), encoding="utf-8") as fh:
            m = _re.search(r"^\s*\(active-model\s+([^\s()]+)\s*\)",
                           fh.read(), _re.M)
        if m:
            os.environ["SYNTHETIC_MODEL"] = m.group(1)
    except OSError:
        pass


_restore_persisted_model()


def _anthropic_models():
    raw = os.environ.get("ANTHROPIC_MODELS", _ANTHROPIC_DEFAULT_MODELS)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _provider_for(model):
    if str(model).startswith("openai:"):
        return {"name": "openai", "protocol": "responses",
                "model": str(model).split(":", 1)[1],
                "base": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
                "key": os.environ.get("OPENAI_API_KEY", "")}
    if _is_anthropic_model(model):
        return {"name": "anthropic", "protocol": "anthropic",
                "base": os.environ.get("ANTHROPIC_BASE_URL", _ANTHROPIC_DEFAULT_BASE).rstrip("/"),
                "key": os.environ.get("ANTHROPIC_API_KEY", "")}
    return {"name": "synthetic", "protocol": "chat-completions",
            "base": os.environ.get("SYNTHETIC_BASE_URL", "https://api.synthetic.new/openai/v1").rstrip("/"),
            "key": os.environ.get("SYNTHETIC_API_KEY", "")}


# The agent's prompt layout (loop.metta and context_sources.py) places
# exactly this many boundaries, all ahead of per-turn state.  Later
# occurrences are content, such as a shell read of this file, and stay text.
_CACHE_BOUNDARY = "_cache_boundary_"
_LAYOUT_BOUNDARIES = 3


def _without_cache_boundaries(text):
    """The layout's boundaries are not content: no provider sees them."""
    text = str(text).replace(_CACHE_BOUNDARY, "", _LAYOUT_BOUNDARIES)
    return text.replace("\n_cache_chunk_\n", "\n\n")


def _anthropic_request(provider, effective_model, max_tokens, prompt, effort="medium"):
    endpoint, body = provider_protocol.command_body("anthropic", effective_model, max_tokens, effort, prompt)
    return _authenticated_request(provider, endpoint, body)


def _usage_path():
    return os.environ.get("METTACLAW_ANTHROPIC_USAGE_PATH",
                          "memory/anthropic_usage.json")


def _tally_anthropic(usage):
    """Accumulate real token counts from Anthropic responses (the only
    numbers a non-admin key can see). Read back by quota()."""
    try:
        today = time.strftime("%Y-%m-%d")
        try:
            with open(_usage_path(), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = {}
        if d.get("today") != today:
            d = {"today": today, "in": 0, "out": 0,
                 "since": d.get("since", today),
                 "total_in": d.get("total_in", 0),
                 "total_out": d.get("total_out", 0)}
        inn = int(usage.get("input_tokens", 0))             + int(usage.get("cache_creation_input_tokens", 0) or 0)             + int(usage.get("cache_read_input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0))
        d["in"] += inn; d["out"] += out
        d["total_in"] = d.get("total_in", 0) + inn
        d["total_out"] = d.get("total_out", 0) + out
        tmp = _usage_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        os.replace(tmp, _usage_path())
    except OSError:
        pass


def _fmt_tokens(n):
    n = int(n)
    return "%.1fM" % (n / 1e6) if n >= 1e6 else "%dk" % (n // 1000) if n >= 1000 else str(n)


def _diagnose_empty(payload):
    """Explain an empty answer using the provider's actual stop reason."""
    try:
        if isinstance(payload.get("content"), list):
            stop = payload.get("stop_reason")
            used = (payload.get("usage") or {}).get("output_tokens")
            if stop == "max_tokens":
                return (
                    "output budget exhausted before any answer was emitted "
                    f"(stop_reason=max_tokens, output_tokens={used}). Raise "
                    "maxOutputToken, or think in smaller steps."
                )
            return f"empty response (stop_reason={stop})"
        choice = (payload.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        msg = choice.get("message") or {}
        reasoning = msg.get("reasoning_content") or ""
        used = (payload.get("usage") or {}).get("completion_tokens")
        if finish == "length":
            return (
                "output budget exhausted before any answer was emitted "
                f"(finish_reason=length, completion_tokens={used}, "
                f"reasoning_chars={len(reasoning)}). The whole budget went to "
                "reasoning. Raise maxOutputToken, or think in smaller steps."
            )
        if reasoning:
            return (f"empty answer with {len(reasoning)} chars of reasoning "
                    f"(finish_reason={finish})")
        return f"empty response (finish_reason={finish})"
    except (AttributeError, IndexError, TypeError):
        return "empty response"


def _extract_content(payload, provider_name="synthetic"):
    """Assistant text from either response dialect (native or openai-compat)."""
    if isinstance(payload.get("content"), list):  # native Messages API
        parts = [b.get("text", "") for b in payload["content"]
                 if isinstance(b, dict) and b.get("type") == "text"]
        usage = payload.get("usage") or {}
        if usage and provider_name == "anthropic":
            _tally_anthropic(usage)
            print(
                "[synthetic_llm] anthropic usage: "
                f"in={usage.get('input_tokens')} "
                f"cache_read={usage.get('cache_read_input_tokens')} "
                f"cache_write={usage.get('cache_creation_input_tokens')} "
                f"out={usage.get('output_tokens')}",
                file=sys.stderr,
            )
        return "".join(parts)
    return payload["choices"][0]["message"]["content"]


class OutputTruncated(RuntimeError):
    pass


class EmptyResponse(RuntimeError):
    pass


def _authenticated_request(provider, endpoint, body):
    if provider["name"] == "synthetic":
        body = provider_protocol.synthetic_body(body)
    headers = {"Content-Type": "application/json"}
    if provider["name"] == "anthropic":
        headers.update({"x-api-key": provider["key"], "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = "Bearer " + provider["key"]
    return urllib.request.Request(provider["base"] + endpoint,
                                  json.dumps(body).encode("utf-8"), headers)


def _completion_metadata(result, prompt_layout=None, *, call_context=None):
    """No messages, tool arguments, signatures, credentials or raw errors."""
    path = os.environ.get("METTACLAW_LLM_COMPLETION_PATH")
    if not path:
        health = os.environ.get("METTACLAW_COGNITIVE_HEALTH_PATH")
        path = os.path.join(os.path.dirname(health), "llm-completion.json") if health else None
    if path:
        value = {"finish_reason": result["choices"][0].get("finish_reason"),
                 "provider_stop": result.get("provider_stop"), "usage": result.get("usage", {}),
                 "transport": result.get("transport", "python"), "observed_at": time.time()}
        value["cache"] = provider_protocol.cache_observation(value["usage"])
        if prompt_layout is not None:
            value["prompt_layout"] = prompt_layout
        # This is an observation, not admission authority.
        try:
            parent = os.path.dirname(path) or "."
            os.makedirs(parent, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=parent, prefix=".llm-completion-")
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream)
            os.replace(temporary, path)
        except OSError:
            pass
        # One line per call, so prefix reuse can be followed across turns.
        try:
            usage = value["usage"] if isinstance(value["usage"], dict) else {}
            line = dict(observed_at=value["observed_at"], transport=value["transport"],
                        provider_stop=value["provider_stop"],
                        output_tokens=usage.get("output_tokens", usage.get("completion_tokens")),
                        **value["cache"])
            # This JSONL is a compatibility view; the call history in the
            # provider ledger owns idempotence and durable usage evidence.
            for key, count in llm_policy.usage_evidence(usage)["counts"].items():
                if count is not None:
                    line[key] = count
            if isinstance(call_context, dict):
                line.update({key: call_context[key] for key in
                             ("call_id", "provider", "model", "purpose") if key in call_context})
            fd = os.open(os.path.join(os.path.dirname(path) or ".", "llm-usage.jsonl"),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as stream:
                stream.write(json.dumps(line) + "\n")
        except (OSError, TypeError, ValueError):
            pass


def _exchange(provider, endpoint, body, max_tokens, *, purpose="unspecified"):
    ticket = llm_policy.admit(provider["name"], max_tokens, request_fingerprint=llm_policy.fingerprint(body))
    context = dict(call_id=ticket, provider=provider["name"],
                   model=str(body.get("model", "")), purpose=purpose)
    llm_policy.describe(ticket, model=context["model"], protocol=provider["protocol"],
                        purpose=purpose, transport="python")
    cognitive_health.turn_started(provider["name"])
    sent = False
    try:
        import process_supervision
        process_supervision.provider_wait(_float_env("SYNTHETIC_TIMEOUT", 120.0, 1.0))
        request = _authenticated_request(provider, endpoint, body)
        llm_policy.submitted(ticket)
        sent = True
        payload = _request_json(request,
                                _float_env("SYNTHETIC_TIMEOUT", 120.0, 1.0))
        result = provider_protocol.normalize_response(provider["protocol"], payload)
        llm_policy.complete(ticket, result)
        _completion_metadata(result, call_context=context)
        if provider["name"] == "anthropic":
            _tally_anthropic(payload.get("usage", {}))
        return result
    except BaseException as error:
        status = getattr(error, "code", None)
        retry = retry_after_seconds(getattr(error, "headers", None))
        llm_policy.fail(ticket, status=status, retry_after=retry, sent=sent)
        raise


def structured_chat(request):
    selected = current_model()
    provider = _provider_for(selected)
    if not provider["key"]:
        raise RuntimeError("provider credential is not configured")
    endpoint, payload = provider_protocol.structured_body(
        provider["protocol"], provider.get("model", selected), request)
    return _exchange(provider, endpoint, payload, request["max_tokens"],
                     purpose="coding" if os.environ.get("METTACLAW_CODING_SESSION") == "1" else "structured")


def chat(model, max_tokens, effort, prompt):
    effective_model = current_model(str(model))
    provider = _provider_for(effective_model)
    try:
        if not provider["key"]:
            raise RuntimeError("provider credential is not configured")
        endpoint, body = provider_protocol.command_body(
            provider["protocol"], provider.get("model", effective_model), max_tokens, effort, prompt)
        import model_commentary
        from types import SimpleNamespace
        commentary_plan = SimpleNamespace(kind="command")
        model_commentary.begin(commentary_plan)
        result = _exchange(provider, endpoint, body, max_tokens, purpose="turn")
        content = result["choices"][0]["message"].get("content")
        finish = result["choices"][0].get("finish_reason")
        content = model_commentary.finish(commentary_plan, content, finish, result["choices"][0]["message"])
        if finish == "length":
            import helper
            content = helper.complete_command_prefix(content or "")
        import context_budget
        context_budget.observe(prompt, result.get("usage", {}))
        if isinstance(content, str) and content.strip():
            _note_answered()
            cognitive_health.turn_completed(len(content.encode("utf-8")))
            return content
        return _empty_action(EmptyResponse())
    except (urllib.error.HTTPError, TimeoutError, socket.timeout, urllib.error.URLError,
            ConnectionError, OSError, http.client.HTTPException, ValueError, KeyError,
            IndexError, TypeError, RuntimeError) as exc:
        return _empty_action(exc)


# --- API introspection: quota, models, model switching ------------------------
# These never raise across the Janus boundary (same discipline as chat()).

def _api_root():
    base = os.environ.get("SYNTHETIC_BASE_URL", "https://api.synthetic.new/openai/v1")
    return base.split("/openai/")[0].rstrip("/")


def _openai_base():
    return os.environ.get("SYNTHETIC_BASE_URL", "https://api.synthetic.new/openai/v1").rstrip("/")


# Provider introspection runs only in background refresh workers.  Operator
# controls read the last durable observation and therefore never inherit this
# timeout.
_INTROSPECT_TIMEOUT = float(os.environ.get("SYNTHETIC_INTROSPECT_TIMEOUT", "8"))

_OBSERVATION_SCHEMA = 1
_OBSERVATION_TTLS = {"models": 300.0, "quota": 60.0}
_OBSERVATION_RETRY_SECONDS = 30.0
_observation_lock = threading.RLock()
_OBSERVATION_TTLS["model-window"] = _OBSERVATION_TTLS["models"]
_refreshing_observations = set()

_SYNTHETIC_PINNED_MODELS = (
    "syn:large:text",
    "hf:zai-org/GLM-5.3-Flash",
    "hf:zai-org/GLM-5.2",
    "hf:moonshotai/Kimi-K3",
)


def _observation_path():
    configured = os.environ.get("METTACLAW_PROVIDER_OBSERVATION_PATH", "")
    if configured:
        return configured
    state_home = os.environ.get(
        "XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    instance = os.environ.get("METTACLAW_INSTANCE", "default")
    return os.path.join(state_home, "pettaclaw", instance,
                        "provider-observations.json")


def _read_observations():
    try:
        with open(_observation_path(), encoding="utf-8") as stream:
            value = json.load(stream)
        if (isinstance(value, dict)
                and value.get("schema") == _OBSERVATION_SCHEMA):
            return value
    except (OSError, TypeError, ValueError):
        pass
    return {"schema": _OBSERVATION_SCHEMA}


def _write_observations(value):
    path = _observation_path()
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".provider-observations-", dir=parent, text=True)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return True
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


def _cached_observation(kind):
    with _observation_lock:
        record = _read_observations().get(str(kind))
    return record if isinstance(record, dict) else {}


def _store_observation(kind, value, now=None):
    observed_at = time.time() if now is None else float(now)
    record = {
        "observed_at": observed_at,
        "attempted_at": observed_at,
        "value": value,
    }
    with _observation_lock:
        observations = _read_observations()
        observations[str(kind)] = record
        return _write_observations(observations)


def _observation_stale(kind, record, now=None):
    now = time.time() if now is None else float(now)
    observed = float(record.get("observed_at", 0) or 0)
    return not observed or now - observed >= _OBSERVATION_TTLS[kind]


def _note_observation_failure(kind, error, now=None):
    attempted_at = time.time() if now is None else float(now)
    with _observation_lock:
        observations = _read_observations()
        record = observations.get(str(kind))
        if not isinstance(record, dict):
            record = {}
        record["attempted_at"] = attempted_at
        record["last_error"] = str(error)[:160]
        observations[str(kind)] = record
        return _write_observations(observations)


def _refresh_allowed(record, now=None):
    now = time.time() if now is None else float(now)
    attempted = float(record.get("attempted_at", 0) or 0)
    return not attempted or now - attempted >= _OBSERVATION_RETRY_SECONDS


def _sanitize_models(payload):
    result = []
    seen = set()
    for model in payload.get("data") or []:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        context = model.get("context_length") or model.get("context_window")
        result.append({"id": model_id, "context": context})
    return result


def _sanitize_quota(payload):
    """Retain only the numbers rendered by the local operator control."""
    result = {}
    plan = payload.get("planWeeklyLimit")
    if isinstance(plan, dict):
        result["planWeeklyLimit"] = {
            key: plan.get(key) for key in ("percentRemaining", "nextRegenAt", "nextRegenPercent")
        }
    week = payload.get("weeklyTokenLimit")
    if isinstance(week, dict):
        result["weeklyTokenLimit"] = {
            key: week.get(key)
            for key in ("remainingCredits", "maxCredits", "percentRemaining")
        }
    five = payload.get("rollingFiveHourLimit")
    if isinstance(five, dict):
        result["rollingFiveHourLimit"] = {
            key: five.get(key)
            for key in ("remaining", "max", "limited")
        }
    return result


def _refresh_observation(kind):
    try:
        if kind == "models":
            payload = _get_json(_openai_base() + "/models")
            value = _sanitize_models(payload) if "error" not in payload else None
        elif kind == "model-window":
            model = current_model()
            provider = _provider_for(model)
            if provider["protocol"] != "anthropic" or not provider["key"]:
                raise ValueError("selected provider has no model-window discovery")
            request = urllib.request.Request(provider["base"] + "/models/" + urllib.parse.quote(model, safe=""),
                headers={"x-api-key":provider["key"], "anthropic-version":"2023-06-01"})
            payload = _request_json(request, _INTROSPECT_TIMEOUT)
            limit = payload.get("max_input_tokens")
            value = {"id":model,"context":limit,"source":"provider-model-metadata"} if type(limit) is int and limit > 0 else None
        elif kind == "quota":
            payload = _get_json(_api_root() + "/v2/quotas")
            value = _sanitize_quota(payload) if "error" not in payload else None
        else:
            raise ValueError(f"unknown observation kind: {kind}")
        if value is None:
            _note_observation_failure(
                kind, payload.get("error", "invalid response"))
        else:
            _store_observation(kind, value)
    except Exception as exc:
        _note_observation_failure(kind, type(exc).__name__)
    finally:
        with _observation_lock:
            _refreshing_observations.discard(kind)


def model_window_due():
    """The selected model, when the native runtime should read its window.

    The ChatGPT model list reports each model's window, but only the native
    runtime holds that sign-in; the host reads every other provider's window.
    The read sits on the caller's path, so a failure waits a full interval."""
    model = str(current_model())
    if not model.startswith("chatgpt:"):
        return ""
    with _observation_lock:
        record = _read_observations().get("model-window")
    record = record if isinstance(record, dict) else {}
    value = record.get("value") if isinstance(record.get("value"), dict) else {}
    if value.get("id") == model and not _observation_stale("model-window", record):
        return ""
    if (record.get("attempted_model") == model and time.time()
            - float(record.get("attempted_at", 0) or 0) < _OBSERVATION_TTLS["model-window"]):
        return ""
    return model


def observe_model_window(model, context, source):
    """Record the window the native runtime read for a model; 0 when none."""
    if not str(model).startswith("chatgpt:"):
        return 0
    context = int(context) if type(context) in (int, float) else 0
    if context > 0:
        _store_observation("model-window", {"id": str(model), "context": context,
                                            "source": str(source)})
        return 1
    with _observation_lock:
        observations = _read_observations()
        record = observations.get("model-window")
        record = record if isinstance(record, dict) else {}
        record.update(attempted_at=time.time(), attempted_model=str(model),
                      last_error=("no window: " + str(source))[:160])
        observations["model-window"] = record
        _write_observations(observations)
    return 0


def refresh_provider_observations_async(kinds=("models", "quota"), force=False):
    """Refresh provider facts without putting the provider on a caller's path.

    At most one worker per kind exists. Failure preserves the last good value
    and is rate-limited, so repeated controls cannot stampede the provider.
    """
    started = []
    for kind in kinds:
        if kind not in _OBSERVATION_TTLS:
            continue
        if kind == "model-window" and str(current_model()).startswith("chatgpt:"):
            continue  # read by the native runtime: see model_window_due
        with _observation_lock:
            record = _read_observations().get(kind)
            record = record if isinstance(record, dict) else {}
            should_start = (
                kind not in _refreshing_observations
                and (force or (_observation_stale(kind, record)
                               and _refresh_allowed(record)))
            )
            if should_start:
                _refreshing_observations.add(kind)
        if should_start:
            threading.Thread(
                target=_refresh_observation,
                args=(kind,),
                name="provider-observation-" + kind,
                daemon=True,
            ).start()
            started.append(kind)
    return tuple(started)


def _get_json(url):
    key = os.environ.get("SYNTHETIC_API_KEY", "")
    if not key:
        return {"error": "SYNTHETIC_API_KEY is not set"}
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        data = _request_json(req, _INTROSPECT_TIMEOUT)
        return data if isinstance(data, dict) else {"error": "unexpected response"}
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}"}
    except (
        TimeoutError,
        socket.timeout,
        urllib.error.URLError,
        ConnectionError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        return {"error": type(exc).__name__}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"bad response: {type(exc).__name__}"}


def current_model(default="syn:large:text"):
    # The control responder and cognition deliberately have different PIDs.
    # Their process environments are not a shared settings store.
    _restore_persisted_model()
    return os.environ.get("SYNTHETIC_MODEL", default)


def _percent(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return "?"


def quota():
    """Render the durable quota observation; refresh only in the background."""
    selected = current_model()
    on_anthropic = _is_anthropic_model(selected)
    provider_tag = ("Anthropic API" if on_anthropic else
                    "ChatGPT subscription" if selected.startswith("chatgpt:") else
                    "OpenAI API" if selected.startswith("openai:") else "Synthetic")
    parts = [f"model={selected} ({provider_tag})"]
    if on_anthropic:
        try:
            with open(_usage_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("in") or d.get("out"):
                parts.append("today=%s in / %s out"
                             % (_fmt_tokens(d.get("in", 0)),
                                _fmt_tokens(d.get("out", 0))))
        except (OSError, ValueError):
            pass  # no numbers -> say nothing about it
    record = _cached_observation("quota")
    data = record.get("value") if isinstance(record.get("value"), dict) else {}
    refresh_provider_observations_async(("quota",))
    if data:
        plan = data.get("planWeeklyLimit") or {}
        if plan:
            parts.append(f"synthetic_weekly={_percent(plan.get('percentRemaining'))}% remaining")
        week = data.get("weeklyTokenLimit") or {}
        if week and not plan:
            parts.append(f"synthetic_weekly={week.get('remainingCredits', '?')} "
                         f"of {week.get('maxCredits', '?')} "
                         f"({_percent(week.get('percentRemaining'))}%)")
        five = data.get("rollingFiveHourLimit") or {}
        if five:
            parts.append(f"5h={five.get('remaining', '?')}/{five.get('max', '?')}"
                         + (" LIMITED" if five.get("limited") else ""))
    elif len(parts) == 1:
        parts.append("quota refreshing; no cached observation")
    return " | ".join(parts)


def models():
    """Last-known models as a MeTTa-friendly list; never provider-blocking."""
    record = _cached_observation("models")
    data = record.get("value") if isinstance(record.get("value"), list) else []
    refresh_provider_observations_async(("models",))
    items = []
    for model in data:
        if not isinstance(model, dict):
            continue
        context = model.get("context") or "?"
        items.append(f"({model.get('id', '?')} ctx {context})")
    if os.environ.get("ANTHROPIC_API_KEY", ""):
        items.extend(f"({m} provider anthropic)" for m in _anthropic_models())
    return "(" + " ".join(items) + ")" if items else "(catalog refreshing)"


def _pinned_synthetic_models():
    raw = os.environ.get("SYNTHETIC_PINNED_MODELS", "")
    configured = [part.strip() for part in raw.split(",") if part.strip()]
    return configured or list(_SYNTHETIC_PINNED_MODELS)


# Subscription choices observed with chatgpt-models on 2026-10-15.
# These are pinned choices, not a claim of current account availability.
_CHATGPT_MENU_MODELS = (
    "chatgpt:gpt-5.6-sol", "chatgpt:gpt-6-sol",
    "chatgpt:gpt-6.1-sol", "chatgpt:gpt-6-astra",
)


def _chatgpt_menu_enabled():
    if os.environ.get("METTACLAW_ACTIVE_ENGINE") != "cetta":
        return False
    if os.environ.get("METTACLAW_LLM_TRANSPORT") != "native":
        return False
    path = os.environ.get("METTACLAW_CHATGPT_POLICY_PATH")
    if not path:
        engine = os.environ.get("METTACLAW_ENGINE_STATE_PATH",
            os.path.expanduser("~/.local/state/godelclaw/engine"))
        path = os.path.join(os.path.dirname(engine), "chatgpt.json")
    try:
        with open(path, encoding="utf-8") as stream:
            policy = json.load(stream)
        return isinstance(policy, dict) and bool(policy.get("account"))
    except (OSError, ValueError, TypeError):
        return False


def model_ids():
    """Switchable ids from local durable state, never a provider round trip."""
    record = _cached_observation("models")
    rows = record.get("value") if isinstance(record.get("value"), list) else []
    ids = [str(row.get("id")) for row in rows
           if isinstance(row, dict) and row.get("id")]
    for model_id in _pinned_synthetic_models():
        if model_id not in ids:
            ids.append(model_id)
    if _chatgpt_menu_enabled():
        for model_id in _CHATGPT_MENU_MODELS:
            if model_id not in ids:
                ids.append(model_id)
    selected = current_model()
    if not _is_anthropic_model(selected) and selected not in ids:
        ids.append(selected)
    if os.environ.get("ANTHROPIC_API_KEY", ""):
        for model_id in _anthropic_models():
            if model_id not in ids:
                ids.append(model_id)
    refresh_provider_observations_async(("models",))
    return ids


def model_catalog_status():
    """Short, local-only provenance note for the Telegram model menu."""
    if _chatgpt_menu_enabled():
        return "Synthetic catalog + pinned ChatGPT subscription choices (availability unverified)"
    record = _cached_observation("models")
    observed = float(record.get("observed_at", 0) or 0)
    if not observed:
        refresh_provider_observations_async(("models",))
        return "provider catalog refreshing; pinned choices shown"
    age = max(0, int(time.time() - observed))
    suffix = "; refresh running" if _observation_stale("models", record) else ""
    return f"provider catalog observed {age}s ago{suffix}"


def model_in_observed_catalog(name):
    if str(name).startswith("chatgpt:"):
        # Synthetic observations cannot establish subscription availability.
        return None
    if _is_anthropic_model(name):
        return (str(name) in _anthropic_models()
                if os.environ.get("ANTHROPIC_API_KEY", "") else False)
    record = _cached_observation("models")
    rows = record.get("value") if isinstance(record.get("value"), list) else []
    ids = {str(row.get("id")) for row in rows
           if isinstance(row, dict) and row.get("id")}
    return None if not ids else str(name) in ids


def session_model_error(name):
    """Why a model id cannot serve a coding session, or "" when it can.
    The checks set_model applies, without persisting or switching anything."""
    name = str(name).strip()
    if _is_anthropic_model(name):
        if not os.environ.get("ANTHROPIC_API_KEY", ""):
            return "anthropic provider not configured (ANTHROPIC_API_KEY missing)"
        if name not in _anthropic_models():
            return f"unknown anthropic model '{name}'; available: " + ", ".join(_anthropic_models())
        return ""
    import re as _re
    if (not name or len(name) > 200
            or not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]*", name)):
        return f"invalid model id '{name}'"
    return ""


def set_model(name):
    """Switch the model for subsequent chat() calls.

    Validated first, then durably persisted, then activated — a switch that
    cannot be persisted is not applied. The choice survives restarts.
    """
    name = str(name).strip()
    if _is_anthropic_model(name):
        if not os.environ.get("ANTHROPIC_API_KEY", ""):
            return ("anthropic provider not configured "
                    "(ANTHROPIC_API_KEY missing); model unchanged")
        if name not in _anthropic_models():
            return (f"unknown anthropic model '{name}'; available: "
                    + ", ".join(_anthropic_models()))
        if not _persist_model(name):
            return (f"switch to '{name}' NOT applied: could not durably "
                    "persist the choice")
        os.environ["SYNTHETIC_MODEL"] = name
        return f"model set to '{name}' (anthropic); persists across restarts"
    import re as _re
    if (not name or len(name) > 200
            or not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]*", name)):
        return f"invalid model id '{name}'; model unchanged"
    if not _persist_model(name):
        return (f"switch to '{name}' NOT applied: could not durably persist "
                "the choice")
    os.environ["SYNTHETIC_MODEL"] = name
    refresh_provider_observations_async(("models",))
    availability = model_in_observed_catalog(name)
    warning = (
        "; warning: absent from the latest provider catalog, so the provider "
        "may reject it"
        if availability is False else ""
    )
    return f"model set to '{name}'; persists across restarts{warning}"

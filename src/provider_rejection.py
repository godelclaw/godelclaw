"""Retain invalid-request blockage until observed corrective state changes.

The existing cognitive receipt owns the blockage. Time, prompt status text and
retry counts are not corrections. Provider authentication and transient errors
keep their own recovery/backoff; this gate covers deterministic request errors.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

import cognitive_health


INVALID_REQUEST = frozenset((400, 404, 405, 406, 410, 413, 415, 422))
_unwritten = {}


def _health():
    value = cognitive_health._read()
    path = cognitive_health._path()
    if path in _unwritten:
        value.pop('provider_rejection', None)
        if _unwritten[path] is not None:
            value['provider_rejection'] = _unwritten[path]
    return value


def _save(value):
    path = cognitive_health._path()
    try:
        cognitive_health._write(value)
        _unwritten.pop(path, None)
    except OSError:
        # A full local disk must not turn a known remote rejection into another
        # provider request. Keep the gate in this process and retry persistence.
        _unwritten[path] = value.get('provider_rejection')


def _json(path):
    try:
        value = Path(path).read_text()
        return json.loads(value)
    except (OSError, ValueError):
        return None


def context():
    """Nonsecret corrective facts, without prompts or volatile timestamps."""
    root = Path(__file__).resolve().parents[1]
    state = Path(cognitive_health._path()).parent
    import synthetic_llm
    facts = {"model": synthetic_llm.current_model(), "operator_input": [0, None, None], "mode_input": ""}
    release = _json(state / "runtime-release.json")
    if isinstance(release, dict):
        facts["release"] = release.get("release")
    files = [root / "config/local.toml", root / "config/default.toml", state / 'context-policy.json']
    for key in ("METTACLAW_MODEL_STATE_PATH", "METTACLAW_CHATGPT_POLICY_PATH", "METTACLAW_LLM_POLICY_PATH"):
        if os.environ.get(key):
            files.append(Path(os.environ[key]))
    stamps = []
    for path in files:
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            stamps.append((str(path), None))
        except OSError:
            stamps = None
            break  # Missing configuration observation does not hide input facts.
    if stamps is not None:
        facts["configuration"] = hashlib.sha256(json.dumps(stamps).encode()).hexdigest()
    mode_state = Path(os.environ.get("METTACLAW_MODE_STATE_ROOT", state / "modes"))
    control = _json(mode_state / "control-status.json")
    inbox = control.get("operator_inbox") if isinstance(control, dict) else None
    if isinstance(inbox, dict) and type(inbox.get("pending")) is int and inbox['pending'] > 0:
        facts["operator_input"] = [inbox.get(k) for k in
                                   ("pending", "newestMessageAt", "oldestMessageAt")]
    mode = os.environ.get("METTACLAW_MODE_RUNTIME")
    if mode:
        offered = _json(Path(mode) / "offered-input.json")
        if offered:
            facts["mode_input"] = hashlib.sha256(json.dumps(offered, sort_keys=True).encode()).hexdigest()
    checkpoint = os.environ.get('METTACLAW_STRATEGY_PATH')
    if checkpoint:
        try:
            with sqlite3.connect(Path(checkpoint).resolve().as_uri() + '?mode=ro', uri=True, timeout=.1) as db:
                rows = db.execute("SELECT key,value FROM meta WHERE key GLOB 'task_window:*' ORDER BY key").fetchall()
            facts['checkpoint'] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        except (OSError, sqlite3.Error):
            pass
    return facts


def blocked():
    """An unreadable corrective observation never grants a retry."""
    with cognitive_health._lock:
        health = _health()
        block = health.get("provider_rejection")
        if not isinstance(block, dict):
            return False
        before, now = block.get("context", {}), context()
        before = {'operator_input': [0, None, None], 'mode_input': '', **before}
        if any(key in now and now[key] != value for key, value in before.items()):
            health.pop("provider_rejection", None)
            health["retry_at"] = 0
            _save(health)
            return False
        # Backfill newly available observations without pretending they changed.
        if any(key not in before for key in now):
            block["context"] = {**now, **before}
            _save(health)
        elif cognitive_health._path() in _unwritten:
            _save(health)
        return True


def safe_text(value, limit=400):
    text = ''.join(c if c.isprintable() else ' ' for c in str(value or ""))
    for key, secret in os.environ.items():
        if re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD)$", key) and len(secret) >= 6:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", text)
    return " ".join(text.split())[:limit]


def error_fields(body):
    try:
        value = json.loads(str(body))
    except (ValueError, TypeError):
        return "", "Non-JSON provider rejection"
    error = value.get("error", value) if isinstance(value, dict) else {}
    if not isinstance(error, dict):
        return "", "Provider rejected the request"
    return safe_text(error.get("code") or error.get("type"), 64), safe_text(
        error.get("message") or value.get("detail") or "Provider rejected the request")


def record(status, code="", message="", captured=None):
    if status not in INVALID_REQUEST:
        return False
    snapshot = context() if captured is None else captured
    identity = hashlib.sha256(json.dumps([status, code, snapshot], sort_keys=True).encode()).hexdigest()
    with cognitive_health._lock:
        health = _health()
        prior = health.get("provider_rejection", {})
        if prior.get("id") != identity:
            health["provider_rejection"] = dict(id=identity, status=status, code=safe_text(code, 64),
                                               message=safe_text(message), context=snapshot)
            external = {k: v for k, v in snapshot.items() if k != 'checkpoint'}
            size_error = status == 413 or code in ('context_length_exceeded', 'string_above_max_length')
            if (size_error and os.environ.get('METTACLAW_ACTIVE_ENGINE') == 'cetta'
                    and os.environ.get('METTACLAW_OBSERVED_STRATEGY') == '1'
                    and health.get('provider_context_repair') != external):
                health['provider_rejection']['repair'] = 'requested'
        health["retry_at"] = 0
        _save(health)
    notify()
    return True


def repair_pending():
    with cognitive_health._lock:
        return _health().get('provider_rejection', {}).get('repair') == 'requested'


def begin_context_repair():
    """Claim one real compaction, never permission to resend unchanged input."""
    with cognitive_health._lock:
        health = _health()
        block = health.get('provider_rejection', {})
        if block.get('repair') != 'requested':
            return 0
        block['repair'] = 'attempted'
        health['provider_context_repair'] = {k: v for k, v in block['context'].items() if k != 'checkpoint'}
        _save(health)
        return 0 if cognitive_health._path() in _unwritten else 1


def notify():
    """One durable operator notice, submitted by the independent control plane."""
    with cognitive_health._lock:
        health = _health()
        block = health.get("provider_rejection")
        if not isinstance(block, dict) or block.get("alert_queued") or block.get('repair') == 'requested':
            return 0
        try:
            import mode_channel
            import durable_telegram
            route = mode_channel.resolve("telegram")
            folder = mode_channel.state() / "channel-outbox"
            folder.mkdir(parents=True, exist_ok=True)
            if not block.get("alert_key"):
                block["alert_key"] = durable_telegram.Channel("", str(folder / "sequence")).key(
                    route["chat"], route["thread"])
                _save(health)
            if cognitive_health._path() in _unwritten:
                return 0  # Reserve durably before publication across restarts.
            text = ("Provider request blocked: HTTP %s (%s). %s\n"
                    "The request and input are retained. A corrected configuration, deployment, "
                    "model selection or new operator input can resume it. Controls remain available." %
                    (block["status"], block.get("code") or "invalid request", block.get("message", "")))
            mode_channel.store(folder / (block["alert_key"] + ".json"), {
                "key": block["alert_key"], "commands": [["send", text, "plain"]]})
            block["alert_queued"] = True
            _save(health)
            return 1
        except (OSError, ValueError, KeyError, TypeError):
            return 0  # The retained block keeps its reserved notice identity.

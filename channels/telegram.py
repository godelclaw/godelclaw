import os
import fcntl
import hashlib
import json
import math
import subprocess
import tempfile
import re
import threading
import time
import uuid

import requests

import stimulus_frontier

# Grandfathered producers only signal the native wait. Payload routing and
# identity handling remain at their existing boundary.
try:
    from cetta_bridge import notice as _host_notice
except ImportError:
    _host_notice = None

_token = ""
_allowed_chat_ids = set()
_last_chat_id = ""
_primary_chat_id = ""
_offset_path = ""
# "durable" makes a CeTTa channel service the bot's only poller and sender,
# for the MeTTa client; "bot-api" leaves the client disabled.
_transport = "bot-api"
_durable = None
_durable_receipts = None
_durable_outbox = None
_durable_pending = {}
_durable_lock = threading.Lock()
_log_path = ""
_msg_lock = threading.RLock()
_log_lock = threading.Lock()
_energy_lock = threading.RLock()
_chat_titles = {}  # chat_id -> last-seen display title (for outbound records)
# Rest state, as the MeTTa client publishes it.
_health_lock = threading.RLock()
_effect_lock = threading.RLock()
_effect_turn = None
_effect_activity_epoch = None
_effect_sends = set()

_CONTROL_COMMANDS = (
    "/start", "/stop",
    "/model", "/models", "/mode", "/modes",
    "/fuel", "/fuels",
    "/quota", "/wake", "/energy", "/health", "/activity", "/delete",
    "/claude_code_authorization",
)

_MENU_COMMANDS = (
    ("start", "Enable cognition with the operator latch"),
    ("stop", "Stop cognition with the operator latch"),
    ("fuel", "Show or switch what a renewal does to the budget"),
    ("fuels", "List fuel disciplines"),
    ("mode", "Show or switch the cognitive loop mode"),
    ("modes", "List cognitive loop modes"),
    ("model", "Show or switch the language model"),
    ("models", "List language models"),
    ("energy", "Show per-sender arming energy"),
    ("health", "Show runtime, channel, and memory health"),
    ("activity", "Show whether cognition is working, resting, or idle"),
    ("quota", "Show model budget"),
    ("wake", "End the current rest"),
)

# Every authenticated operator control has its own execution lane.  State
# modules serialize their own short local mutations; external delivery of one
# response must never hold up receipt or execution of another control.


def _health_path():
    configured = os.environ.get("METTACLAW_TELEGRAM_HEALTH_PATH", "")
    if configured:
        return configured
    state_home = os.environ.get(
        "XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    instance = os.environ.get("METTACLAW_INSTANCE", "default")
    return os.path.join(state_home, "pettaclaw", instance,
                        "telegram-health.json")


def _health_update(**fields):
    """Merge non-secret liveness facts into the health file an external
    monitor reads. The MeTTa client merges its own facts into the same file,
    so each writer adds only the facts it was given, under the file's lock."""
    path = _health_path()
    with _health_lock:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            lock = os.open(path + ".lock", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    with open(path, encoding="utf-8") as stream:
                        state = json.load(stream)
                except (OSError, ValueError):
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                state.update(fields)
                state["observed_at"] = time.time()
                fd, tmp = tempfile.mkstemp(prefix=".telegram-health-",
                                           dir=os.path.dirname(path) or ".", text=True)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        json.dump(state, fh, sort_keys=True)
                        fh.write("\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            finally:
                os.close(lock)
        except OSError as exc:
            print("[telegram] health write failed:", type(exc).__name__)


def _api_base():
    return os.environ.get("METTACLAW_TELEGRAM_BASE_URL",
                          "https://api.telegram.org")


def _api(method, token=""):
    return f"{_api_base()}/bot{token or _token}/{method}"


def _split_chat_ids(raw):
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _compact(value, limit=160):
    value = "" if value is None else str(value)
    value = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _field(key, value, limit=160):
    value = _compact(value, limit=limit)
    if not value:
        return None
    return f"{key}={json.dumps(value, ensure_ascii=False)}"


def _bool_field(key, value):
    return f"{key}={'true' if value else 'false'}"


def _display_user(user):
    if not user:
        return ""
    if user.get("username"):
        return "@" + str(user["username"])
    return user.get("first_name") or str(user.get("id", ""))


def _display_chat(chat):
    if not chat:
        return ""
    if chat.get("title"):
        return chat["title"]
    if chat.get("username"):
        return "@" + str(chat["username"])
    return chat.get("first_name") or str(chat.get("id", ""))


def _message_excerpt(message, limit=180):
    if not message:
        return ""
    return _compact(message.get("text") or message.get("caption") or "", limit=limit)


def _forward_fields(message):
    origin = message.get("forward_origin") or {}
    if origin:
        origin_type = origin.get("type", "")
        fields = [_bool_field("forwarded", True), _field("forward_type", origin_type)]
        if origin_type == "user":
            user = origin.get("sender_user") or {}
            fields.extend([
                _field("forward_from", _display_user(user)),
                _field("forward_from_id", user.get("id")),
            ])
        elif origin_type == "hidden_user":
            fields.append(_field("forward_from", origin.get("sender_user_name")))
        elif origin_type in {"chat", "channel"}:
            chat = origin.get("sender_chat") or origin.get("chat") or {}
            fields.extend([
                _field("forward_from_chat", _display_chat(chat)),
                _field("forward_from_chat_id", chat.get("id")),
            ])
        fields.extend([
            _field("forward_message_id", origin.get("message_id")),
            _field("forward_signature", origin.get("author_signature")),
        ])
        return [f for f in fields if f]

    # Older Telegram payloads used these fields before forward_origin.
    legacy_user = message.get("forward_from") or {}
    legacy_chat = message.get("forward_from_chat") or {}
    legacy_name = message.get("forward_sender_name")
    if legacy_user or legacy_chat or legacy_name:
        return [
            _bool_field("forwarded", True),
            _field("forward_from", _display_user(legacy_user) or legacy_name),
            _field("forward_from_id", legacy_user.get("id")),
            _field("forward_from_chat", _display_chat(legacy_chat)),
            _field("forward_from_chat_id", legacy_chat.get("id")),
            _field("forward_signature", message.get("author_signature")),
        ]
    return [_bool_field("forwarded", False)]


def _reply_fields(message):
    reply = message.get("reply_to_message") or {}
    external = message.get("external_reply") or {}
    quote = message.get("quote") or {}
    fields = []
    if reply:
        sender = reply.get("from") or {}
        chat = reply.get("chat") or {}
        fields.extend([
            _bool_field("is_reply", True),
            _field("reply_to_message_id", reply.get("message_id")),
            _field("reply_to_from", _display_user(sender)),
            _field("reply_to_from_id", sender.get("id")),
            _field("reply_to_chat", _display_chat(chat)),
            _field("reply_to_excerpt", _message_excerpt(reply)),
        ])
    else:
        fields.append(_bool_field("is_reply", False))
    if external:
        fields.extend([
            _field("external_reply_origin", external.get("origin")),
            _field("external_reply_excerpt", _message_excerpt(external)),
        ])
    if quote:
        fields.append(_field("quote", quote.get("text")))
    return [f for f in fields if f]


def _clock(epoch):
    """Telegram's own send time, local, in the same format as TIME."""
    if not epoch:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _format_message(update, kind, message, text):
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    sender_chat = message.get("sender_chat") or {}
    via_bot = message.get("via_bot") or {}
    header = [
        "telegram",
        _field("update_id", update.get("update_id")),
        _field("kind", kind),
        _field("chat_id", chat.get("id")),
        _field("chat_type", chat.get("type")),
        _field("chat_title", _display_chat(chat)),
        _field("message_id", message.get("message_id")),
        _field("sent_at", _clock(message.get("date"))),
        _field("edited_at", _clock(message.get("edit_date"))),
        _field("thread_id", message.get("message_thread_id")),
        _bool_field("is_topic", bool(message.get("is_topic_message"))),
        _field("from", _display_user(sender)),
        _field("from_id", sender.get("id")),
        _bool_field("from_is_bot", bool(sender.get("is_bot"))),
        _field("sender_chat", _display_chat(sender_chat)),
        _field("sender_chat_id", sender_chat.get("id")),
        _field("via_bot", _display_user(via_bot)),
        _bool_field("automatic_forward", bool(message.get("is_automatic_forward"))),
    ]
    header.extend(_forward_fields(message))
    header.extend(_reply_fields(message))
    header = " ".join(f for f in header if f)
    return f"[{header}]\n{text}"


def _extract_message(update):
    for kind in ("message", "edited_message", "channel_post", "edited_channel_post"):
        message = update.get(kind)
        if message:
            return kind, message
    return "unsupported", None


# ---- attachments -----------------------------------------------------------
# Files from ALLOWED chats are downloaded via the Bot API into a local inbox
# (inside the gitignored memory/ tree) so the agent can actually read them;
# the saved path is announced in the message text it sees. Disallowed chats
# are never fetched. getFile's own 20MB bot limit is enforced as our cap too.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_ATTACHMENT_SINGLE_KINDS = ("document", "video", "audio", "voice", "animation", "video_note", "sticker")


def _attachment_info(message):
    """(kind, file_id, file_name, mime, size) of the message's file, or None."""
    for kind in _ATTACHMENT_SINGLE_KINDS:
        item = (message or {}).get(kind)
        if item:
            return (kind, item.get("file_id"), item.get("file_name"),
                    item.get("mime_type"), item.get("file_size"))
    photos = (message or {}).get("photo") or []
    if photos:
        best = max(photos, key=lambda p: p.get("file_size") or 0)
        return ("photo", best.get("file_id"), None, "image/jpeg", best.get("file_size"))
    return None


def _safe_filename(name, fallback):
    name = os.path.basename(str(name or fallback))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or str(fallback)
    return name[:120]


def _attachments_dir():
    configured = os.environ.get("METTACLAW_TELEGRAM_ATTACHMENTS_DIR", "")
    if configured:
        return configured
    base = os.path.dirname(_offset_path or "") or "."
    return os.path.join(base, "memory", "attachments")


def _download_attachment(message):
    """Fetch the message's attachment; return an announcement line or None.

    Never raises, and never includes the token or a tokened URL in its output
    (transport exceptions are reduced to their type name)."""
    info = _attachment_info(message)
    if not info:
        return None
    kind, file_id, file_name, mime, size = info
    label = f"{kind} {file_name or file_id or '?'}" \
        + (f", {mime}" if mime else "") + (f", {size}b" if size else "")
    if not file_id:
        return f"[attachment {label} — no file_id]"
    if size and size > _MAX_ATTACHMENT_BYTES:
        return f"[attachment {label} — exceeds {_MAX_ATTACHMENT_BYTES}b cap, not downloaded]"
    try:
        resp = requests.get(_api("getFile"), params={"file_id": file_id}, timeout=30)
        resp.raise_for_status()
        remote_path = (resp.json().get("result") or {}).get("file_path")
        if not remote_path:
            return f"[attachment {label} — getFile returned no path]"
        dest_dir = _attachments_dir()
        os.makedirs(dest_dir, exist_ok=True)
        base = _safe_filename(file_name or os.path.basename(remote_path), (file_id or "file")[:16])
        dest = os.path.join(dest_dir, f"{(message or {}).get('message_id', 'x')}-{base}")
        partial = dest + ".part"
        url = f"{_api_base()}/file/bot{_token}/{remote_path}"
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                written = 0
                with open(partial, "wb") as f:
                    for chunk in r.iter_content(65536):
                        written += len(chunk)
                        if written > _MAX_ATTACHMENT_BYTES:
                            raise ValueError("size cap exceeded mid-download")
                        f.write(chunk)
            os.replace(partial, dest)
        finally:
            try:
                os.unlink(partial)
            except FileNotFoundError:
                pass
        return f"[attachment saved: {dest} ({label})]"
    except Exception as exc:
        return f"[attachment {label} — download failed: {type(exc).__name__}]"


def _attachment_job(update, kind, message, base_text):
    try:
        attachment_note = _download_attachment(message)
        text = (str(base_text) + "\n" if base_text else "") + str(
            attachment_note or "[attachment unavailable]"
        )
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        ready_message = dict(message)
        ready_message["text"] = text
        ready_message.pop("caption", None)
        with _msg_lock:
            _append_update_log(
                update, kind, ready_message, True, True,
                "attachment_ready"
            )
            _set_last(
                chat.get("id", ""),
                _format_message(update, kind, ready_message, text),
                bool(sender.get("is_bot")),
                _tier_for_sender(sender),
                update_id=int(update["update_id"]),
            )
        _wake_for_operator_message(sender)
    except Exception as exc:
        print("[telegram] attachment worker error:", type(exc).__name__)


def _enqueue_attachment(update, kind, message, base_text):
    # Each received file begins transport immediately. One slow Telegram file
    # must not consume another file's download opportunity.
    threading.Thread(
        target=_attachment_job,
        args=(update, kind, message, base_text),
        name="telegram-attachment-download",
        daemon=True,
    ).start()
    return 1


def _recover_pending_attachments(limit=100):
    """Retry received attachments that lack a completed ledger revision."""
    if not _log_path or not os.path.isfile(_log_path):
        return 0
    pending = {}
    try:
        with _log_lock:
            with open(_log_path, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    key = (str(record.get("chat_id", "")),
                           str(record.get("message_id", "")))
                    note = record.get("note")
                    if note == "attachment_dispatched" and record.get("raw"):
                        pending[key] = record
                    elif note == "attachment_ready":
                        pending.pop(key, None)
    except OSError:
        return 0
    recovered = 0
    records = sorted(
        pending.values(), key=lambda item: int(item.get("update_id", 0) or 0)
    )[-max(1, int(limit)):]
    for record in records:
        update = record.get("raw") or {}
        kind, message = _extract_message(update)
        if not message or not _attachment_info(message):
            continue
        _enqueue_attachment(
            update, kind, message,
            message.get("text") or message.get("caption") or "",
        )
        recovered += 1
    return recovered


def _append_update_log(update, kind, message, allowed, queued, note=""):
    if not _log_path:
        return True
    chat = (message or {}).get("chat") or {}
    sender = (message or {}).get("from") or {}
    text = (message or {}).get("text")
    caption = (message or {}).get("caption")
    record = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "update_id": update.get("update_id"),
        "kind": kind,
        "allowed": bool(allowed),
        "queued": bool(queued),
        "note": note,
        "chat_id": str(chat.get("id", "")),
        "chat_type": chat.get("type", ""),
        "chat_title": _display_chat(chat),
        "message_id": (message or {}).get("message_id"),
        "from": _display_user(sender),
        "from_id": str(sender.get("id", "")),
        "from_is_bot": bool(sender.get("is_bot")),
        "text": text if allowed else None,
        "caption": caption if allowed else None,
        "raw": update if allowed else None,
    }
    try:
        parent = os.path.dirname(_log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _log_lock:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return True
    except Exception as exc:
        print("[telegram] update log error:", exc)
        return False


# ---- arming energy ---------------------------------------------------------
# Every human message arms a fresh loop budget. Tiers size that burst per
# sender — full/mid/light — so a high-volume conversational partner does not
# hold the agent at maximum indefinitely, while everyone still wakes the agent
# and still gets answered. The map persists in a small JSON file the agent
# (via energy skills) and the operator (via /energy) both edit.

ENERGY_TIERS = ("full", "mid", "light")


def _tier_loops(tier):
    defaults = {"full": 50, "mid": 30, "light": 10}
    envnames = {"full": "METTACLAW_LOOPS_FULL", "mid": "METTACLAW_LOOPS_MID",
                "light": "METTACLAW_LOOPS_LIGHT"}
    # Live override: energy.json tier_loops takes priority over env and defaults
    try:
        import json as _json
        with open(_energy_path()) as _f:
            _edata = _json.load(_f)
        _tl = _edata.get('tier_loops', {})
        if tier in _tl:
            return max(1, int(_tl[tier]))
    except Exception:
        pass  # silently fall through to env/defaults
    try:
        return max(1, int(os.environ.get(envnames[tier], defaults[tier])))
    except (KeyError, ValueError, TypeError):
        return 50


def _energy_path():
    return os.environ.get("METTACLAW_ENERGY_PATH", "memory/energy.json")


def _sender_names_env():
    """Optional 'id:Name,id:Name' labels for the /energy menu."""
    out = {}
    for part in os.environ.get("METTACLAW_TELEGRAM_SENDER_NAMES", "").split(","):
        if ":" in part:
            sid, name = part.split(":", 1)
            if sid.strip() and name.strip():
                out[sid.strip()] = name.strip()
    return out


def _energy_load():
    """The energy map; seeded on first use from the legacy light-arm env."""
    with _energy_lock:
        try:
            with open(_energy_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                default = data.get("default", "full")
                if default not in ENERGY_TIERS:
                    default = "full"
                raw_senders = data.get("senders", {})
                raw_names = data.get("names", {})
                senders = ({str(k): v for k, v in raw_senders.items()
                            if v in ENERGY_TIERS}
                           if isinstance(raw_senders, dict) else {})
                names = ({str(k): str(v) for k, v in raw_names.items()}
                         if isinstance(raw_names, dict) else {})
                return {"default": default, "senders": senders,
                        "names": names}
        except (OSError, ValueError, TypeError):
            pass
        senders = {}
        for sid in os.environ.get(
                "METTACLAW_TELEGRAM_LIGHT_ARM_IDS", "").split(","):
            if sid.strip():
                senders[sid.strip()] = "light"
        return {"default": "full", "senders": senders,
                "names": _sender_names_env()}


def _tier_for_sender(sender):
    data = _energy_load()
    return data["senders"].get(str((sender or {}).get("id", "")),
                               data.get("default", "full"))


def _energy_label(data, sid):
    return data.get("names", {}).get(sid) or _sender_names_env().get(sid) or sid


def energy_view():
    """Human-readable summary the agent can print."""
    data = _energy_load()
    d = data.get("default", "full")
    parts = ["default: %s(%d)" % (d, _tier_loops(d))]
    for sid, tier in sorted(data.get("senders", {}).items()):
        parts.append("%s: %s(%d)" % (_energy_label(data, sid), tier,
                                     _tier_loops(tier)))
    return " | ".join(parts)


def _set_last(chat_id, text, from_bot=False, arm_tier="full", update_id=None):
    """Hand an input to the MeTTa client, which queues it for the next turn."""
    _metta_queue({"chat": str(chat_id), "text": str(text), "from_bot": bool(from_bot),
                  "tier": str(arm_tier), "update_id": "" if update_id is None else str(update_id)})


# Runtime events, such as a finished background job, arrive through the same
# activity queue as messages.  Their pseudo chat is never a reply target.
RUNTIME_CHAT = "runtime"


def enqueue_runtime_event(text, reason="runtime event", *, identity=None):
    """Deliver a runtime event as ordinary activity for the next turn and end
    any rest.  A plain send afterwards still goes where it would have."""
    identity = _runtime_id("runtime:" + uuid.uuid4().hex if identity is None else identity)
    import input_ack
    if input_ack.already_committed(identity): return True
    _sync()
    if identity in _value("_current_batch_update_ids"): return True
    with _metta_lock:
        if any(e.get("update_id") == identity for e in _metta_events): return True
        _metta_events.append({"chat": RUNTIME_CHAT, "text": "[runtime] " + str(text), "from_bot": False,
                             "tier": "light", "update_id": identity, "wake": str(reason)})
    if _host_notice is not None: _host_notice()
    return True


def pendingActivityCount():
    """Number of ordinary inputs not yet history-acknowledged."""
    return int(metta_value("pending_count", 0) or 0)


def preparedActivityCount():
    """Number of those inputs frozen into the current stable observation."""
    return int(metta_value("prepared_count", 0) or 0)


def current_batch_identity():
    """Bounded stable identity for the input presented to this model turn.

    Native MeTTa publishes the same epoch and update IDs into this projection;
    the legacy Python client owns them directly. Re-presenting retained input
    therefore has the same identity, while any newly included occurrence
    changes it.
    """
    _sync()
    with _msg_lock:
        epoch = int(_value("_context_activity_epoch"))
        identities = sorted(str(value) for value in _value("_current_batch_update_ids"))
    payload = (str(epoch) + "\0" + "\0".join(identities)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_CONTROL_REPLY_PREFIXES = (
    "activity:", "runtime-health:", "active model:",
    "active mode:", "arming energy", "model set to ", "mode set to ",
    "tap to switch:", "deleted message ",
    "delete refused:", "delete failed:", "cannot delete message ",
)

_REVISION_KINDS = frozenset((
    "message", "edited_message", "channel_post", "edited_channel_post",
))


def _semantic_message_key(record):
    """Stable identity of an inbound Telegram message revision."""
    if str(record.get("kind", "")) not in _REVISION_KINDS:
        return None
    chat_id = record.get("chat_id")
    message_id = record.get("message_id")
    if chat_id in (None, "") or message_id is None:
        return None
    return str(chat_id), str(message_id)


def _collapse_message_revisions(records):
    """Keep the newest ledger revision of each semantic message.

    The ledger itself remains append-only.  Moving a replacement to its
    revision position preserves the visible chronology of the projected
    conversation.
    """
    projected = []
    positions = {}
    for record in records:
        key = _semantic_message_key(record)
        if key is not None and key in positions:
            projected[positions[key]] = None
        if key is not None:
            positions[key] = len(projected)
        projected.append(record)
    return [record for record in projected if record is not None]


def conversation_window(max_chars=30000, max_events=48,
                        max_event_chars=6000, chat_id=None):
    """Render a causal, role-preserving window from the append-only ledger.

    The update log is the source of truth.  ``getActivityBatch`` fixes a byte
    frontier before the model prompt is assembled and records the update ids
    prepared for this turn.  Records beyond that frontier are future input;
    prepared records remain stable as NEW_ACTIVITY until history-backed
    acknowledgement.
    Deterministic slash-command chatter remains auditable in the ledger but is
    not conversation.
    """
    _sync()
    try:
        limit = max(1000, int(max_chars))
        event_limit = max(1, int(max_events))
        item_limit = max(200, int(max_event_chars))
        if not _log_path or not os.path.exists(_log_path):
            return route_view() + "\n\n(no prior conversation events)"
        with _msg_lock:
            frontier = _value("_context_frontier_bytes")
            excluded = set(_value("_current_batch_update_ids"))
        with _log_lock:
            size = os.path.getsize(_log_path)
            end = size if frontier is None else min(size, int(frontier))
            # A bounded tail avoids re-reading a multi-megabyte audit ledger.
            start = max(0, end - max(524288, limit * 8))
            with open(_log_path, "rb") as stream:
                stream.seek(start)
                data = stream.read(end - start)
        if start:
            newline = data.find(b"\n")
            data = data[newline + 1:] if newline >= 0 else b""

        records = []
        for raw in data.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            update_id = rec.get("update_id")
            if update_id is not None and str(update_id) in excluded:
                continue
            records.append(rec)

        rendered = []
        for rec in _collapse_message_revisions(records):
            if chat_id and str(rec.get("chat_id", "")) != str(chat_id):
                continue
            text_value = rec.get("text") or rec.get("caption")
            if not rec.get("allowed") or not text_value:
                continue
            kind = str(rec.get("kind", ""))
            note = str(rec.get("note", ""))
            if (note.startswith("slash_command:")
                    or note.startswith("slash_command_other_bot:")
                    or kind in ("callback_query", "outbound_delete")):
                continue
            text_value = str(text_value).strip()
            if (kind == "outbound"
                    and text_value.lower().startswith(_CONTROL_REPLY_PREFIXES)):
                continue
            if len(text_value) > item_limit:
                text_value = text_value[:item_limit - 1] + "…"
            stamp = str(rec.get("received_at", ""))
            who = "GODEL" if kind == "outbound" else str(
                rec.get("from") or "unknown")
            direction = "OUT" if kind == "outbound" else "IN"
            chat = str(rec.get("chat_title") or rec.get("chat_id") or "?")
            message_id = rec.get("message_id")
            header = "[%s %s %s chat=%s" % (stamp, direction, who, chat)
            if message_id is not None:
                header += " message_id=%s" % message_id
            rendered.append(header + "]\n" + text_value)

        chosen = []
        used = 0
        for event in reversed(rendered):
            cost = len(event) + (2 if chosen else 0)
            if chosen and (len(chosen) >= event_limit or used + cost > limit):
                break
            if not chosen and cost > limit:
                event = event[-limit:]
                cost = len(event)
            chosen.append(event)
            used += cost
        chosen.reverse()
        history = "\n\n".join(chosen) or "(no prior conversation events)"
        return route_view() + "\n\n" + history
    except Exception as exc:
        print("[telegram] conversation window error:", type(exc).__name__)
        return "(conversation window unavailable)"


def _operator_ids():
    """Sender ids allowed to use the model/quota controls (spending levers)."""
    raw = os.environ.get("METTACLAW_TELEGRAM_OPERATOR_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _is_operator(sender):
    return str((sender or {}).get("id", "")) in _operator_ids()


def _wake_peer_ids():
    """Non-operator senders whose ordinary messages may end a rest.

    A direct conversational line, such as a parent agent's bot, needs a
    timely answer without gaining the operator's controls.  Waking only ends
    the current rest: the woken turn spends the agent's own banked budget,
    and the message keeps its true sender and bot flag."""
    raw = os.environ.get("METTACLAW_TELEGRAM_WAKE_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def _request_wake(reason):
    """Interrupt a timed rest; the MeTTa client records why it woke."""
    _metta_queue({"wake_only": str(reason)})


def _wake_for_operator_message(sender):
    """An authenticated operator message is itself an explicit wake request.
    So is an ordinary message from a configured wake peer, which gains no
    operator control by waking the agent."""
    if _is_operator(sender):
        _request_wake("operator message")
        return True
    sender_id = str((sender or {}).get("id", ""))
    if sender_id and sender_id in _wake_peer_ids():
        _request_wake("peer message")
        return True
    return False


def _settings_keyboard(kind, prefix):
    import helper
    page = json.loads(helper.menu_page(kind, prefix))
    return {"inline_keyboard": [[{"text": label, "callback_data": data}
             for label, data in row] for row in page["rows"]]}


def _models_keyboard():
    return _settings_keyboard("models", "model")


def _modes_keyboard():
    return _settings_keyboard("modes", "mode")


def _fuels_keyboard():
    return _settings_keyboard("fuels", "fuel")


def _energy_keyboard():
    """Inline keyboard for arming tiers: one row for the default, one per
    known sender. Tapping writes the persisted energy map — zero LLM cost."""
    data = _energy_load()
    cur_default = data.get("default", "full")
    rows = []
    row = []
    for tier in ENERGY_TIERS:
        mark = "● " if tier == cur_default else ""
        row.append({"text": "%severyone: %s(%d)" % (mark, tier, _tier_loops(tier)),
                    "callback_data": "energy:default:" + tier})
    rows.append(row)
    known = dict(_sender_names_env())
    known.update({sid: _energy_label(data, sid)
                  for sid in data.get("senders", {})})
    for sid, name in sorted(known.items(), key=lambda kv: kv[1].lower()):
        cur = data.get("senders", {}).get(sid, cur_default)
        row = []
        for tier in ENERGY_TIERS:
            mark = "● " if tier == cur else ""
            cbd = "energy:%s:%s" % (sid, tier)
            if len(cbd.encode("utf-8")) > 64:
                continue
            row.append({"text": "%s%s: %s" % (mark, name, tier),
                        "callback_data": cbd})
        if row:
            rows.append(row)
    return {"inline_keyboard": rows}


_bot_username = None


def _known_username():
    """Configured/cached bot identity, without any network operation."""
    return str(
        _bot_username
        or os.environ.get("METTACLAW_TELEGRAM_BOT_USERNAME", "")
    ).lstrip("@")


def _peek_slash_command(text, sender):
    """Would _handle_slash_command take this? Decided without network calls, so
    the poll thread can hand it off and keep polling."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    head = stripped.split(None, 1)[0]
    cmd = head.split("@", 1)[0].lower()
    if cmd not in _CONTROL_COMMANDS:
        return None
    if "@" in head:
        # The polling path must not block on getMe. Identity is provisioned
        # alongside the token; an unknown addressed command is safely consumed.
        mine = _known_username().lower()
        if not mine or head.split("@", 1)[1].lower() != mine:
            return "slash_command_other_bot:" + head.split("@", 1)[1].lower()
    if not _is_operator(sender):
        return None
    return "slash_command:" + cmd


def _handle_slash_command(chat, sender, text):
    """Deterministic operator commands with zero LLM involvement.

    Zero LLM involvement: the reply is sent directly and the message is NOT
    queued for the agent, so a command costs no tokens and works even while
    the agent rests. A model switch takes effect on the agent's next turn
    (chat() reads SYNTHETIC_MODEL per call). Returns a log note when handled,
    None otherwise."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(None, 1)
    head = parts[0]
    cmd = head.split("@", 1)[0].lower()  # '/model@SomeBot' -> '/model'
    if "@" in head and cmd in _CONTROL_COMMANDS:
        # An @suffix names the addressee. Answering a command aimed at a
        # DIFFERENT bot switched the wrong agent's model live (2026-07-19).
        target = head.split("@", 1)[1].lower()
        mine = _known_username().lower()
        if not mine or target != mine:
            return "slash_command_other_bot:" + target
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cmd not in _CONTROL_COMMANDS:
        return None
    if not _is_operator(sender):
        # Operator controls are not available to other senders. Their message
        # flows to the agent as ordinary conversation instead.
        return None
    try:
        if cmd == "/energy":
            requests.post(_api("sendMessage"), json={
                "chat_id": chat.get("id"),
                "text": "arming energy — " + energy_view(),
                "reply_markup": _energy_keyboard(),
            }, timeout=15)
            return "slash_command:/energy"
        if cmd == "/fuels":
            import fuel_modes
            requests.post(_api("sendMessage"), json={
                "chat_id": chat.get("id"),
                "text": fuel_modes.fuels_view(),
                "reply_markup": _fuels_keyboard(),
            }, timeout=15)
            return "slash_command:/fuels"
        if cmd == "/modes":
            import loop_modes
            requests.post(_api("sendMessage"), json={
                "chat_id": chat.get("id"),
                "text": loop_modes.mode_view(),
                "reply_markup": _modes_keyboard(),
            }, timeout=15)
            return "slash_command:/modes"
        if cmd == "/models":
            import synthetic_llm
            requests.post(_api("sendMessage"), json={
                "chat_id": chat.get("id"),
                "text": "tap to switch — "
                        + synthetic_llm.model_catalog_status(),
                "reply_markup": _models_keyboard(),
            }, timeout=15)
            return "slash_command:/models"
        reply = _command_answer(cmd, arg, str(chat.get("id", "")))
        if reply is not None:
            send_message_to_chat(str(chat.get("id", "")), reply)
        return "slash_command:" + cmd
    except Exception as exc:
        print("[telegram] slash command error:", exc)
        return "slash_command_error:" + cmd


def _command_answer(cmd, arg, chat_id):
    """Carry out one operator control and return its reply text, or None when
    it has nothing to say. No Telegram call: the Bot API path sends the text
    itself; on the durable path the channel service sends it, and answers the
    operator itself if this takes too long."""
    if cmd in ("/start", "/stop"):
        import lifecycle
        if cmd == "/start":
            reply = lifecycle.start()
            if lifecycle.cognition_enabled():
                _request_wake("operator start")
        else:
            reply = lifecycle.stop()
            _request_wake("operator stop")
        return reply
    if cmd == "/wake":
        # Wake silently. The agent's next real reply is the confirmation;
        # a separate "awake" acknowledgement is just noise in the chat.
        _request_wake("/wake")
        return None
    if cmd == "/delete":
        fields = arg.rsplit(None, 1)
        if len(fields) != 2:
            return "usage: /delete <known-chat-id-or-title> <message-id>"
        target = _resolve_known_chat_id(fields[0])
        if not target:
            return "delete refused: chat is not in the local ledger"
        if (_allowed_chat_ids
                and target not in _allowed_chat_ids
                and target != chat_id):
            return "delete refused: chat is not allowed"
        return delete_message(target, fields[1])
    if cmd == "/claude_code_authorization":
        import claude_bridge
        return claude_bridge.set_authorized(not claude_bridge.authorized())
    if cmd == "/health":
        import runtime_health
        return runtime_health.report()
    if cmd == "/activity":
        import runtime_health
        return runtime_health.activity_report()
    if cmd == "/fuel":
        import fuel_modes
        if arg:
            return str(fuel_modes.set_fuel(arg))[:3800]
        return (fuel_modes.fuel_view()
                + " — /fuel <name> to switch, /fuels to list")[:3800]
    if cmd == "/mode":
        import loop_modes
        if arg:
            reply = loop_modes.set_mode(arg)
            _request_wake("mode switch")
            return str(reply)[:3800]
        return (loop_modes.mode_view()
                + " — /mode <name> to switch, /modes to list")[:3800]
    import synthetic_llm
    if cmd == "/quota":
        return str(synthetic_llm.quota())[:3800]
    if cmd == "/model":
        if arg:
            return str(synthetic_llm.set_model(arg))[:3800]
        return (f"active model: {synthetic_llm.current_model()} — "
                "/model <name> to switch, /models to list, /quota for budget")[:3800]
    return "unknown command " + cmd


def _dispatch_slash_command(chat, sender, text):
    """Give every operator control an independent, zero-LLM execution lane."""
    stripped = str(text or "").strip()
    command = stripped.split(None, 1)[0].split("@", 1)[0].lower() \
        if stripped.startswith("/") else ""
    if command not in _CONTROL_COMMANDS:
        return "ignored"
    threading.Thread(
        target=_handle_slash_command,
        args=(chat, sender, text),
        name="telegram-control-" + command.lstrip("/").replace("_", "-"),
        daemon=True,
    ).start()
    return "independent"


def _durable_timeout():
    try:
        return max(1.0, float(os.environ.get(
            "METTACLAW_TELEGRAM_SERVICE_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def _durable_record(key, position, result):
    """Log what a receipt proves, once, whether or not a sender still waits."""
    finished = False
    with _durable_lock:
        entry = _durable_pending.get(key)
        if entry is not None:
            entry["seen"].add(position)
            if len(entry["seen"]) >= entry["count"]:
                _durable_pending.pop(key, None)
                finished = True
    if finished:
        _durable_outbox.done(key)
    if isinstance(result, list) and result and result[0] == "uncertain":
        import durable_telegram
        _durable_outbox.hold(durable_telegram.lane_of(key))
    if entry is not None and isinstance(result, list) and result:
        if entry["kind"] == "send" and result[0] == "delivered":
            _log_outbound(entry["chat"], entry["pieces"][position], result[1])
        elif entry["kind"] == "delete" and result[0] == "delivered":
            _log_own_delete(entry["chat"], entry["message_id"])
        elif entry["kind"] == "delete" and result[0] == "failed" and result[1] == 400:
            _log_own_delete(entry["chat"], entry["message_id"],
                            "own_delete_terminal")
    _durable_receipts.record(key, position, result)


def _durable_submit(chat_id, commands, entry):
    """Record one keyed batch in the durable outbox and wait for its receipts.

    Returns (key, results). results is "queued" while the service has not yet
    stored the batch (it will be, in order, when the service answers), a
    ("refused", code) pair, a rejection reason string, or a list with one
    receipt per action, None where none arrived in time. A receipt arriving
    later, even after this process restarts, is logged like any other.
    """
    key = _durable.key(chat_id)
    entry = dict(entry, count=len(commands))
    with _durable_lock:
        _durable_pending[key] = dict(entry, seen=set())
    _durable_outbox.enqueue(key, commands, entry)
    deadline = time.monotonic() + _durable_timeout()
    stored = _durable_outbox.wait_stored(key, _durable_timeout())
    if stored is False:
        return key, "queued"
    if stored is not True:
        with _durable_lock:
            _durable_pending.pop(key, None)
        return key, ("refused", stored)
    return key, _durable_receipts.wait(
        key, len(commands), max(0.0, deadline - time.monotonic()))


def _durable_send_message(chat_id, body):
    import durable_telegram
    pieces = durable_telegram.chunks(body)
    if not pieces:
        return "send failed: empty message"
    key, results = _durable_submit(
        chat_id, [["send", piece, "plain"] for piece in pieces],
        {"kind": "send", "chat": str(chat_id), "pieces": pieces})
    if results == "queued":
        return ("send queued: the Telegram service is not answering; it goes "
                "out in order when the service returns (%s)" % key)
    if isinstance(results, tuple):
        return "send failed: the Telegram service refused it (%s)" % results[1]
    if isinstance(results, str):
        return "send failed: the Telegram service rejected it (%s)" % results
    ids = []
    for result in results:
        if result is None:
            return ("send accepted by the Telegram service; delivery is still "
                    "pending (%s)" % key)
        if result[0] == "delivered":
            ids.append(result[1])
        elif result[0] == "failed":
            return "send failed: Telegram rejected %s" % result[1]
        elif result[0] == "not-sent":
            return "send failed: the message was not sent"
        else:
            print("[telegram] chat lane %s: delivery of %s is uncertain (%s)"
                  % (durable_telegram.lane_of(key), key, result[1]))
            return ("send uncertain (%s): Telegram may or may not have it; "
                    "this occurrence will not be retried; later messages may proceed (%s)" % (result[1], key))
    reply = "sent message %s to chat %s" % (ids[0], chat_id)
    if len(ids) > 1:
        reply += " (in %d parts: %s)" % (len(ids), ", ".join(map(str, ids)))
    return reply


def _durable_delete_message(target, message_id):
    _, results = _durable_submit(
        target, [["delete", int(message_id)]],
        {"kind": "delete", "chat": str(target), "message_id": int(message_id)})
    if results == "queued":
        return "delete queued: the Telegram service is not answering"
    if isinstance(results, tuple):
        return "delete failed: the Telegram service refused it (%s)" % results[1]
    if isinstance(results, str):
        return "delete failed: the Telegram service rejected it (%s)" % results
    result = results[0]
    if result is None:
        return "delete accepted by the Telegram service; still pending"
    if result[0] == "delivered":
        return "deleted message %s from chat %s" % (message_id, target)
    if result[0] == "failed" and result[1] == 400:
        return ("cannot delete message %s: it is older than 48h, or a "
                "forward (whoever forwarded it must delete it), or not the "
                "bot's own message" % message_id)
    return "delete failed: %s" % result[1:]


def _register_menu_commands():
    """Publish the deterministic controls to Telegram's slash-command menu."""
    try:
        response = requests.post(
            _api("setMyCommands"),
            json={
                "commands": [
                    {"command": command, "description": description}
                    for command, description in _MENU_COMMANDS
                ]
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError("Telegram rejected the command menu")
        _health_update(menu_status="ok",
                       menu_registered_at=time.time())
    except Exception as exc:
        # Menu discoverability is not allowed to take down message polling;
        # typed slash commands continue to work and the health watch records
        # registration failures from the service journal.
        _health_update(menu_status="error",
                       menu_error_at=time.time(),
                       menu_error_type=type(exc).__name__)
        print("[telegram] command menu registration failed:",
              type(exc).__name__)


# ---- MeTTa client ----------------------------------------------------------
# With METTACLAW_TELEGRAM_CLIENT=metta, channels/telegram.metta is Lila's
# Telegram client and this module starts no thread of its own. It keeps what
# calls the Bot API directly (button menus, attachment downloads, uploads),
# answers the Python and Prolog code that asks about Telegram from state the
# MeTTa client publishes here, and checks the channel service itself where
# an answer must be current (the unread-input guard on sends).

_metta = {}
_client_seen = None
_metta_lock = threading.Lock()
_metta_events = []
_handover_offered = set()


def _metta_queue(item):
    with _metta_lock:
        _metta_events.append(item)
    if _host_notice is not None: _host_notice()


def metta_take_runtime_events():
    """Runtime events, finished attachments and wake requests, oldest first,
    as one JSON array; each is handed over once."""
    with _metta_lock:
        items = _metta_events[:]
        del _metta_events[:]
    import mode_channel
    for uid, update in mode_channel.handover_inputs():
        if not update or uid in _handover_offered:
            continue
        kind, message = _extract_message(update)
        if message:
            sender = message.get("from", {})
            items.append({"chat": str(message["chat"]["id"]),
                          "text": _format_message(update, kind, message, message.get("text") or message.get("caption") or "[attachment]"),
                          "from_bot": bool(sender.get("is_bot")), "tier": _tier_for_sender(sender), "update_id": uid})
            _handover_offered.add(uid)
    return json.dumps(items, ensure_ascii=False)


def handover_ids(ids):
    """Validate a complete ID batch; error atoms grant no processing authority."""
    if isinstance(ids, str):
        values = [] if ids == "" else ids.split(",")
    elif isinstance(ids, (list, tuple)):
        values = ids
    elif type(ids) is int:
        values = [ids]
    else:
        raise ValueError("handover requires update IDs or comma-separated update IDs")
    result, seen = [], set()
    for value in values:
        if type(value) not in (int, str):
            raise ValueError("invalid handover update ID")
        text = str(value)
        if (not text or len(text) > 19 or not text.isascii() or not text.isdigit()
                or str(int(text)) != text or not 0 <= int(text) < 2**63):
            raise ValueError("invalid handover update ID")
        if text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _runtime_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"runtime:[A-Za-z0-9_.:-]{1,160}",value):
        raise ValueError("invalid runtime occurrence ID")
    return value


def activity_source_ack(ids):
    """Settle checkpointed input at its source."""
    if isinstance(ids, str):
        values = [] if ids == "" else ids.split(",")
    elif isinstance(ids, (list, tuple)):
        values = list(ids)
    elif type(ids) is int:
        values = [ids]
    else:
        raise ValueError("invalid activity source batch")
    import input_ack
    updates, runtime = [], []
    # Validate the complete batch before granting any processing authority.
    for value in values:
        if isinstance(value,str) and value.startswith("runtime:"):
            runtime.append(_runtime_id(value))
        else:
            updates.append(value)
    updates = handover_ids(updates)
    if runtime and not input_ack.committed(runtime): return 0
    return handover_ack(updates) if updates else 1


def handover_ack(ids):
    values = handover_ids(ids)
    import mode_channel
    # A failed durable acknowledgement must leave the notice unprocessed.
    if not mode_channel.acknowledge_handover(values):
        return 0
    import input_ack
    try:
        input_ack.processed_update_ids(values)
    except input_ack.LedgerUnavailable:
        # The durable handover remains settled; retry this idempotent metadata
        # transition at the next boundary, never replay the cognitive actions.
        return 0
    return 1


# What this process's MeTTa client last published (_sync takes it up from its
# file). Other modules read these under their own names, which stay out of the
# module's globals so that a read always goes through __getattr__; a test may
# assign one, and its value then stands for this module's code too.
_published = {"_reply_chat_id": "", "_activity_epoch": 0, "_context_activity_epoch": 0,
              "_context_frontier_bytes": None, "_current_batch_update_ids": frozenset()}


def _value(name):
    """A published value: a test's assignment if one stands, else the client's."""
    if name in globals():
        return globals()[name]
    _sync()
    return _published[name]


def __getattr__(name):
    if name in _published:
        return _value(name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _client_path():
    """The file beside the health file where the MeTTa client publishes."""
    health = _health_path()
    return (health[:-5] if health.endswith(".json") else health) + ".client.json"


def _sync():
    """Take up what the MeTTa client of this process last published, once its
    file changed. It rewrites the file whole, so a new identity is a new
    value; another process's client is read through metta_value only."""
    global _client_seen
    try:
        status = os.stat(_client_path())
    except OSError:
        return
    seen = (status.st_ino, status.st_mtime_ns, status.st_size)
    if seen == _client_seen:
        return
    try:
        with open(_client_path(), encoding="utf-8") as stream:
            published = json.load(stream)
    except (OSError, ValueError):
        return
    if not isinstance(published, dict):
        return
    with _msg_lock:
        _client_seen = seen
        _metta.clear()
        _metta.update(published)
        if published.get("pid") != os.getpid():
            return
        frontier = published.get("context_frontier")
        _published.update(
            _reply_chat_id=str(published.get("reply_chat") or ""),
            _activity_epoch=int(published.get("activity_epoch") or 0),
            _context_activity_epoch=int(published.get("context_epoch") or 0),
            _context_frontier_bytes=None if frontier is None else int(frontier),
            _current_batch_update_ids=frozenset(v for v in str(published.get("batch_update_ids") or "").split(",") if v))
        titles = published.get("titles")
        if isinstance(titles, dict):
            _chat_titles.update({str(chat): str(title) for chat, title in titles.items()})


def metta_value(name, default=None):
    """What the MeTTa client published, in this process or another."""
    _sync()
    return _metta.get(name, default)


def _metta_unread_chats():
    """Read all pending chats; an incomplete read cannot establish absence."""
    import durable_telegram
    import attention
    path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET", "")
    if not path:
        return None
    _sync()
    prepared = set(_value("_current_batch_update_ids"))
    unread = set()
    try:
        for task, observation in durable_telegram.Channel(path, os.devnull).iter_pending():
            attention.observe_pending([(task, observation)])
            try:
                value = json.loads(observation)
            except ValueError:
                return None
            if value and value[0] == "input" and len(value) == 5:
                update = value[4] if isinstance(value[4], dict) else {}
                if str(update.get("update_id", "")) not in prepared:
                    unread.add(str(value[1]).split(".", 1)[0])
    except (OSError, durable_telegram.ChannelError):
        return None
    return unread


def metta_effect_turn():
    with _effect_lock:
        return "" if _effect_turn is None else str(_effect_turn)


def metta_has_attachment(message_json):
    try:
        return bool(_attachment_info(json.loads(message_json)))
    except ValueError:
        return False


def metta_attachment(update_json, kind, text):
    update = json.loads(update_json)
    message = update.get(kind) or {}
    _enqueue_attachment(update, kind, message, text or "")
    return 1


def metta_slash_command(update_json, kind):
    """A command addressed with @bot, which the service does not answer
    itself. Handled on its own thread, as before."""
    message = json.loads(update_json).get(kind) or {}
    text = message.get("text") or message.get("caption") or ""
    note = _peek_slash_command(text, message.get("from")) or "slash_command_unknown"
    _dispatch_slash_command(message.get("chat") or {}, message.get("from"), text)
    return note


def metta_command_answer(name, args, chat):
    """The text of a delegated operator command the service asked about."""
    try:
        text = _command_answer(str(name), str(args), str(chat))
    except Exception as exc:
        print("[telegram] operator command failed:", name, type(exc).__name__)
        return "%s failed: %s" % (name, type(exc).__name__)
    if text is None:
        return "Waking." if name == "/wake" else "Done."
    return str(text)


def metta_foreign_receipt(observation):
    """A receipt for a send this module made (a menu's text reply)."""
    import input_ack
    if input_ack.receipt(observation):
        return 1
    value = json.loads(observation)
    if value[0] == "delivery":
        _durable_record(value[1], value[2], value[4])
    elif value[0] == "rejected":
        with _durable_lock:
            known = _durable_pending.pop(value[1], None) is not None
        if known:
            _durable_outbox.done(value[1])
        _durable_receipts.record(value[1], -1, value[2])
    elif value[0] == "released":
        with _durable_lock:
            entry = _durable_pending.pop(value[2], None)
        if entry is not None:
            _durable_outbox.done(value[2])
        _durable_outbox.release(value[1])
    return 1


def start_telegram(token="", chat_id=""):
    global _token, _allowed_chat_ids
    global _offset_path, _log_path, _bot_username, _primary_chat_id
    global _transport, _durable, _durable_receipts, _durable_outbox
    _token = str(
        token
        or os.environ.get("METTACLAW_TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    )
    configured_ids = ",".join(
        part
        for part in [
            str(chat_id or ""),
            os.environ.get("METTACLAW_TELEGRAM_CHAT_ID", ""),
            os.environ.get("TELEGRAM_CHAT_ID", ""),
            os.environ.get("METTACLAW_TELEGRAM_ALLOWED_CHAT_IDS", ""),
        ]
        if part
    )
    _allowed_chat_ids = _split_chat_ids(configured_ids)
    _primary_chat_id = str(
        os.environ.get("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID", "").strip()
    )
    _offset_path = os.environ.get("METTACLAW_TELEGRAM_OFFSET_PATH", "")
    _log_path = os.environ.get("METTACLAW_TELEGRAM_LOG_PATH", "")
    configured_username = os.environ.get(
        "METTACLAW_TELEGRAM_BOT_USERNAME", "").lstrip("@")
    if configured_username:
        _bot_username = configured_username
    if not _token:
        print("[telegram] disabled: set METTACLAW_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN")
        return None
    _transport = os.environ.get("METTACLAW_TELEGRAM_TRANSPORT", "bot-api")
    if _transport != "durable":
        print("[telegram] disabled: the MeTTa client needs the durable transport")
        return None
    import durable_telegram
    socket_path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET", "")
    sequence_path = os.environ.get(
        "METTACLAW_TELEGRAM_SEQUENCE_PATH",
        (_offset_path or "telegram") + ".durable-sequence")
    if not socket_path:
        print("[telegram] disabled: durable transport needs "
              "METTACLAW_TELEGRAM_SERVICE_SOCKET")
        return None
    _durable = durable_telegram.Channel(socket_path, sequence_path)
    _durable_receipts = durable_telegram.Receipts()
    _durable_outbox = durable_telegram.Outbox(_durable, os.environ.get(
        "METTACLAW_TELEGRAM_OUTBOX_PATH",
        (_offset_path or "telegram") + ".durable-outbox"))
    with _durable_lock:
        for key, entry in _durable_outbox.entries.items():
            if entry:
                _durable_pending[key] = dict(entry, seen=set())
    _health_update(running=True, started_at=time.time(),
                   client="metta", menu_status="pending", poll_status="starting")
    threading.Thread(target=_register_menu_commands, daemon=True).start()
    _recover_pending_attachments()
    return "metta"


def _reply_target(chat_id=""):
    _sync()
    if chat_id:
        return str(chat_id)
    with _msg_lock:
        # A plain `(send ...)` means "send to the primary operator", not
        # "send wherever the last cross-chat observation happened to come
        # from". Other audiences remain available through the explicit
        # addressed send. Deployments without a configured primary retain the
        # legacy reply behavior.
        return _primary_chat_id or _value("_reply_chat_id") or _last_chat_id


def route_view():
    """Project the actual default-send rule into model context."""
    _sync()
    with _msg_lock:
        if _primary_chat_id:
            title = _chat_titles.get(_primary_chat_id, "primary operator")
            return (
                "TELEGRAM ROUTE: plain (send ...) is bound to %s chat_id=%s. "
                "For every other audience use (send-telegram-chat chat_id "
                '"message"). Reading another chat cannot change this route.'
                % (title, _primary_chat_id)
            )
        current = _value("_reply_chat_id") or _last_chat_id
        return (
            "TELEGRAM ROUTE: no primary chat is configured; plain (send ...) "
            "uses the current reply chat%s. Prefer an explicit addressed "
            "send when multiple chats are visible."
            % ((" chat_id=" + current) if current else "")
        )


def _log_outbound(chat_id, text, message_id=None):
    """Record the agent's own send in the local update log so the short-term
    memory (recent_activity) shows both sides of the conversation.

    Recording `message_id` is what lets the agent later delete its own send:
    Telegram never reports a bot's outbound messages back through getUpdates,
    so if the id is not captured here it is lost, and `deleteMessage` has
    nothing to aim at. That gap is why a "clean up the test message" request
    turned into a long hunt for an id that was never stored."""
    _sync()
    if not _log_path:
        return
    try:
        record = {
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": "outbound",
            "allowed": True,
            "queued": False,
            "note": "own_send",
            "chat_id": str(chat_id),
            "chat_title": _chat_titles.get(str(chat_id), str(chat_id)),
            "from": "me",
            "from_is_bot": True,
            "message_id": message_id,
            "text": str(text),
        }
        line = json.dumps(record, ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        with _log_lock:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as exc:
        print("[telegram] outbound log error:", exc)


def _log_own_delete(chat_id, message_id, note="own_delete"):
    """Tombstone one terminal deletion so cleanup never targets it again."""
    if not _log_path:
        return
    try:
        record = {
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": "outbound_delete",
            "allowed": True,
            "queued": False,
            "note": note,
            "chat_id": str(chat_id),
            "from": "me",
            "from_is_bot": True,
            "message_id": int(message_id),
            "text": None,
        }
        line = json.dumps(record, ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        with _log_lock:
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line)
    except (OSError, TypeError, ValueError) as exc:
        print("[telegram] delete tombstone log error:", exc)


def send_message(text, chat_id=""):
    chat_id = _reply_target(chat_id)
    if not _token or not chat_id:
        print("[telegram] cannot send: missing token or chat id")
        return "send failed: missing token or chat id"
    if (_allowed_chat_ids and str(chat_id) not in _allowed_chat_ids
            and str(chat_id) != _primary_chat_id):
        return "send failed: target chat is not allowed"
    if _transport == "durable":
        return _durable_send_message(chat_id, str(text).replace("\\n", "\n"))
    try:
        body = str(text).replace("\\n", "\n")
        resp = requests.post(
            _api("sendMessage"),
            json={"chat_id": chat_id, "text": body},
            timeout=30,
        )
        delivered = False
        message_id = None
        try:
            payload = resp.json()
            delivered = bool(resp.ok and payload.get("ok"))
            message_id = (payload.get("result") or {}).get("message_id")
        except ValueError:
            pass
        if delivered:
            # STM records only what Telegram actually accepted — with the id,
            # so the agent can delete its own message later.
            _log_outbound(chat_id, body, message_id)
            return "sent message %s to chat %s" % (message_id, chat_id)
        else:
            print(f"[telegram] send rejected (HTTP {resp.status_code})")
            return "send failed: Telegram rejected HTTP %s" % resp.status_code
    except requests.exceptions.RequestException as exc:
        # A transient network failure on send must never cross Janus and kill the
        # loop (the same failure class as a provider exception crossing Janus).
        print(f"[telegram] send failed ({type(exc).__name__})")
        return "send failed: %s" % type(exc).__name__


def begin_effect_turn(turn):
    """Open the model-effect scope for one cognitive turn.

    Re-entering the same turn is deliberately a no-op: PeTTa may revisit a
    reduction while resolving later goals, but an already attempted external
    send must not become a second Telegram message.
    """
    _sync()
    global _effect_turn, _effect_activity_epoch
    turn = str(turn)
    # The context frontier was fixed atomically with the activity drain. A
    # message received after that drain must not be laundered into this turn by
    # capturing the newer live epoch here.
    with _msg_lock:
        context_epoch = _value("_context_activity_epoch")
    import attention
    if attention.enabled():
        _metta_unread_chats()
    with _effect_lock:
        if turn != _effect_turn:
            _effect_turn = turn
            _effect_activity_epoch = context_epoch
            _effect_sends.clear()
        stimulus_frontier.publish(
            turn, _effect_activity_epoch, _value("_activity_epoch")
        )
    return turn


def rest_status():
    """(is_resting, seconds_left) as seen from outside the sleeping loop."""
    until = float(metta_value("sleep_until", 0) or 0)
    left = until - time.time()
    return (left > 0, math.ceil(left) if left > 0 else 0)


def send_message_to_chat(chat_id, text):
    return send_message(text, chat_id=chat_id)


def delete_message(chat_id, message_id):
    """Execute one exact deletion after the caller has authorized its target.

    This provider primitive is intentionally not the model skill: a Telegram
    group administrator may be able to delete messages written by others.
    Model calls go through ``delete_recorded_message``; the authenticated
    operator slash path may authorize a legacy id absent from the ledger.
    The result says which provider case occurred rather than failing silently.
    """
    token = _token or os.environ.get("METTACLAW_TELEGRAM_BOT_TOKEN", "")
    if not token:
        return "delete failed: no bot token configured"
    target = str(chat_id)
    if (_allowed_chat_ids and target not in _allowed_chat_ids
            and target != _primary_chat_id):
        return "delete refused: target chat is not allowed"
    if _transport == "durable":
        return _durable_delete_message(target, message_id)
    try:
        resp = requests.post(_api("deleteMessage", token),
                             json={"chat_id": target,
                                   "message_id": int(message_id)},
                             timeout=15)
        payload = resp.json()
        if payload.get("ok"):
            _log_own_delete(target, message_id)
            return "deleted message %s from chat %s" % (message_id, target)
        desc = str(payload.get("description", "")).lower()
        if "can't be deleted" in desc or "message to delete not found" in desc:
            _log_own_delete(target, message_id, "own_delete_terminal")
            return ("cannot delete message %s: it is older than 48h, or a "
                    "forward (whoever forwarded it must delete it), or not the "
                    "bot's own message" % message_id)
        return "delete failed: %s" % payload.get("description", resp.status_code)
    except (requests.exceptions.RequestException, ValueError, TypeError) as exc:
        return "delete failed (%s): %s" % (type(exc).__name__, exc)


def _resolve_known_chat_id(chat):
    """Resolve one exact id or title already present in the local ledger."""
    wanted = str(chat).strip()
    if not wanted:
        return ""
    matches = set()
    if _log_path and os.path.isfile(_log_path):
        try:
            with _log_lock:
                with open(_log_path, "r", encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        chat_id = str(record.get("chat_id", ""))
                        title = str(record.get("chat_title", ""))
                        if wanted == chat_id or wanted.casefold() == title.casefold():
                            if chat_id:
                                matches.add(chat_id)
        except OSError:
            return ""
    return next(iter(matches)) if len(matches) == 1 else ""


def _send_upload(method, field, path, caption, chat_id):
    """POST one file to Telegram. Returns a result string the agent can read —
    success or the actual reason, never a silent failure."""
    chat_id = _reply_target(chat_id)
    path = str(path)
    token = _token or os.environ.get("METTACLAW_TELEGRAM_BOT_TOKEN", "")
    if not os.path.isfile(path):
        # Check the file first: "no such file" is the answer the caller can act
        # on, and it is the common case (a placeholder path copied verbatim).
        return "send failed: no such file: " + path
    if not token:
        return "send failed: no bot token configured"
    if not chat_id:
        return ("send failed: no chat to send to — pass one explicitly, "
                "e.g. (send-image \"/path.svg\" \"caption\" \"123456789\")")
    if (_allowed_chat_ids and str(chat_id) not in _allowed_chat_ids
            and str(chat_id) != _primary_chat_id):
        return "send failed: target chat is not allowed"
    size = os.path.getsize(path)
    if size > 50 * 1024 * 1024:
        return "send failed: %s is %d bytes; Telegram's limit is 50MB" % (path, size)
    try:
        with open(path, "rb") as fh:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = str(caption).replace("\\n", "\n")[:1024]
            resp = requests.post(_api(method, token), data=data,
                                 files={field: (os.path.basename(path), fh)},
                                 timeout=120)
        ok = False
        message_id = None
        try:
            payload = resp.json()
            ok = bool(resp.ok and payload.get("ok"))
            message_id = (payload.get("result") or {}).get("message_id")
        except ValueError:
            pass
        if not ok:
            detail = ""
            try:
                detail = ": " + str(resp.json().get("description", ""))[:160]
            except ValueError:
                pass
            return "send failed (HTTP %s)%s" % (resp.status_code, detail)
        _log_outbound(chat_id, "[%s %s] %s" % (field, os.path.basename(path),
                                               caption or ""), message_id)
        return "sent %s (%d bytes) to chat %s (msg %s)" % (
            os.path.basename(path), size, chat_id, message_id)
    except requests.exceptions.RequestException as exc:
        return "send failed (%s): %s" % (type(exc).__name__, exc)


def _rasterize_svg(path):
    """SVG -> PNG so it previews inline. Returns the PNG path, or "" if no
    converter is available (the caller then falls back to sendDocument)."""
    out = os.path.join(tempfile.gettempdir(),
                       os.path.basename(path).rsplit(".", 1)[0] + ".png")
    try:
        import cairosvg
        cairosvg.svg2png(url=path, write_to=out, output_width=1400)
        return out
    except Exception:
        pass
    for tool in (["rsvg-convert", "-w", "1400", "-o", out, path],
                 ["inkscape", "--export-type=png", "--export-width=1400",
                  "--export-filename=" + out, path],
                 ["convert", "-density", "150", path, out]):
        try:
            if subprocess.run(tool, capture_output=True,
                              timeout=60).returncode == 0 and os.path.isfile(out):
                return out
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def send_file(path, caption="", chat_id=""):
    """Send any file as a document (appears as an attachment)."""
    return _send_upload("sendDocument", "document", path, caption, chat_id)


def send_image(path, caption="", chat_id=""):
    """Send an image so it previews inline. SVG is rasterized to PNG when a
    converter exists; otherwise it is sent as a document with a note, because
    Telegram cannot preview SVG."""
    path = str(path)
    if path.lower().endswith(".svg"):
        png = _rasterize_svg(path)
        if png:
            result = _send_upload("sendPhoto", "photo", png, caption, chat_id)
            try:
                os.unlink(png)
            except OSError:
                pass
            return result
        note = (caption + " " if caption else "") + "(SVG: no rasterizer here, sent as a file)"
        return _send_upload("sendDocument", "document", path, note, chat_id)
    return _send_upload("sendPhoto", "photo", path, caption, chat_id)

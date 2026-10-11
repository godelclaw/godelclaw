import os
import json
import math
import subprocess
import tempfile
import re
import threading
import time

import requests

import stimulus_frontier

_running = False
_thread = None
_token = ""
_allowed_chat_ids = set()
_allow_private_chats = True
_last_chat_id = ""
_reply_chat_id = ""
_primary_chat_id = ""
_last_message_is_human = False
_last_from_bot = False
_last_arm_tier = "full"
_pending_messages = []
_prepared_messages = []
_activity_epoch = 0
_context_activity_epoch = 0
_context_frontier_bytes = None
_current_batch_update_ids = set()
_offset = None
_offset_path = ""
# "bot-api" polls and sends here. "durable" makes a CeTTa channel service the
# bot's only poller and sender; this process reads its deliveries and receipts.
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
# Rest state. The poll thread keeps running while the agent's main loop
# sleeps, so it can answer "he is resting" and end the rest on request.
_sleep_until = 0.0          # epoch when the current rest ends; 0 = awake
_wake_event = threading.Event()
_wake_lock = threading.Lock()
_wake_reason = ""
_rest_notice_sent = False
_health_lock = threading.RLock()
_health_state = {}
_health_last_write = 0.0
_effect_lock = threading.RLock()
_effect_turn = None
_effect_activity_epoch = None
_effect_attention_revision = None
_effect_sends = set()

_CONTROL_COMMANDS = (
    "/start", "/stop",
    "/model", "/models", "/mode", "/modes", "/engine", "/engines",
    "/fuel", "/fuels",
    "/quota", "/wake", "/energy", "/health", "/activity", "/delete",
    "/claude_code_authorization",
)

_MENU_COMMANDS = (
    ("start", "Enable cognition with the operator latch"),
    ("stop", "Stop cognition with the operator latch"),
    ("engine", "Show or switch the evaluator engine"),
    ("engines", "List evaluator engines"),
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


def _health_update(force=False, **fields):
    """Atomically publish non-secret liveness facts for an external monitor."""
    global _health_last_write
    now = time.time()
    with _health_lock:
        _health_state.update(fields)
        _health_state["observed_at"] = now
        if not force and now - _health_last_write < 15:
            return
        path = _health_path()
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".telegram-health-",
                                       dir=directory, text=True)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(_health_state, fh, sort_keys=True)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            _health_last_write = now
        except OSError as exc:
            print("[telegram] health write failed:", type(exc).__name__)


def _api_base():
    return os.environ.get("METTACLAW_TELEGRAM_BASE_URL",
                          "https://api.telegram.org")


def _api(method, token=""):
    return f"{_api_base()}/bot{token or _token}/{method}"


def _split_chat_ids(raw):
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _chat_is_allowed(chat):
    chat_id = str(chat.get("id", ""))
    if _allow_private_chats and chat.get("type") == "private":
        return True
    return chat_id in _allowed_chat_ids


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


def _energy_save(data):
    with _energy_lock:
        path = _energy_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except OSError as exc:
            print("[telegram] energy save failed:", exc)
            return False


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


def energy_set(who, tier):
    """Set the arming tier for 'default', a sender id, or a known name."""
    tier = str(tier).strip().lower()
    alias = {"50": "full", "30": "mid", "10": "light"}
    tier = alias.get(tier, tier)
    if tier not in ENERGY_TIERS:
        return "energy-set failed: tier must be full, mid or light (got %r)" % tier
    who = str(who).strip()
    with _energy_lock:
        data = _energy_load()
        if who.lower() in ("default", "everyone", "*"):
            data["default"] = tier
            ok = _energy_save(data)
            return ("default arming set to %s(%d)" % (tier, _tier_loops(tier))
                    if ok else "energy-set failed: could not persist")
        sid = who
        if not who.lstrip("-").isdigit():
            matches = [k for k, v in data.get("names", {}).items()
                       if v.lower() == who.lower()]
            if not matches:
                matches = [k for k, v in _sender_names_env().items()
                           if v.lower() == who.lower()]
            if not matches:
                return ("energy-set failed: unknown name %r — use a numeric "
                        "sender id, or one of: %s" % (who, ", ".join(
                            sorted(set(data.get("names", {}).values())
                                   | set(_sender_names_env().values())))
                            or "(none known)"))
            sid = matches[0]
        data["senders"][sid] = tier
        data.setdefault("names", {}).update({
            sid: data.get("names", {}).get(sid)
            or _sender_names_env().get(sid, sid)})
        ok = _energy_save(data)
        return ("%s arming set to %s(%d)" % (_energy_label(data, sid), tier,
                                             _tier_loops(tier))
                if ok else "energy-set failed: could not persist")


def _set_last(chat_id, text, from_bot=False, arm_tier="full", update_id=None):
    global _last_chat_id, _activity_epoch
    if metta_client():
        _metta_queue({"chat": str(chat_id), "text": str(text), "from_bot": bool(from_bot),
                      "tier": str(arm_tier), "update_id": "" if update_id is None else str(update_id)})
        return
    with _msg_lock:
        _last_chat_id = str(chat_id)
        _pending_messages.append(
            (_last_chat_id, str(text), bool(from_bot), str(arm_tier),
             update_id))
        # Monotone receipt for ordinary inbound stimuli. The model turn captures
        # the prepared frontier; a later receipt can interrupt its unexecuted
        # command suffix without dictating how large the proposed batch was.
        _activity_epoch += 1
        if _effect_turn is not None and _effect_activity_epoch is not None:
            stimulus_frontier.publish(
                _effect_turn, _effect_activity_epoch, _activity_epoch
            )


# Runtime events, such as a finished background job, arrive through the same
# activity queue as messages.  Their pseudo chat is never a reply target.
RUNTIME_CHAT = "runtime"


def enqueue_runtime_event(text, reason="runtime event"):
    """Deliver a runtime event as ordinary activity for the next turn and end
    any rest.  A plain send afterwards still goes where it would have."""
    global _activity_epoch
    if metta_client():
        _metta_queue({"chat": RUNTIME_CHAT, "text": "[runtime] " + str(text), "from_bot": False,
                      "tier": "light", "update_id": "", "wake": str(reason)})
        return True
    with _msg_lock:
        _pending_messages.append(
            (RUNTIME_CHAT, "[runtime] " + str(text), False, "light", None))
        _activity_epoch += 1
        if _effect_turn is not None and _effect_activity_epoch is not None:
            stimulus_frontier.publish(
                _effect_turn, _effect_activity_epoch, _activity_epoch
            )
    _request_wake(reason)
    return True


def getLastMessage():
    global _reply_chat_id, _last_message_is_human, _last_from_bot
    global _last_arm_tier
    with _msg_lock:
        if not _pending_messages:
            _last_message_is_human = False
            _last_arm_tier = "full"
            return ""
        item = _pending_messages.pop(0)
        # Tuples have grown over time; older queued entries stay readable.
        tier = "full"
        if len(item) == 2:
            _reply_chat_id, msg = item
            from_bot = False
        elif len(item) == 3:
            _reply_chat_id, msg, from_bot = item
        elif len(item) == 4:
            _reply_chat_id, msg, from_bot, tier = item
        else:
            _reply_chat_id, msg, from_bot, tier, _update_id = item
        _last_from_bot = bool(from_bot)
        _last_message_is_human = not _last_from_bot
        _last_arm_tier = str(tier) if _last_message_is_human else "full"
        return msg


def _pending_fields(item):
    """Read every historical pending-tuple shape without rewriting it."""
    tier = "full"
    update_id = None
    if len(item) == 2:
        chat, message = item
        from_bot = False
    elif len(item) == 3:
        chat, message, from_bot = item
    elif len(item) == 4:
        chat, message, from_bot, tier = item
    else:
        chat, message, from_bot, tier, update_id = item[:5]
    return str(chat), str(message), bool(from_bot), str(tier), update_id


def _write_activity_receipt(kind, update_ids):
    """Append and fsync an input-delivery receipt without message content."""
    if not _log_path:
        return True
    record = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": str(kind),
        "update_ids": [str(value) for value in update_ids],
    }
    line = json.dumps(record, ensure_ascii=False,
                      separators=(",", ":")) + "\n"
    try:
        with _log_lock:
            with open(_log_path, "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        return True
    except Exception as exc:
        print("[telegram] activity receipt error:", type(exc).__name__)
        return False


def initialize_activity_receipts(preserve_update_ids=()):
    """Begin transactional delivery without replaying unknowable legacy state.

    Existing logs predate input acknowledgements.  A one-time epoch receipt
    marks their queued records as historical, except exact ids explicitly
    preserved during a supervised upgrade.  Later starts recover normally.
    """
    if not _log_path or not os.path.isfile(_log_path):
        return 1 if _write_activity_receipt("activity_receipt_epoch", []) else 0
    preserve = {str(value) for value in preserve_update_ids}
    queued = []
    epoch_exists = False
    try:
        with _log_lock:
            with open(_log_path, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("kind") == "activity_receipt_epoch":
                        epoch_exists = True
                    if record.get("queued") and record.get("update_id") is not None:
                        queued.append(str(record["update_id"]))
    except OSError:
        return 0
    if epoch_exists:
        return 1
    baseline = [value for value in dict.fromkeys(queued)
                if value not in preserve]
    return 1 if _write_activity_receipt(
        "activity_receipt_epoch", baseline) else 0


def _recover_pending_activity():
    """Rebuild logged, unacknowledged ordinary input after process restart."""
    if not _log_path or not os.path.isfile(_log_path):
        return 0
    queued = {}
    acknowledged = set()
    epoch_exists = False
    try:
        with _log_lock:
            with open(_log_path, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    kind = record.get("kind")
                    if kind in ("activity_receipt_epoch", "activity_ack"):
                        if kind == "activity_receipt_epoch":
                            epoch_exists = True
                        acknowledged.update(
                            str(value) for value in record.get(
                                "update_ids", []))
                        continue
                    update_id = record.get("update_id")
                    if (record.get("queued") and record.get("allowed")
                            and update_id is not None and record.get("raw")):
                        queued[str(update_id)] = record
    except OSError:
        return 0
    if not epoch_exists:
        print("[telegram] activity recovery awaits legacy epoch receipt")
        return 0
    recovered = 0
    def order(item):
        try:
            return int(item[0])
        except (TypeError, ValueError):
            return 0
    for update_id, record in sorted(queued.items(), key=order):
        if update_id in acknowledged:
            continue
        update = record.get("raw") or {}
        kind, message = _extract_message(update)
        if not message:
            continue
        message = dict(message)
        text = record.get("text") or record.get("caption")
        if text is not None:
            message["text"] = text
            message.pop("caption", None)
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        _set_last(
            chat.get("id", record.get("chat_id", "")),
            _format_message(update, kind, message, text or ""),
            bool(sender.get("is_bot", record.get("from_is_bot", False))),
            _tier_for_sender(sender),
            update_id=update_id,
        )
        recovered += 1
    return recovered


def getActivityBatch():
    """Prepare one stable chronological activity batch — the
    context-centric turn: accumulated activity is OBSERVATION for the next
    cognitive tick, not a per-message to-do list. Each entry keeps its own
    metadata header (sender, chat, ids, from_is_bot) as already formatted.
    Reply routing and energy accounting follow the NEWEST HUMAN message when
    present (existing tier machinery, unchanged), else the newest message.

    Repeated evaluation returns the same prepared batch.  Only
    ``ackActivityBatch`` may remove it after the corresponding turn is in
    durable history."""
    global _reply_chat_id, _last_message_is_human, _last_from_bot
    global _last_arm_tier, _context_frontier_bytes
    global _current_batch_update_ids, _context_activity_epoch
    global _prepared_messages
    with _msg_lock:
        if _prepared_messages:
            items = _prepared_messages[:]
        elif not _pending_messages:
            _last_message_is_human = False
            _last_arm_tier = "full"
            _current_batch_update_ids = set()
            _context_activity_epoch = _activity_epoch
            with _log_lock:
                _context_frontier_bytes = (
                    os.path.getsize(_log_path)
                    if _log_path and os.path.exists(_log_path) else 0)
            return ""
        else:
            _prepared_messages = _pending_messages[:]
            items = _prepared_messages[:]
            _context_activity_epoch = _activity_epoch
            # This is the causal frontier for the turn.  The poller records
            # and queues an inbound message while holding the same lock, so
            # later activity cannot leak into the prepared prompt.
            with _log_lock:
                _context_frontier_bytes = (
                    os.path.getsize(_log_path)
                    if _log_path and os.path.exists(_log_path) else 0)
            _current_batch_update_ids = set()
    texts = []
    newest_human = None
    newest = None
    runtime = None
    for item in items:
        chat, msg, from_bot, tier, update_id = _pending_fields(item)
        texts.append(str(msg))
        if update_id is not None:
            _current_batch_update_ids.add(str(update_id))
        if chat == RUNTIME_CHAT:
            runtime = (chat, from_bot, tier)
            continue
        newest = (chat, from_bot, tier)
        if not from_bot:
            newest_human = (chat, from_bot, tier)
    pick = newest_human or newest
    if pick is not None:
        _reply_chat_id = str(pick[0])
        _last_from_bot = bool(pick[1])
        _last_message_is_human = not _last_from_bot
        _last_arm_tier = str(pick[2]) if _last_message_is_human else "full"
    else:
        # Only runtime events: replies keep their target, and the agent's
        # own finished work arms a burst so it can act on the result.
        _last_from_bot = False
        _last_message_is_human = True
        _last_arm_tier = runtime[2]
    return "\n".join(texts)


def ackActivityBatch():
    """Acknowledge exactly the prepared prefix after durable history.

    The receipt is fsynced before the in-memory prefix is removed.  A receipt
    failure leaves the full batch pending, preferring retry over silent loss.
    """
    global _prepared_messages, _current_batch_update_ids
    with _msg_lock:
        if not _prepared_messages:
            return 1
        count = len(_prepared_messages)
        if _pending_messages[:count] != _prepared_messages:
            print("[telegram] activity acknowledgement prefix mismatch")
            return 0
        update_ids = []
        for item in _prepared_messages:
            update_id = _pending_fields(item)[4]
            if update_id is not None:
                update_ids.append(str(update_id))
        import input_ack
        if not input_ack.committed(update_ids):
            return 0
        if update_ids and not _write_activity_receipt(
                "activity_ack", update_ids):
            return 0
        input_ack.processed_update_ids(update_ids)
        del _pending_messages[:count]
        _prepared_messages = []
        _current_batch_update_ids = set()
        return 1


def pendingActivityCount():
    """Number of ordinary inputs not yet history-acknowledged."""
    if metta_client():
        return int(metta_value("pending_count", 0) or 0)
    with _msg_lock:
        return len(_pending_messages)


def preparedActivityCount():
    """Number of those inputs frozen into the current stable observation."""
    if metta_client():
        return int(metta_value("prepared_count", 0) or 0)
    with _msg_lock:
        return len(_prepared_messages)


def lastMessageIsHuman():
    # Janus maps Python bools as grounded objects in PeTTa; use a tiny numeric
    # flag so MeTTa can convert it to a native boolean with ==.
    return 1 if _last_message_is_human else 0


def lastMessageArmLoops():
    """Loop count the last human message arms: the sender's energy tier."""
    tier = _last_arm_tier if _last_arm_tier in ENERGY_TIERS else "full"
    return _tier_loops(tier)


def lastMessageFromBot():
    return _last_from_bot


def _load_offset():
    global _offset
    if not _offset_path:
        return
    try:
        with open(_offset_path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            _offset = int(value)
    except FileNotFoundError:
        return
    except Exception as exc:
        print("[telegram] offset load error:", exc)


def _save_offset():
    if not _offset_path or _offset is None:
        return
    try:
        parent = os.path.dirname(_offset_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = _offset_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(_offset))
        os.replace(tmp, _offset_path)
    except Exception as exc:
        print("[telegram] offset save error:", exc)


def recent_activity(n=10, chat=None, snippet_chars=110):
    """Short-term memory: the last n allowed messages from the local update
    log as '[hh:mm chat sender: text…]', oldest→newest. Always in context, so
    the agent keeps group-dynamics awareness (who spoke, when, was I last?)
    across turns — bounded last-k-messages continuity, kept
    sweetly simple. Never raises (janus boundary)."""
    try:
        n = max(1, int(n))
        if not _log_path or not os.path.exists(_log_path):
            return ""
        if chat is None:
            # ambient per-turn STM: cheap tail read
            with open(_log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 65536))
                lines = f.read().decode("utf-8", "replace").splitlines()
        else:
            # explicit per-chat recall: stream the whole log (reaches back to
            # the beginning of local logging)
            with open(_log_path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        entries = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("allowed") or not rec.get("text"):
                continue
            if chat is not None:
                needle = str(chat).lower()
                if (needle not in str(rec.get("chat_title", "")).lower()
                        and needle != str(rec.get("chat_id", ""))):
                    continue
            ts = str(rec.get("received_at", ""))[11:16]  # ISO -> hh:mm
            snippet = " ".join(str(rec.get("text", "")).split())
            if len(snippet) > snippet_chars:
                snippet = snippet[:snippet_chars] + "…"
            entries.append(f"[{ts} {rec.get('chat_title', '?')} "
                           f"{rec.get('from', '?')}: {snippet}]")
        return " ".join(entries[-n:])
    except Exception as exc:
        print("[telegram] recent_activity error:", exc)
        return ""


_CONTROL_REPLY_PREFIXES = (
    "activity:", "runtime-health:", "active engine:", "active model:",
    "active mode:", "arming energy", "model set to ", "mode set to ",
    "engine set to ", "tap to switch:", "deleted message ",
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
                        max_event_chars=6000):
    """Render a causal, role-preserving window from the append-only ledger.

    The update log is the source of truth.  ``getActivityBatch`` fixes a byte
    frontier before the model prompt is assembled and records the update ids
    prepared for this turn.  Records beyond that frontier are future input;
    prepared records remain stable as NEW_ACTIVITY until history-backed
    acknowledgement.
    Deterministic slash-command chatter remains auditable in the ledger but is
    not conversation.
    """
    try:
        limit = max(1000, int(max_chars))
        event_limit = max(1, int(max_events))
        item_limit = max(200, int(max_event_chars))
        if not _log_path or not os.path.exists(_log_path):
            return route_view() + "\n\n(no prior conversation events)"
        with _msg_lock:
            frontier = _context_frontier_bytes
            excluded = set(_current_batch_update_ids)
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
    """Interrupt a timed rest and retain truthful provenance for its result."""
    global _wake_reason
    if metta_client():
        _metta_queue({"wake_only": str(reason)})
    with _wake_lock:
        _wake_reason = str(reason)
        _wake_event.set()


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


def _models_keyboard():
    """Inline keyboard of switchable models: tap a button to switch (handled
    by _handle_callback_query, zero LLM involvement)."""
    import synthetic_llm
    current = synthetic_llm.current_model()
    rows = []
    for m in synthetic_llm.model_ids():
        if len(("model:" + m).encode("utf-8")) > 64:  # telegram limit
            continue
        availability = synthetic_llm.model_in_observed_catalog(m)
        mark = "● " if m == current else ("⚠ " if availability is False else "")
        label = mark + m
        rows.append([{"text": label, "callback_data": "model:" + m}])
    return {"inline_keyboard": rows}


def _modes_keyboard():
    """Inline keyboard for the persistent cognitive-loop policy."""
    import loop_modes
    current = loop_modes.current_mode()
    rows = []
    for name in loop_modes.MODES:
        label = ("● " if name == current else "") + name
        rows.append([{"text": label, "callback_data": "mode:" + name}])
    return {"inline_keyboard": rows}


def _fuels_keyboard():
    """Inline keyboard for the persisted fuel discipline."""
    import fuel_modes
    current = fuel_modes.current_fuel()
    rows = []
    for name in fuel_modes.FUELS:
        label = ("● " if name == current else "") + name
        rows.append([{"text": label, "callback_data": "fuel:" + name}])
    return {"inline_keyboard": rows}


def _engines_keyboard():
    """Inline keyboard for the persisted evaluator selection."""
    import engine_modes
    active = engine_modes.active_engine()
    selected = engine_modes.selected_engine()
    rows = []
    for name in engine_modes.ENGINES:
        mark = "● " if name == active else ("◌ " if name == selected else "")
        unavailable = " (unavailable)" if not engine_modes.engine_available(name) else ""
        rows.append([{
            "text": mark + name + unavailable,
            "callback_data": "engine:" + name,
        }])
    return {"inline_keyboard": rows}


def _handle_callback_query(cq):
    """A tapped, namespaced model button (callback_data 'model:<id>') from an
    operator: switch, acknowledge, update the menu message."""
    data = str(cq.get("data") or "")
    message = cq.get("message") or {}
    chat = message.get("chat") or {}
    try:
        if data.startswith("energy:"):
            if not _chat_is_allowed(chat):
                return "callback_disallowed_chat"
            if not _is_operator(cq.get("from")):
                requests.post(_api("answerCallbackQuery"), json={
                    "callback_query_id": cq.get("id"),
                    "text": "energy tiers are operator-only",
                }, timeout=15)
                return "callback_not_operator"
            _, who, tier = data.split(":", 2)
            reply = energy_set(who, tier)
            requests.post(_api("answerCallbackQuery"), json={
                "callback_query_id": cq.get("id"), "text": str(reply)[:190],
            }, timeout=15)
            requests.post(_api("editMessageText"), json={
                "chat_id": chat.get("id"),
                "message_id": message.get("message_id"),
                "text": "arming energy — " + energy_view(),
                "reply_markup": _energy_keyboard(),
            }, timeout=15)
            return "callback_energy"
        if data.startswith("mode:"):
            if not _chat_is_allowed(chat):
                return "callback_disallowed_chat"
            if not _is_operator(cq.get("from")):
                requests.post(_api("answerCallbackQuery"), json={
                    "callback_query_id": cq.get("id"),
                    "text": "mode switching is operator-only",
                }, timeout=15)
                return "callback_not_operator"
            import loop_modes
            reply = loop_modes.set_mode(data[len("mode:"):])
            _request_wake("mode switch")
            requests.post(_api("answerCallbackQuery"), json={
                "callback_query_id": cq.get("id"), "text": str(reply)[:190],
            }, timeout=15)
            requests.post(_api("editMessageText"), json={
                "chat_id": chat.get("id"),
                "message_id": message.get("message_id"),
                "text": loop_modes.mode_view(),
                "reply_markup": _modes_keyboard(),
            }, timeout=15)
            return "callback_mode_switch"
        if data.startswith("fuel:"):
            if not _chat_is_allowed(chat):
                return "callback_disallowed_chat"
            if not _is_operator(cq.get("from")):
                requests.post(_api("answerCallbackQuery"), json={
                    "callback_query_id": cq.get("id"),
                    "text": "fuel switching is operator-only",
                }, timeout=15)
                return "callback_not_operator"
            import fuel_modes
            reply = fuel_modes.set_fuel(data[len("fuel:"):])
            requests.post(_api("answerCallbackQuery"), json={
                "callback_query_id": cq.get("id"), "text": str(reply)[:190],
            }, timeout=15)
            requests.post(_api("editMessageText"), json={
                "chat_id": chat.get("id"),
                "message_id": message.get("message_id"),
                "text": fuel_modes.fuels_view(),
                "reply_markup": _fuels_keyboard(),
            }, timeout=15)
            return "callback_fuel_switch"
        if data.startswith("engine:"):
            if not _chat_is_allowed(chat):
                return "callback_disallowed_chat"
            if not _is_operator(cq.get("from")):
                requests.post(_api("answerCallbackQuery"), json={
                    "callback_query_id": cq.get("id"),
                    "text": "engine switching is operator-only",
                }, timeout=15)
                return "callback_not_operator"
            import engine_modes
            reply = engine_modes.set_engine(data[len("engine:"):])
            requests.post(_api("answerCallbackQuery"), json={
                "callback_query_id": cq.get("id"), "text": str(reply)[:190],
            }, timeout=15)
            requests.post(_api("editMessageText"), json={
                "chat_id": chat.get("id"),
                "message_id": message.get("message_id"),
                "text": engine_modes.engines_view(),
                "reply_markup": _engines_keyboard(),
            }, timeout=15)
            if (not str(reply).startswith("engine-set failed:")
                    and engine_modes.selected_engine()
                    != engine_modes.active_engine()):
                engine_modes.request_recycle()
                _request_wake("engine switch")
            return "callback_engine_switch"
        if not data.startswith("model:"):
            return "callback_ignored"
        if not _chat_is_allowed(chat):
            return "callback_disallowed_chat"
        if not _is_operator(cq.get("from")):
            requests.post(_api("answerCallbackQuery"), json={
                "callback_query_id": cq.get("id"),
                "text": "model switching is operator-only",
            }, timeout=15)
            return "callback_not_operator"
        # Stop Telegram's spinner FIRST: the client shows a loading state until
        # the callback is answered, so acknowledging before doing the work is
        # the difference between "instant" and "stuck" from the operator's side.
        started = time.time()
        requests.post(_api("answerCallbackQuery"), json={
            "callback_query_id": cq.get("id"), "text": "switching…",
        }, timeout=15)
        import synthetic_llm
        reply = synthetic_llm.set_model(data[len("model:"):])
        print("[telegram] model switch handled in %.2fs" % (time.time() - started))
        requests.post(_api("editMessageText"), json={
            "chat_id": chat.get("id"),
            "message_id": message.get("message_id"),
            "text": str(reply)[:3800],
            "reply_markup": _models_keyboard(),
        }, timeout=15)
        return "callback_model_switch"
    except Exception as exc:
        print("[telegram] callback error:", exc)
        return "callback_error"


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


def _my_username():
    """This bot's @username via getMe, cached; "" while unknown."""
    global _bot_username
    configured = _known_username()
    if configured:
        _bot_username = configured
        return configured
    if _bot_username is None:
        try:
            r = requests.get(_api("getMe"), timeout=15).json()
            name = str((r.get("result") or {}).get("username") or "")
            if name:
                _bot_username = name
            return name
        except Exception:
            return ""
    return _bot_username


def _maybe_rest_notice(chat, sender):
    """Tell the operator the agent is resting, and for how long, so they can
    decide whether to wait or /wake. Sent once per rest, not per message."""
    global _rest_notice_sent
    resting, left = rest_status()
    if not resting or _rest_notice_sent or not _is_operator(sender):
        return
    _rest_notice_sent = True
    wake_at = time.strftime("%H:%M", time.localtime(time.time() + left))
    mins, secs = left // 60, left % 60
    span = ("%dm%02ds" % (mins, secs)) if mins else ("%ds" % secs)
    try:
        send_message_to_chat(
            str(chat.get("id", "")),
            "\U0001F4A4 resting — wakes in %s (at %s). Your message is queued "
            "and will be read then; send /wake to end the rest now."
            % (span, wake_at))
    except Exception as exc:
        print("[telegram] rest notice failed:", exc)


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
        if cmd == "/engines":
            import engine_modes
            requests.post(_api("sendMessage"), json={
                "chat_id": chat.get("id"),
                "text": engine_modes.engines_view(),
                "reply_markup": _engines_keyboard(),
            }, timeout=15)
            return "slash_command:/engines"
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
    if cmd == "/engine":
        import engine_modes
        if not arg:
            return (engine_modes.engine_view()
                    + " — /engine <name> to switch, /engines to list")[:3800]
        reply = engine_modes.set_engine(arg)
        if (not str(reply).startswith("engine-set failed:")
                and engine_modes.selected_engine()
                != engine_modes.active_engine()):
            engine_modes.request_recycle()
            _request_wake("engine switch")
        return str(reply)[:3800]
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


def _process_update(update):
    """Route one received update. Both transports call this for every update
    in receipt order; the caller advances its own position afterwards."""
    update_id = int(update["update_id"])
    if "callback_query" in update:
        cq = update["callback_query"] or {}
        threading.Thread(target=_handle_callback_query,
                         args=(cq,), daemon=True).start()
        cb_note = "callback_dispatched"
        cb_msg = cq.get("message") or {}
        # log the real allowlist verdict; the outcome is cb_note
        # (a False here on a successful switch is a lying record)
        _append_update_log(update, "callback_query", cb_msg,
                           _chat_is_allowed(cb_msg.get("chat") or {}),
                           False, cb_note)
        return
    kind, message = _extract_message(update)
    note = ""
    allowed = False
    queued = False
    text = None
    if not message:
        note = "unsupported_update"
    else:
        chat = message.get("chat") or {}
        allowed = _chat_is_allowed(chat)
        if allowed:
            _chat_titles[str(chat.get("id", ""))] = \
                _display_chat(chat)
        text = message.get("text") or message.get("caption")
        if not allowed:
            note = "disallowed_chat"
        else:
            command_note = _peek_slash_command(
                text, message.get("from")) if text else None
            if command_note:
                # Controls are classified before any attachment
                # transport. A file captioned `/activity` remains a
                # control request, not a 120-second poll blockage.
                note = command_note
                _dispatch_slash_command(
                    chat, message.get("from"), text)
            elif _attachment_info(message):
                sender = message.get("from") or {}
                if not _wake_for_operator_message(sender):
                    _maybe_rest_notice(chat, sender)
                note = "attachment_dispatched"
                _enqueue_attachment(
                    update, kind, message, text or "")
            elif not text:
                note = "no_text_or_caption"
            else:
                # Only ordinary messages are queued. Slash
                # commands and attachments have independent lanes.
                sender = message.get("from")
                if not _wake_for_operator_message(sender):
                    _maybe_rest_notice(chat, sender)
                queued = True
    # Ordinary inbound activity is logged and queued under the
    # same lock used to freeze a model turn's causal frontier.
    # Without this pairing a newly logged message could leak into
    # one prompt before it had actually been drained for that
    # turn, then appear again as NEW_ACTIVITY on the next turn.
    if queued:
        import attention
        attention.ingress("update:" + str(update_id), "operator" if _is_operator(message.get("from")) else "peer", update)
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        with _msg_lock:
            logged = _append_update_log(
                update, kind, message, allowed, queued, note)
            _set_last(
                chat.get("id", ""),
                _format_message(update, kind, message, text),
                bool(sender.get("is_bot")),
                _tier_for_sender(sender),
                update_id=update_id,
            )
    else:
        logged = _append_update_log(
            update, kind, message, allowed, queued, note)
    if not logged:
        print("[telegram] continuing after update log failure")


def _poll_loop():
    global _offset
    while _running:
        try:
            try:
                poll_timeout = min(30, max(1, int(os.environ.get(
                    "METTACLAW_TELEGRAM_POLL_TIMEOUT", "20"))))
            except ValueError:
                poll_timeout = 20
            try:
                request_timeout = max(poll_timeout + 2, int(os.environ.get(
                    "METTACLAW_TELEGRAM_REQUEST_TIMEOUT", "30")))
            except ValueError:
                request_timeout = max(poll_timeout + 2, 30)
            params = {"timeout": poll_timeout}
            if _offset is not None:
                params["offset"] = _offset
            resp = requests.get(
                _api("getUpdates"), params=params, timeout=request_timeout)
            resp.raise_for_status()
            data = resp.json()
            _health_update(poll_status="ok", last_poll_ok_at=time.time())
            for update in data.get("result", []):
                _process_update(update)
                _offset = int(update["update_id"]) + 1
                _save_offset()
        except Exception as exc:
            _health_update(force=True, poll_status="error",
                           last_poll_error_at=time.time(),
                           poll_error_type=type(exc).__name__)
            # Request exceptions can include the token-bearing API URL. Never
            # echo their full text into a persistent service journal.
            print("[telegram] poll error:", type(exc).__name__)
            time.sleep(5)


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


def _durable_observe(observation):
    import durable_telegram
    value = durable_telegram.parse(observation)
    if value[0] == "input" and len(value) == 5 and isinstance(value[4], dict):
        _process_update(value[4])
    elif value[0] == "delivery" and len(value) == 5:
        _durable_record(value[1], value[2], value[4])
    elif value[0] == "rejected" and len(value) == 3:
        with _durable_lock:
            known = _durable_pending.pop(value[1], None) is not None
        if known:
            _durable_outbox.done(value[1])
        _durable_receipts.record(value[1], -1, value[2])
    elif value[0] == "released" and len(value) == 4:
        # An operator released the lane: the uncertain action stays
        # uncertain, the rest of its batch was abandoned, later batches go on.
        with _durable_lock:
            entry = _durable_pending.pop(value[2], None)
        if entry is not None:
            _durable_outbox.done(value[2])
        _durable_outbox.release(value[1])
        print("[telegram] operator released chat lane", value[1])
    elif value[0] == "input-too-large":
        # The service journal keeps the update; only its size is known here.
        print("[telegram] durable delivery too large to read:", value[1:4])
    else:
        print("[telegram] unknown durable observation:", value[0])


_durable_commands_lock = threading.Lock()
_durable_command_running = set()
_durable_command_answers = {}   # task -> text, computed at most once


def _durable_command_of(observation):
    try:
        value = json.loads(observation)
    except ValueError:
        return None
    if (isinstance(value, list) and len(value) == 5 and value[0] == "command"
            and all(isinstance(v, str) for v in value)):
        return value
    return None


def _durable_command(task, value):
    """Answer one operator command the channel service asked about, on its
    own thread: never the cognition thread, never the delivery loop. The
    service waits for this only until its deadline; a later answer edits the
    service's notice. A command runs at most once in this process, however
    often its answer has to be resent."""
    import durable_telegram
    _, key, name, args, lane = value
    with _durable_commands_lock:
        text = _durable_command_answers.get(task)
    if text is None:
        try:
            text = _command_answer(name, args, lane.split(".", 1)[0])
        except Exception as exc:
            print("[telegram] operator command failed:", name, type(exc).__name__)
            text = "%s failed: %s" % (name, type(exc).__name__)
        if text is None:
            text = "Waking." if name == "/wake" else "Done."
        with _durable_commands_lock:
            _durable_command_answers[task] = text
            while len(_durable_command_answers) > 256:
                _durable_command_answers.pop(next(iter(_durable_command_answers)))
    try:
        for delay in (0, 0.5, 1, 2, 4):
            time.sleep(delay)
            try:
                _durable.answer(task, text)
                return
            except (OSError, durable_telegram.ChannelError) as exc:
                failure = type(exc).__name__
        print("[telegram] operator command answer not recorded yet:", name, failure)
    finally:
        with _durable_commands_lock:
            _durable_command_running.discard(task)


def _durable_loop():
    """Handle deliveries and receipts in order; acknowledge each afterwards,
    exactly where the Bot API poller advances its offset. An operator command
    is answered by its own thread instead.

    If the service stops after a task was handled here but before the
    acknowledgment committed, it offers the same task again: that task is
    acknowledged without being handled twice."""
    handled = []
    while _running:
        try:
            tasks = _durable.pending(64)
            _health_update(poll_status="ok", last_poll_ok_at=time.time())
            for task, observation in tasks:
                command = _durable_command_of(observation)
                if command is not None:
                    with _durable_commands_lock:
                        start = task not in _durable_command_running
                        _durable_command_running.add(task)
                    if start:
                        threading.Thread(target=_durable_command, args=(task, command),
                                         name="telegram-command-" + command[2].lstrip("/").replace("_", "-"),
                                         daemon=True).start()
                    continue
                if task not in handled:
                    _durable_observe(observation)
                    handled.append(task)
                    del handled[:-4096]
                _durable.acknowledge(task)
            if not tasks:
                time.sleep(0.25)
        except Exception as exc:
            _health_update(poll_status="error", last_poll_error_at=time.time(),
                           poll_error_type=type(exc).__name__)
            print("[telegram] durable channel error:", type(exc).__name__)
            time.sleep(2)


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
    if results == "queued" and _durable_outbox.is_held(durable_telegram.lane_of(key)):
        return ("send held: an earlier message to this chat may not have "
                "arrived; this one waits, in order, for an operator decision "
                "(%s)" % key)
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
            print("[telegram] chat lane %s held: delivery of %s is uncertain (%s)"
                  % (durable_telegram.lane_of(key), key, result[1]))
            return ("send uncertain (%s): Telegram may or may not have it; "
                    "this chat holds later messages until an operator "
                    "resolves it (%s)" % (result[1], key))
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
        _health_update(force=True, menu_status="ok",
                       menu_registered_at=time.time())
    except Exception as exc:
        # Menu discoverability is not allowed to take down message polling;
        # typed slash commands continue to work and the health watch records
        # registration failures from the service journal.
        _health_update(force=True, menu_status="error",
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
_metta_lock = threading.Lock()
_metta_events = []
_handover_offered = set()


def metta_client():
    return os.environ.get("METTACLAW_TELEGRAM_CLIENT", "python") == "metta"


def _metta_queue(item):
    with _metta_lock:
        _metta_events.append(item)


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
    if len(values) > 256:
        raise ValueError("handover update batch exceeds its bound")
    result = []
    for value in values:
        if type(value) not in (int, str):
            raise ValueError("invalid handover update ID")
        text = str(value)
        if (not text or len(text) > 19 or not text.isascii() or not text.isdigit()
                or str(int(text)) != text or not 0 <= int(text) < 2**63):
            raise ValueError("invalid handover update ID")
        if text not in result:
            result.append(text)
    return result


def handover_ack(ids):
    values = handover_ids(ids)
    import mode_channel
    # A failed durable acknowledgement must leave the notice unprocessed.
    if not mode_channel.acknowledge_handover(values):
        return 0
    import input_ack
    input_ack.processed_update_ids(values)
    return 1


_METTA_SHARED = ("pending_count", "prepared_count", "sleep_until", "last_poll",
                 "active_engine")


def metta_value(name, default=None):
    """What the MeTTa client published: here if it runs in this process, else
    from the health file it keeps, as the command responder reads it."""
    if name in _metta:
        return _metta[name]
    try:
        with open(_health_path(), encoding="utf-8") as stream:
            return (json.load(stream).get("metta") or {}).get(name, default)
    except (OSError, ValueError):
        return default


def metta_publish(name, value):
    global _context_frontier_bytes, _current_batch_update_ids, _sleep_until
    global _reply_chat_id, _activity_epoch, _context_activity_epoch
    name = str(name)
    _metta[name] = value
    if name in _METTA_SHARED:
        with _health_lock:
            shared = dict(_health_state.get("metta") or {})
            shared[name] = value
        _health_update(force=name in ("sleep_until", "active_engine"), metta=shared)
    if name == "context_frontier":
        _context_frontier_bytes = int(value)
    elif name == "batch_update_ids":
        _current_batch_update_ids = {v for v in str(value).split(",") if v}
    elif name == "batch_tasks":
        pass
    elif name == "sleep_until":
        _sleep_until = float(value)
        if _sleep_until:
            _health_update(force=True, loop_status="waiting",
                           waiting_since=time.time(), waiting_until=_sleep_until)
        else:
            _health_update(force=True, loop_status="awake", waiting_until=0.0,
                           last_wait_completed_at=time.time())
    elif name == "reply_chat":
        _reply_chat_id = str(value)
    elif name == "activity_epoch":
        with _msg_lock:
            _activity_epoch = int(value)
    elif name == "context_epoch":
        with _msg_lock:
            _context_activity_epoch = int(value)
    elif name == "last_poll":
        _health_update(poll_status="ok", last_poll_ok_at=float(value))
    elif name == "title":
        chat, _, title = str(value).partition(":")
        if chat:
            _chat_titles[chat] = title
    return 1


def poll_age_seconds(now=None):
    last = float(metta_value("last_poll", 0) or _health_state.get("last_poll_ok_at", 0) or 0)
    if not last:
        return None
    return max(0.0, float(now if now is not None else time.time()) - last)


def _metta_unread_chats():
    """Chats with input the current turn has not read: messages still pending
    in the channel service that are not in the prepared batch. None when the
    service cannot be asked, which withholds the send."""
    import durable_telegram
    path = os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET", "")
    if not path:
        return None
    prepared = set(_current_batch_update_ids)
    try:
        tasks = durable_telegram.Channel(path, os.devnull).pending(256)
    except (OSError, durable_telegram.ChannelError):
        return None
    import attention
    attention.observe_pending(tasks)
    unread = set()
    for _, observation in tasks:
        try:
            value = json.loads(observation)
        except ValueError:
            continue
        if value and value[0] == "input" and len(value) == 5:
            update = value[4] if isinstance(value[4], dict) else {}
            if str(update.get("update_id", "")) not in prepared:
                unread.add(str(value[1]).split(".", 1)[0])
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


def metta_resolve_chat(who):
    return _resolve_known_chat_id(who)


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
    global _running, _thread, _token, _allowed_chat_ids, _allow_private_chats
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
    _allow_private_chats = (
        os.environ.get("METTACLAW_TELEGRAM_ALLOW_PRIVATE", "1").lower()
        not in {"0", "false", "no"}
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
    if _thread and _thread.is_alive():
        return _thread
    _transport = os.environ.get("METTACLAW_TELEGRAM_TRANSPORT", "bot-api")
    if _transport == "durable":
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
    elif _transport != "bot-api":
        print("[telegram] disabled: unknown METTACLAW_TELEGRAM_TRANSPORT")
        return None
    if metta_client():
        if _transport != "durable":
            print("[telegram] disabled: the MeTTa client needs the durable transport")
            return None
        _running = True
        _health_update(force=True, running=True, started_at=time.time(),
                       client="metta", menu_status="pending", poll_status="starting")
        threading.Thread(target=_register_menu_commands, daemon=True).start()
        _recover_pending_attachments()
        return "metta"
    _load_offset()
    initialize_activity_receipts()
    recovered_activity = _recover_pending_activity()
    _recover_pending_attachments()
    # Catalog and quota are observations, never startup or control gates.
    # Refreshing them here only improves the next locally rendered response.
    try:
        import synthetic_llm
        synthetic_llm.refresh_provider_observations_async()
    except Exception as exc:
        print("[telegram] provider observation refresh failed:",
              type(exc).__name__)
    _running = True
    _health_update(force=True, running=True, started_at=time.time(),
                   recovered_activity_count=recovered_activity,
                   menu_status="pending", poll_status="starting")
    threading.Thread(target=_register_menu_commands, daemon=True).start()
    _thread = threading.Thread(
        target=_durable_loop if _transport == "durable" else _poll_loop,
        daemon=True)
    _thread.start()
    return _thread


def stop_telegram():
    global _running
    _running = False
    _health_update(force=True, running=False, stopped_at=time.time())


def _reply_target(chat_id=""):
    if chat_id:
        return str(chat_id)
    with _msg_lock:
        # A plain `(send ...)` means "send to the primary operator", not
        # "send wherever the last cross-chat observation happened to come
        # from". Other audiences remain available through the explicit
        # addressed send. Deployments without a configured primary retain the
        # legacy reply behavior.
        return _primary_chat_id or _reply_chat_id or _last_chat_id


def route_view():
    """Project the actual default-send rule into model context."""
    with _msg_lock:
        if _primary_chat_id:
            title = _chat_titles.get(_primary_chat_id, "primary operator")
            return (
                "TELEGRAM ROUTE: plain (send ...) is bound to %s chat_id=%s. "
                "For every other audience use (send-telegram-chat chat_id "
                '"message"). Reading another chat cannot change this route.'
                % (title, _primary_chat_id)
            )
        current = _reply_chat_id or _last_chat_id
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
    global _effect_turn, _effect_activity_epoch, _effect_attention_revision
    turn = str(turn)
    # The context frontier was fixed atomically with the activity drain. A
    # message received after that drain must not be laundered into this turn by
    # capturing the newer live epoch here.
    with _msg_lock:
        context_epoch = _context_activity_epoch
    import attention
    if attention.enabled() and metta_client():
        _metta_unread_chats()
    with _effect_lock:
        if turn != _effect_turn:
            _effect_turn = turn
            _effect_activity_epoch = context_epoch
            _effect_sends.clear()
            _effect_attention_revision = attention.revision()
        stimulus_frontier.publish(
            turn, _effect_activity_epoch, _activity_epoch
        )
    return turn


# Conversation effects, by how they name their chat: an explicit first
# argument, or the default reply target of a plain send.
_CHAT_ARGUMENT_EFFECTS = frozenset({
    "send-telegram-chat", "delete-my-recent", "delete-message-exact",
})
_REPLY_TARGET_EFFECTS = frozenset({"send", "send-file", "send-image"})


def effect_command_unaffected(turn, command):
    """Whether input newer than this turn's frontier leaves command as chosen.

    The model has not read that input.  A message into the same chat can
    change what should be said there, so a conversation effect into it is
    withheld, and a rest would sleep through any of it, so a rest waits too.
    Input in one chat never withholds a send to another, and other commands
    run as chosen.  The operator latch still stops everything.
    """
    turn = str(turn)
    with _effect_lock:
        registered = _effect_turn == turn and _effect_activity_epoch is not None
    if not registered:
        return 0
    import lifecycle
    if not lifecycle.cognition_enabled():
        return 0
    if not isinstance(command, (list, tuple)) or not command:
        return 1
    if metta_client():
        unread = _metta_unread_chats()
        if unread is None:
            return 0
    else:
      with _msg_lock:
        unread = {str(item[0])
                  for item in _pending_messages[len(_prepared_messages):]}
    import attention
    if attention.enabled() and str(command[0]) != "rest":
        return int(attention.revision() == _effect_attention_revision)
    head = str(command[0])
    if head == "rest":
        return 0 if unread else 1
    if head in _CHAT_ARGUMENT_EFFECTS:
        target = str(command[1]) if len(command) > 1 else _reply_target("")
    elif head in _REPLY_TARGET_EFFECTS:
        target = _reply_target("")
    else:
        return 1
    return 0 if str(target) in unread else 1


def effect_turn_stimulus_free(turn):
    """Whether no ordinary inbound stimulus crossed this turn's frontier.

    This is a cooperative, between-effects guard. It does not claim to cancel
    a command already running or to roll back an external effect.
    """
    turn = str(turn)
    with _effect_lock:
        registered = _effect_turn == turn and _effect_activity_epoch is not None
    if registered:
        import lifecycle
        if not lifecycle.cognition_enabled():
            return 0
    with _msg_lock:
        live_epoch = _activity_epoch
    with _effect_lock:
        # Direct dispatcher uses outside a registered cognitive turn retain
        # their old behavior; real model turns always call begin_effect_turn.
        if _effect_turn != turn or _effect_activity_epoch is None:
            return 1
        import attention
        if attention.enabled():
            if metta_client() and _metta_unread_chats() is None:
                return 0
            return int(attention.revision() == _effect_attention_revision)
        if metta_client():
            unread = _metta_unread_chats()
            return 1 if unread == set() else 0
        return 1 if live_epoch == _effect_activity_epoch else 0


def send_effect_message(text, chat_id=""):
    """Attempt a model-authored Telegram send at most once in this turn."""
    target = _reply_target(chat_id)
    body = str(text).replace("\\n", "\n")
    key = (str(target), body)
    with _effect_lock:
        if key in _effect_sends:
            return "duplicate send suppressed"
        _effect_sends.add(key)
    return send_message(body, chat_id=target)


def send_effect_message_to_chat(chat_id, text):
    return send_effect_message(text, chat_id=chat_id)


def sleep_until_message(seconds):
    """Rest until the deadline or until the operator speaks or says /wake.

    A blocking sleep makes the agent unreachable for its whole duration and
    silent about it. This waits in short slices. Other senders queue without
    cutting rest short; an authenticated operator message, or a configured
    wake peer's message, wakes the agent and is consumed normally by the next
    turn."""
    global _sleep_until, _rest_notice_sent, _wake_reason
    try:
        seconds = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        seconds = 1
    deadline = time.time() + seconds
    # Clear a stale wake and publish the rest under the same lock used by wake
    # requests. A request is therefore ordered either before this rest or
    # after it; one published during rest cannot be lost by this clear.
    with _wake_lock:
        _wake_event.clear()
        _wake_reason = ""
        _rest_notice_sent = False
        _sleep_until = deadline
    import process_supervision
    process_supervision.progress("channel-wait", min(1, max(0, deadline - time.time())))
    _health_update(force=True, loop_status="waiting",
                   waiting_since=time.time(), waiting_until=deadline)
    try:
        while True:
            if helper.recycle_requested():
                return "recycle requested"
            remaining = deadline - time.time()
            if remaining <= 0:
                return "rested %ds" % seconds
            if _wake_event.wait(min(1.0, remaining)):
                with _wake_lock:
                    reason = _wake_reason or "wake request"
                return "woken by %s" % reason
            process_supervision.progress("channel-wait", min(1, max(0, deadline - time.time())))
    finally:
        with _wake_lock:
            _sleep_until = 0.0
            _wake_event.clear()
            _wake_reason = ""
        process_supervision.progress("channel-resume")
        _health_update(force=True, loop_status="awake", waiting_until=0.0,
                       last_wait_completed_at=time.time())


def rest_status():
    """(is_resting, seconds_left) as seen from outside the sleeping loop."""
    until = float(metta_value("sleep_until", 0) or 0) if metta_client() else _sleep_until
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


def _recorded_own_send(chat_id, message_id):
    """Whether the append-only ledger witnesses this exact bot send."""
    target, wanted = str(chat_id), str(message_id)
    witnessed = False
    terminal = False
    if not _log_path or not os.path.isfile(_log_path):
        return False
    try:
        with _log_lock:
            with open(_log_path, "r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if (str(record.get("chat_id")) != target
                            or str(record.get("message_id")) != wanted):
                        continue
                    if record.get("note") == "own_send":
                        witnessed = True
                    elif record.get("note") in (
                            "own_delete", "own_delete_terminal"):
                        terminal = True
    except OSError:
        return False
    return witnessed and not terminal


def delete_recorded_message(chat_id, message_id):
    """Delete one exact send only when an own-send receipt is recorded."""
    target = str(_reply_target(chat_id))
    if not _recorded_own_send(target, message_id):
        return (
            "delete refused: no current own-send receipt for message %s in "
            "chat %s; an authenticated operator may use /delete for an "
            "explicit legacy id" % (message_id, target)
        )
    return delete_message(target, message_id)


def delete_my_recent(chat_id, count=1):
    """Delete the bot's own last `count` sends to a chat, newest first, using
    the ids recorded in the outbound log. This is the "clean up after a test"
    primitive: it needs no id from the operator because sends are now logged
    with their message_id. Only the bot's own recent messages are touched."""
    target = str(_reply_target(chat_id))
    ids, deleted = [], set()
    if _log_path and os.path.isfile(_log_path):
        try:
            with _log_lock:
                with open(_log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if str(rec.get("chat_id")) != target:
                            continue
                        if (rec.get("note") == "own_send"
                                and rec.get("message_id")):
                            ids.append(rec["message_id"])
                        elif rec.get("note") in (
                                "own_delete", "own_delete_terminal"):
                            deleted.add(str(rec.get("message_id")))
        except OSError as exc:
            return "delete failed: cannot read outbound log: %s" % exc
    ids = [mid for mid in ids if str(mid) not in deleted]
    if not ids:
        return ("no recorded own-sends to chat %s — nothing to delete (only "
                "messages sent AFTER id-capture was added can be cleaned this "
                "way)" % target)
    try:
        n = min(20, max(1, int(count)))
    except (TypeError, ValueError):
        n = 1
    results = [delete_message(target, mid) for mid in reversed(ids[-n:])]
    return " | ".join(results)


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

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


def _format_message(update, kind, message, text, provenance=""):
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
        _field("provenance", provenance),
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


def _attachment_job(update, kind, message, base_text, provenance="", tier=""):
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
                "attachment_ready", provenance
            )
            _set_last(
                chat.get("id", ""),
                _format_message(update, kind, ready_message, text, provenance),
                bool(sender.get("is_bot")),
                tier or _tier_for_sender(sender),
                update_id=int(update["update_id"]),
            )
        _wake_for_operator_message(sender)
    except Exception as exc:
        print("[telegram] attachment worker error:", type(exc).__name__)


def _enqueue_attachment(update, kind, message, base_text, provenance="", tier=""):
    # Each received file begins transport immediately. One slow Telegram file
    # must not consume another file's download opportunity.
    threading.Thread(
        target=_attachment_job,
        args=(update, kind, message, base_text, provenance, tier),
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
            str(record.get("provenance") or ""),
        )
        recovered += 1
    return recovered


def _append_update_log(update, kind, message, allowed, queued, note="", provenance=""):
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
    if provenance:
        record["provenance"] = provenance
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


def _set_last(chat_id, text, from_bot=False, arm_tier="full", update_id=None):
    """Hand an input to the MeTTa client, which queues it for the next turn."""
    _metta_queue({"chat": str(chat_id), "text": str(text), "from_bot": bool(from_bot),
                  "tier": str(arm_tier), "update_id": "" if update_id is None else str(update_id)})


# Runtime events, such as a finished background job, are spooled for the
# native client, which presents them as activity.  Their pseudo chat is
# never a reply target.
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
        if _spooled(identity): return True
        _metta_queue({"chat": RUNTIME_CHAT, "text": "[runtime] " + str(text), "from_bot": False,
                      "tier": "light", "update_id": identity, "wake": str(reason)}, identity)
    return True


def pendingActivityCount():
    """Number of ordinary inputs not yet history-acknowledged."""
    return int(metta_value("pending_count", 0) or 0)


def preparedActivityCount():
    """Number of those inputs frozen into the current stable observation."""
    return int(metta_value("prepared_count", 0) or 0)


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




# ---- MeTTa client ----------------------------------------------------------
# With METTACLAW_TELEGRAM_CLIENT=metta, channels/telegram.metta is Lila's
# Telegram client and this module starts no thread of its own. It keeps what
# calls the Bot API directly (attachment downloads, uploads),
# answers the Python and Prolog code that asks about Telegram from state the
# MeTTa client publishes here, and checks the channel service itself where
# an answer must be current (the unread-input guard on sends).

_metta = {}
_client_seen = None
_metta_lock = threading.Lock()
_spool_lock = threading.Lock()
_spool_last = 0


def runtime_spool():
    """runtime-events/ beside the health record: the native client takes
    each event from here (src/runtime_events_cetta.metta)."""
    return os.path.join(os.path.dirname(_health_path()), "runtime-events")


def _event_digest(identity):
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:24]


def _metta_queue(item, identity=None):
    """Spool one runtime event for the native client: a whole file, synced
    and renamed into place, named in arrival order and by its identity's
    digest. The host notice ends the client's wait at once."""
    global _spool_last
    folder = runtime_spool()
    os.makedirs(folder, exist_ok=True)
    data = json.dumps(item, ensure_ascii=False).encode("utf-8")
    with _spool_lock:
        _spool_last = max(time.time_ns(), _spool_last + 1)
        name = "%020d-%s.json" % (_spool_last, _event_digest(
            identity if identity is not None else uuid.uuid4().hex))
    descriptor, temporary = tempfile.mkstemp(prefix=".event-", dir=folder)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, os.path.join(folder, name))
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if _host_notice is not None: _host_notice()


def _spooled(identity):
    """An event with this identity waits in the spool."""
    suffix = "-" + _event_digest(identity) + ".json"
    try:
        return any(name.endswith(suffix) for name in os.listdir(runtime_spool()))
    except OSError:
        return False


def _runtime_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"runtime:[A-Za-z0-9_.:-]{1,160}",value):
        raise ValueError("invalid runtime occurrence ID")
    return value


# What this process's MeTTa client last published (_sync takes it up from its
# file). The names stay out of the module's globals so that every read is
# fresh: other modules call reply_chat_id() and current_batch_update_ids(),
# this module reads _value. A test may assign one, and its value then stands
# for both.
_published = {"_reply_chat_id": "", "_activity_epoch": 0, "_context_activity_epoch": 0,
              "_context_frontier_bytes": None, "_current_batch_update_ids": frozenset()}


def _value(name):
    """A published value: a test's assignment if one stands, else the client's."""
    if name in globals():
        return globals()[name]
    _sync()
    return _published[name]


def reply_chat_id():
    """The reply chat the MeTTa client last published ("" before any)."""
    return _value("_reply_chat_id")


def current_batch_update_ids():
    """The update ids of the activity batch the MeTTa client last published."""
    return _value("_current_batch_update_ids")


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


def metta_attachment(update_json, kind, text, provenance="", tier=""):
    update = json.loads(update_json)
    message = update.get(kind) or {}
    _enqueue_attachment(update, kind, message, text or "", str(provenance or ""), str(tier or ""))
    return 1


def start_telegram(token="", chat_id=""):
    global _token, _allowed_chat_ids
    global _offset_path, _log_path, _primary_chat_id
    global _transport
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
    if not _token:
        print("[telegram] disabled: set METTACLAW_TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN")
        return None
    _transport = os.environ.get("METTACLAW_TELEGRAM_TRANSPORT", "bot-api")
    if _transport != "durable":
        print("[telegram] disabled: the MeTTa client needs the durable transport")
        return None
    if not os.environ.get("METTACLAW_TELEGRAM_SERVICE_SOCKET", ""):
        print("[telegram] disabled: durable transport needs "
              "METTACLAW_TELEGRAM_SERVICE_SOCKET")
        return None
    _health_update(running=True, started_at=time.time(),
                   client="metta", poll_status="starting")
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
        stimulus_frontier.publish(
            turn, _effect_activity_epoch, _value("_activity_epoch")
        )
    return turn


def rest_status():
    """(is_resting, seconds_left) as seen from outside the sleeping loop."""
    until = float(metta_value("sleep_until", 0) or 0)
    left = until - time.time()
    return (left > 0, math.ceil(left) if left > 0 else 0)


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

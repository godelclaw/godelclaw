"""What the Python code of the agent's process asks about Telegram.

channels/telegram.metta is the Telegram client, natively, with
channels/telegram_files.metta for attachments and uploads. This module keeps
the runtime events Python producers spool for it, the formatting the mode
hosts share, and the answers Python callers read from what the client
publishes (the reply chat, the current batch, the effect turn).
"""
import os
import hashlib
import json
import tempfile
import re
import threading
import time
import uuid

import stimulus_frontier

# Grandfathered producers only signal the native wait. Payload routing and
# identity handling remain at their existing boundary.
try:
    from cetta_bridge import notice as _host_notice
except ImportError:
    _host_notice = None

_msg_lock = threading.RLock()
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


# ---- MeTTa client ----------------------------------------------------------
# channels/telegram.metta is the Telegram client; this module starts no
# thread of its own. It answers the Python code that asks about Telegram from
# state the MeTTa client publishes here, and checks the channel service
# itself where an answer must be current (the unread-input guard on sends).

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
    value; another process's publication is not taken up."""
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
        if published.get("pid") != os.getpid():
            return
        frontier = published.get("context_frontier")
        _published.update(
            _reply_chat_id=str(published.get("reply_chat") or ""),
            _activity_epoch=int(published.get("activity_epoch") or 0),
            _context_activity_epoch=int(published.get("context_epoch") or 0),
            _context_frontier_bytes=None if frontier is None else int(frontier),
            _current_batch_update_ids=frozenset(v for v in str(published.get("batch_update_ids") or "").split(",") if v))


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
        return (os.environ.get("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID", "").strip()
                or _value("_reply_chat_id"))


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

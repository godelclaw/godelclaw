import threading, json
from collections import deque
import requests, websocket
import time
import uuid

_running = False
_ws = None
_ws_lock = threading.Lock()
_pending_messages = deque()
_pending_delivery_ids = deque()
_presented_delivery_ids = ()
_msg_lock = threading.Lock()
_connected = False

# ---- Mattermost config (dummy token ok) ----
MM_URL = "https://chat.singularitynet.io"
CHANNEL_ID = "8fjrmabjx7gupy7e5kjznpt5qh" #NOT AN ID JUST NAME: "mettaclaw"x
BOT_TOKEN = ""
_seen_post_ids = deque()
_seen_post_id_set = set()
_seen_post_ids_lock = threading.Lock()
_MAX_SEEN_POST_IDS = 4096

def _get_bot_user_id():
    global headers
    r = requests.get(
        f"{MM_URL}/api/v4/users/me",
        headers=_headers
    )
    return r.json()["id"]

def _set_last(msg, delivery_id=None):
    with _msg_lock:
        _pending_messages.append(str(msg))
        _pending_delivery_ids.append("mattermost:" + str(delivery_id or uuid.uuid4().hex))

def getLastMessage():
    global _presented_delivery_ids
    with _msg_lock:
        messages = " | ".join(_pending_messages)
        _presented_delivery_ids = tuple(_pending_delivery_ids)
        _pending_messages.clear()
        _pending_delivery_ids.clear()
        return messages

def prepared_input_ids_json():
    with _msg_lock:
        return json.dumps(_presented_delivery_ids)

def _remember_post_id(post_id):
    if not post_id:
        return False
    with _seen_post_ids_lock:
        if post_id in _seen_post_id_set:
            return False
        _seen_post_ids.append(post_id)
        _seen_post_id_set.add(post_id)
        while len(_seen_post_ids) > _MAX_SEEN_POST_IDS:
            _seen_post_id_set.discard(_seen_post_ids.popleft())
        return True

def _get_display_name(user_id):
    r = requests.get(
        f"{MM_URL}/api/v4/users/{user_id}",
        headers=_headers
    )
    u = r.json()

    # Mimic common Mattermost display setting
    if u.get("first_name") or u.get("last_name"):
        return f"{u.get('first_name','')} {u.get('last_name','')}".strip()

    return u["username"]

def _ws_loop():
    global _ws, _connected, BOT_USER_ID

    ws_url = MM_URL.replace("https", "wss") + "/api/v4/websocket"
    ws = websocket.WebSocket()
    ws.connect(ws_url, header=[f"Authorization: Bearer {BOT_TOKEN}"])

    BOT_USER_ID = _get_bot_user_id()
    _ws = ws
    _connected = True

    last_ping = time.time()

    while _running:
        try:
            # send ping every 25s
            if time.time() - last_ping > 25:
                ws.ping()
                last_ping = time.time()

            ws.settimeout(1)
            event = json.loads(ws.recv())

            if event.get("event") == "posted":
                post = json.loads(event["data"]["post"])
                if post["channel_id"] != CHANNEL_ID:
                    continue
                if post["user_id"] == BOT_USER_ID:
                    continue
                if not _remember_post_id(post.get("id")):
                    continue
                name = _get_display_name(post["user_id"])
                _set_last(f"{name}: {post['message']}", post["id"])

        except websocket.WebSocketTimeoutException:
            continue
        except Exception:
            break

    ws.close()
    _connected = False

def start_mattermost(MM_URL_, CHANNEL_ID_, BOT_TOKEN_):
    global _running, MM_URL, CHANNEL_ID, BOT_TOKEN, _headers
    MM_URL = MM_URL_
    CHANNEL_ID = CHANNEL_ID_
    BOT_TOKEN = BOT_TOKEN_
    _headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    _running = True
    t = threading.Thread(target=_ws_loop, daemon=True)
    t.start()
    return t

def stop_mattermost():
    global _running
    _running = False

def send_message(text):
    text = text.replace("\\n", "\n")
    if not _connected:
        return
    requests.post(
        f"{MM_URL}/api/v4/posts",
        headers=_headers,
        json={"channel_id": CHANNEL_ID, "message": text}
    )

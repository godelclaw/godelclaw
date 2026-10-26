"""Nostr agent mail: signed events, NIP-44 sealing, receipts and a relay client.

A message is a kind-3838 event addressed to one recipient by a `p` tag. Its
content is the NIP-44 v2 encryption, for that recipient, of a JSON record with
a subject and a body, and for team messages a type and a task. A reply names its
parent in an `e` tag, and `["ack", "request"]` asks for receipts. A receipt is a
kind-7 reaction that names the message (`e`), its author (`p`), the kind it
reacts to (`k`) and the fact it reports (`ack`: delivered or read). This is the
format of NuNet's nostr-mailbox client.

A sender is known by the key that signed, through the registry, and never by a
tag: a tag is only the signer's claim.
"""
import asyncio
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time

KIND_MAIL = 3838
KIND_RECEIPT = 7
KIND_AUTH = 22242
RECEIPT_STATES = ("delivered", "read")


def _curve():
    import coincurve
    return coincurve


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


# ── events (NIP-01) ─────────────────────────────────────────────────────────
def public_key(secret_hex):
    """The x-only public key of a secret key, in hex."""
    return _curve().PrivateKey(bytes.fromhex(secret_hex)).public_key_xonly.format().hex()


def event_id(event):
    """SHA-256 of the serialization `[0, pubkey, created_at, kind, tags, content]`."""
    data = [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]]
    return hashlib.sha256(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def sign(secret_hex, kind, tags, content, created_at=None):
    """An event signed with BIP-340 over its identifier."""
    key = _curve().PrivateKey(bytes.fromhex(secret_hex))
    event = {"pubkey": key.public_key_xonly.format().hex(),
             "created_at": int(time.time()) if created_at is None else created_at,
             "kind": kind, "tags": tags, "content": content}
    event["id"] = event_id(event)
    event["sig"] = key.sign_schnorr(bytes.fromhex(event["id"]), secrets.token_bytes(32)).hex()
    return event


def verify(event):
    """The event is well formed, its identifier hashes its fields, and its key signed it."""
    try:
        if not (_hex(event["id"], 64) and _hex(event["pubkey"], 64) and _hex(event["sig"], 128)):
            return False
        if type(event["created_at"]) is not int or type(event["kind"]) is not int:
            return False
        if not isinstance(event["content"], str) or not isinstance(event["tags"], list) or not all(
                isinstance(tag, list) and all(isinstance(item, str) for item in tag) for tag in event["tags"]):
            return False
        if event_id(event) != event["id"]:
            return False
        return _curve().PublicKeyXOnly(bytes.fromhex(event["pubkey"])).verify(
            bytes.fromhex(event["sig"]), bytes.fromhex(event["id"]))
    except Exception:  # noqa: BLE001 - any malformed event is simply not valid
        return False


def tag_values(event, name):
    """The second element of each tag with this name, in order."""
    return [tag[1] for tag in event.get("tags", []) if len(tag) >= 2 and tag[0] == name]


# ── NIP-44 v2 ───────────────────────────────────────────────────────────────
def conversation_key(secret_hex, peer_hex):
    """HKDF-extract, salted "nip44-v2", of the x coordinate of the shared point."""
    shared = _curve().PublicKey(b"\x02" + bytes.fromhex(peer_hex)).multiply(bytes.fromhex(secret_hex))
    return hmac.new(b"nip44-v2", shared.format(compressed=True)[1:], hashlib.sha256).digest()


def message_keys(conversation, nonce):
    """The ChaCha20 key and nonce and the HMAC key of one message."""
    if len(conversation) != 32 or len(nonce) != 32:
        raise ValueError("NIP-44 keys and nonces are 32 bytes")
    okm, block = b"", b""
    for counter in (1, 2, 3):
        block = hmac.new(conversation, block + nonce + bytes([counter]), hashlib.sha256).digest()
        okm += block
    return okm[:32], okm[32:44], okm[44:76]


def padded_length(length):
    """The padded size of a plaintext of this many bytes."""
    if length <= 32:
        return 32
    power = 1 << (length - 1).bit_length()
    chunk = 32 if power <= 256 else power // 8
    return chunk * ((length - 1) // chunk + 1)


def _chacha(key, nonce, data):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
    return Cipher(algorithms.ChaCha20(key, b"\x00\x00\x00\x00" + nonce), mode=None).encryptor().update(data)


def encrypt(plaintext, conversation, nonce=None):
    """The NIP-44 v2 payload of a plaintext."""
    data = plaintext.encode("utf-8")
    if not 1 <= len(data) <= 65535:
        raise ValueError("NIP-44 plaintexts are 1 to 65535 bytes")
    nonce = secrets.token_bytes(32) if nonce is None else nonce
    key, chacha_nonce, mac_key = message_keys(conversation, nonce)
    padded = len(data).to_bytes(2, "big") + data + bytes(padded_length(len(data)) - len(data))
    ciphertext = _chacha(key, chacha_nonce, padded)
    mac = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.b64encode(b"\x02" + nonce + ciphertext + mac).decode()


def decrypt(payload, conversation):
    """The plaintext of a NIP-44 v2 payload; ValueError when it does not open."""
    if not payload or payload[0] == "#":
        raise ValueError("unknown NIP-44 version")
    if not 132 <= len(payload) <= 87472:
        raise ValueError("invalid NIP-44 payload size")
    data = base64.b64decode(payload, validate=True)
    if not 99 <= len(data) <= 65603:
        raise ValueError("invalid NIP-44 data size")
    if data[0] != 2:
        raise ValueError("unknown NIP-44 version")
    nonce, ciphertext, mac = data[1:33], data[33:-32], data[-32:]
    key, chacha_nonce, mac_key = message_keys(conversation, nonce)
    if not hmac.compare_digest(hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest(), mac):
        raise ValueError("invalid NIP-44 MAC")
    padded = _chacha(key, chacha_nonce, ciphertext)
    length = int.from_bytes(padded[:2], "big")
    if length == 0 or len(padded) != 2 + padded_length(length):
        raise ValueError("invalid NIP-44 padding")
    return padded[2:2 + length].decode("utf-8")


# ── mail ────────────────────────────────────────────────────────────────────
def seal(secret_hex, recipient_hex, subject, body, kind=None, task=None, reply_to=None, ack=True,
         created_at=None, nonce=None):
    """A signed kind-3838 message to one recipient, its letter encrypted for them."""
    letter = {"subject": subject, "body": body}
    if kind:
        letter["type"] = kind
    if task:
        letter["task"] = task
    content = encrypt(json.dumps(letter, ensure_ascii=False, separators=(",", ":")),
                      conversation_key(secret_hex, recipient_hex), nonce)
    tags = [["p", recipient_hex], ["encrypted", "nip44"]]
    if reply_to:
        tags.append(["e", reply_to, "", "reply"])
    if ack:
        tags.append(["ack", "request"])
    return sign(secret_hex, KIND_MAIL, tags, content, created_at)


def open_letter(secret_hex, event):
    """The letter of a message for the holder of this key, or None.

    The holder is its recipient, or its author reading its own sent copy. Only
    the `p` tag and the encrypted content are read: a type or task comes from
    inside the ciphertext, never from a tag."""
    if event.get("kind") != KIND_MAIL or ["encrypted", "nip44"] not in event.get("tags", []):
        return None
    me, recipients = public_key(secret_hex), tag_values(event, "p")
    if event["pubkey"] == me:
        if not recipients:
            return None
        peer = recipients[0]
    elif me in recipients:
        peer = event["pubkey"]
    else:
        return None
    try:
        letter = json.loads(decrypt(event["content"], conversation_key(secret_hex, peer)))
    except Exception:  # noqa: BLE001 - a message that does not open is not kept
        return None
    if not isinstance(letter, dict) or not all(isinstance(letter.get(k), str) for k in ("subject", "body")):
        return None
    return {k: letter[k] for k in ("subject", "body", "type", "task") if isinstance(letter.get(k), str)}


def receipt(secret_hex, message, state, created_at=None):
    """A signed receipt for a message: delivered, or read."""
    if state not in RECEIPT_STATES:
        raise ValueError("a receipt reports delivered or read")
    tags = [["e", message["id"]], ["p", message["pubkey"]], ["k", str(KIND_MAIL)], ["ack", state]]
    return sign(secret_hex, KIND_RECEIPT, tags, "+", created_at)


def receipt_fact(event):
    """(message id, state) of a well-formed receipt, or None."""
    messages, states = tag_values(event, "e"), tag_values(event, "ack")
    if event.get("kind") != KIND_RECEIPT or not messages or not states or states[0] not in RECEIPT_STATES:
        return None
    return messages[0], states[0]


def load_registry(path):
    """Member names by key, from a registry file `{name: {"pubkey_hex": ...}}`.

    A key named twice keeps its first name."""
    names = {}
    for name, entry in json.loads(Path(path).read_text()).items():
        key = entry.get("pubkey_hex") if isinstance(entry, dict) else None
        if _hex(key, 64):
            names.setdefault(key, name)
    return names


def registry_relays(path):
    """The relays each member reads, by name, from the `relays` lists of a
    registry file: names of relays, as the reader's own relay list names them.
    A member whose entry lists none reads every relay and is left out."""
    reads = {}
    for name, entry in json.loads(Path(path).read_text()).items():
        relays = entry.get("relays") if isinstance(entry, dict) else None
        if isinstance(relays, list) and relays and all(isinstance(r, str) for r in relays):
            reads[name] = relays
    return reads


# ── relay client (NIP-01, NIP-42) ───────────────────────────────────────────
class Relay:
    """One connection to a relay, signed in with one key (NIP-42) before any
    request, and shared by concurrent requests: a reader task routes events and
    end-of-stored markers to their subscriptions and OK answers to the publishes
    that wait for them."""

    def __init__(self, url, secret_hex, timeout=30):
        self.url, self.secret, self.timeout = url, secret_hex, timeout
        self.socket = self.reader = None
        self.subscriptions, self.pending = {}, {}

    async def __aenter__(self):
        from websockets.asyncio.client import connect
        self.socket = await connect(self.url, open_timeout=self.timeout, max_size=1 << 22)
        try:
            await self._sign_in()
        except BaseException:
            await self.socket.close()
            raise
        self.reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *error):
        if self.reader:
            self.reader.cancel()
        await self.socket.close()

    async def _receive(self):
        message = json.loads(await asyncio.wait_for(self.socket.recv(), self.timeout))
        if not isinstance(message, list) or not message:
            raise ValueError("relay sent a message that is not a JSON array")
        return message

    async def _sign_in(self):
        while True:
            message = await self._receive()
            if message[0] == "AUTH" and len(message) == 2:
                break
        auth = sign(self.secret, KIND_AUTH, [["relay", self.url], ["challenge", message[1]]], "")
        await self.socket.send(json.dumps(["AUTH", auth]))
        while True:
            answer = await self._receive()
            if answer[:2] == ["OK", auth["id"]] and len(answer) >= 3:
                break
        if answer[2] is not True:
            raise PermissionError("relay refused sign-in: " + str(answer[3] if len(answer) > 3 else ""))

    async def _read(self):
        error = ConnectionError("relay closed the connection")
        try:
            async for raw in self.socket:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, list) or len(message) < 2:
                    continue
                verb, key = message[0], message[1]
                if verb == "EVENT" and len(message) == 3 and key in self.subscriptions:
                    self.subscriptions[key].put_nowait(("event", message[2]))
                elif verb == "EOSE" and key in self.subscriptions:
                    self.subscriptions[key].put_nowait(("eose", None))
                elif verb == "CLOSED" and key in self.subscriptions:
                    self.subscriptions[key].put_nowait(("closed", str(message[2]) if len(message) > 2 else ""))
                elif verb == "OK" and len(message) >= 3 and key in self.pending:
                    future = self.pending.pop(key)
                    if not future.done():
                        future.set_result((message[2] is True, str(message[3]) if len(message) > 3 else ""))
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 - the connection is over either way
            error = ConnectionError(str(failure))
        for queue in self.subscriptions.values():
            queue.put_nowait(("lost", str(error)))
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()

    async def subscribe(self, filters):
        """("event", e) for the stored events matching any filter, then ("eose",
        None), then ("event", e) for live ones. A refusal raises PermissionError and
        a lost connection ConnectionError. Use with contextlib.aclosing."""
        sub = secrets.token_hex(8)
        queue = asyncio.Queue()
        self.subscriptions[sub] = queue
        try:
            await self.socket.send(json.dumps(["REQ", sub, *filters]))
            while True:
                kind, value = await queue.get()
                if kind == "closed":
                    raise PermissionError("relay closed the subscription: " + value)
                if kind == "lost":
                    raise ConnectionError(value)
                yield kind, value
        finally:
            self.subscriptions.pop(sub, None)
            if self.reader and not self.reader.done():
                try:
                    await self.socket.send(json.dumps(["CLOSE", sub]))
                except Exception:  # noqa: BLE001 - closing a dead connection's subscription
                    pass

    async def query(self, filters):
        """Stored events matching any filter, up to the relay's end-of-stored marker."""
        import contextlib
        events = []
        async with contextlib.aclosing(self.subscribe(filters)) as stream:
            async for kind, value in stream:
                if kind == "eose":
                    break
                events.append(value)
        return events

    async def publish(self, event):
        """(accepted, reason) as the relay answered."""
        future = asyncio.get_running_loop().create_future()
        self.pending[event["id"]] = future
        try:
            await self.socket.send(json.dumps(["EVENT", event]))
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        finally:
            self.pending.pop(event["id"], None)

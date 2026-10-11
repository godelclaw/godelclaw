"""Client of a durable CeTTa Telegram channel service (telegram-channel/1).

The service owns the bot's polling and sending. It records every received
update before this client sees it, and every submitted action before it is
sent, so neither survives only in this process. This client reads delivery
and receipt tasks, acknowledges each one after handling it, and submits keyed
action batches.

An operator command the service asks about is a task answered with its
reply text rather than acknowledged; the service answers the operator itself
if the answer is late.

Protocol: CWP1 over a private Unix seqpacket socket. A packet is b"CWP1",
a code byte, an ID length byte, the ID and a UTF-8 body.
"""
import json
import os
import socket
import threading
import time


class Changes:
    """Arm notifications before reading a durable snapshot; reread on any hint.

    Overflow and timers trigger the same reread. Platforms without inotify
    retain a bounded timer fallback, with no additional dependency.
    """
    def __init__(self, directories, ignore_names=()):
        self.directories = tuple(str(path) for path in directories)
        self.ignore_names = frozenset(ignore_names)
        self.fd = -1
        try:
            import ctypes
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.inotify_init1.argtypes = [ctypes.c_int]
            self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (AttributeError, OSError):
            pass

    def arm(self):
        if self.fd < 0:
            return 0
        from pathlib import Path
        for directory in self.directories:
            path = Path(directory)
            while not path.is_dir() and path != path.parent:
                path = path.parent
            self.libc.inotify_add_watch(self.fd, os.fsencode(path), 0x8 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800)
        try:
            while os.read(self.fd, 65536):
                pass
        except BlockingIOError:
            pass
        return 1

    def wait(self, seconds):
        if self.fd < 0:
            time.sleep(seconds)
            return 0
        import select
        import struct
        deadline = time.monotonic() + seconds
        while True:
            remaining = max(0, deadline - time.monotonic())
            if not select.select([self.fd], [], [], remaining)[0]:
                return 0
            try:
                events = os.read(self.fd, 65536)
            except BlockingIOError:
                continue
            offset = 0
            while offset < len(events):
                if len(events) - offset < 16:
                    return 1  # An uncertain hint requires a fresh snapshot.
                _, mask, _, size = struct.unpack_from("iIII", events, offset)
                name = os.fsdecode(events[offset + 16:offset + 16 + size].split(b"\0", 1)[0])
                offset += 16 + size
                # Overflow, a removed watch and directory changes always wake.
                # A writable lock descriptor closes even without a write;
                # those local bookkeeping events must not wake their own loop.
                if mask & (0x4000 | 0x8000 | 0x400 | 0x800) or name not in self.ignore_names:
                    return 1
            if time.monotonic() >= deadline:
                return 0

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

NEXT, RESULT, RECEIPT, SUBMIT = 1, 2, 3, 4
IDLE, TASK, STORED, PENDING, UNKNOWN, CONFLICT, INVALID, UNAVAILABLE, LIMIT = range(64, 73)
BODY_MAX = 65536
TEXT_MAX = 4096  # Unicode scalars per message, as the service validates.


class ChannelError(Exception):
    pass


def chunks(text, limit=TEXT_MAX):
    """Split text into Telegram-sized pieces, preferring line boundaries.

    Python strings index Unicode scalars, which is what the limit counts.
    """
    text = str(text)
    pieces = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = limit
        pieces.append(text[:cut])
        text = text[cut:].lstrip("\n") if text[cut:cut + 1] == "\n" else text[cut:]
    if text:
        pieces.append(text)
    return pieces


class Channel:
    """One client of the channel service. Thread-safe."""

    def __init__(self, path, sequence_path, timeout=5):
        self.path = path
        self.sequence_path = sequence_path
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = self._load_sequence()

    def _load_sequence(self):
        try:
            with open(self.sequence_path, encoding="ascii") as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _save_sequence(self, value):
        temporary = "%s.tmp.%d" % (self.sequence_path, os.getpid())
        with open(temporary, "w", encoding="ascii") as f:
            f.write("%d\n" % value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.sequence_path)

    def rpc(self, code, name="", body=""):
        encoded_name = name.encode("ascii")
        encoded_body = body.encode("utf-8")
        if len(encoded_name) > 64 or len(encoded_body) > BODY_MAX:
            raise ChannelError("request too large")
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as s:
            s.settimeout(self.timeout)
            s.connect(self.path)
            s.sendall(b"CWP1" + bytes([code, len(encoded_name)]) + encoded_name + encoded_body)
            reply = s.recv(6 + 64 + BODY_MAX)
        if len(reply) < 6 or reply[:4] != b"CWP1" or 6 + reply[5] > len(reply):
            raise ChannelError("malformed reply")
        n = reply[5]
        return reply[4], reply[6:6 + n].decode("ascii"), reply[6 + n:].decode("utf-8")

    def pending(self, limit=64):
        """Read up to limit pending tasks, oldest first, answering none."""
        tasks, after = [], ""
        while len(tasks) < limit:
            code, task, body = self.rpc(NEXT, after)
            if code == TASK:
                tasks.append((task, body))
                after = task
            elif code == IDLE:
                break
            elif code == UNKNOWN and after:
                # Answered meanwhile by another reader: return what is known;
                # the next read starts from the oldest pending task again.
                break
            else:
                raise ChannelError("next failed: %d" % code)
        return tasks

    def acknowledge(self, task):
        code, _, _ = self.rpc(RESULT, task, "[]")
        if code != STORED:
            raise ChannelError("acknowledgment failed: %d" % code)

    def answer(self, task, text):
        """Answer a command task. Resending the same answer is safe; a task
        already answered or no longer pending needs nothing more."""
        body = json.dumps(["answer", str(text)[:3800]], ensure_ascii=False, separators=(",", ":"))
        code, _, _ = self.rpc(RESULT, task, body)
        if code not in (STORED, UNKNOWN, CONFLICT):
            raise ChannelError("answer failed: %d" % code)

    def key(self, chat_id, thread=0):
        """A fresh LANE.SEQUENCE key, strictly increasing across restarts."""
        chat = int(chat_id)
        thread = int(thread)
        if not chat or thread < 0:
            raise ValueError("invalid lane")
        with self._lock:
            import fcntl
            with open(self.sequence_path + ".lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                sequence = max(self._last + 1, self._load_sequence() + 1, time.time_ns())
                self._save_sequence(sequence)
                self._last = sequence
        return "%d.%d.%020d" % (chat, thread, sequence)

    def submit(self, key, commands, attempts=4):
        """Record one action batch; resending identical bytes is safe."""
        body = json.dumps(commands, ensure_ascii=False, separators=(",", ":"))
        delay = 0.2
        for _ in range(attempts):
            try:
                code, _, _ = self.rpc(SUBMIT, key, body)
            except OSError:
                code = UNAVAILABLE
            if code == STORED:
                return
            if code not in (UNAVAILABLE, LIMIT):
                raise ChannelError("submission refused: %d" % code)
            time.sleep(delay)
            delay = min(delay * 2, 2.0)
        raise ChannelError("service unavailable")


class Outbox:
    """The client's own intent, durable before the service has recorded it.

    Every batch is appended here, fsynced, before it is submitted. One thread
    submits the queue in order, resending the same key and bytes until the
    service stores it, across service and client restarts. So no message is
    lost while the service is away, none is sent twice, and none overtakes an
    earlier one to the same chat. A lane whose last delivery is uncertain is
    held: its later batches stay queued, in order, until an operator releases
    it, and other lanes go on. A record's metadata stays until its receipts
    arrive, so a receipt that comes after a client restart is still known.
    """

    def __init__(self, channel, path):
        self.channel = channel
        self.path = path
        self.queue = []          # [(key, body)] not yet stored, in order
        self.entries = {}        # key -> metadata, until receipted
        self.stored = set()
        self.refused = {}
        self.held = set()        # lanes whose last delivery is uncertain
        self._condition = threading.Condition()
        self._running = True
        self._load()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _append(self, record):
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _load(self):
        records = {}
        order = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue  # a torn final line from a crash mid-write
                    key = record.get("key")
                    if "hold" in record:
                        self.held.add(record["hold"])
                    elif "release" in record:
                        self.held.discard(record["release"])
                    elif "body" in record:
                        records[key] = dict(record, stored=False)
                        order.append(key)
                    elif key in records and record.get("stored"):
                        records[key]["stored"] = True
                    elif record.get("done"):
                        records.pop(key, None)
        except OSError:
            pass
        live = [records[k] for k in order if k in records]
        self.queue = [(r["key"], r["body"]) for r in live if not r["stored"]]
        self.stored = {r["key"] for r in live if r["stored"]}
        self.entries = {r["key"]: r.get("entry") for r in live}
        temporary = "%s.tmp.%d" % (self.path, os.getpid())
        with open(temporary, "w", encoding="utf-8") as f:
            for lane in sorted(self.held):
                f.write(json.dumps({"hold": lane}) + "\n")
            for r in live:
                f.write(json.dumps({"key": r["key"], "body": r["body"], "entry": r.get("entry")},
                                   ensure_ascii=False, separators=(",", ":")) + "\n")
                if r["stored"]:
                    f.write(json.dumps({"key": r["key"], "stored": True}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.path)

    def enqueue(self, key, commands, entry=None):
        body = json.dumps(commands, ensure_ascii=False, separators=(",", ":"))
        with self._condition:
            self._append({"key": key, "body": body, "entry": entry})
            self.queue.append((key, body))
            self.entries[key] = entry
            self._condition.notify_all()

    def wait_stored(self, key, timeout):
        """True once stored; False while still queued; a code if refused."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while key not in self.stored and key not in self.refused:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True if key in self.stored else self.refused[key]

    def hold(self, lane):
        """Stop submitting to a lane whose last delivery is uncertain. Its
        later batches stay queued, in order, until the lane is released."""
        with self._condition:
            if lane not in self.held:
                self.held.add(lane)
                self._append({"hold": lane})

    def release(self, lane):
        with self._condition:
            if lane in self.held:
                self.held.discard(lane)
                self._append({"release": lane})
            self._condition.notify_all()

    def is_held(self, lane):
        with self._condition:
            return lane in self.held

    def done(self, key):
        """All receipts for key have arrived: forget it."""
        with self._condition:
            self.stored.discard(key)
            self.entries.pop(key, None)
            self._append({"key": key, "done": True})

    def _eligible(self):
        """The first queued batch of any lane that is not held. Each lane's
        batches keep their order; a held lane never blocks another."""
        blocked = set(self.held)
        for key, body in self.queue:
            lane = lane_of(key)
            if lane not in blocked:
                return key, body
            blocked.add(lane)
        return None

    def stop(self):
        with self._condition:
            self._running = False
            self._condition.notify_all()
        self._thread.join(timeout=5)

    def _run(self):
        delay = 0.2
        while True:
            with self._condition:
                while self._running and not self._eligible():
                    self._condition.wait(1)
                if not self._running:
                    return
                key, body = self._eligible()
            try:
                code = self.channel.rpc(SUBMIT, key, body)[0]
            except (OSError, ChannelError):
                code = UNAVAILABLE
            if code in (UNAVAILABLE, LIMIT):
                time.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue
            delay = 0.2
            with self._condition:
                self.queue.remove((key, body))
                if code == STORED:
                    self.stored.add(key)
                    self._append({"key": key, "stored": True})
                else:
                    # Definitive refusal (a conflict or an invalid batch):
                    # nothing will be sent for this key.
                    self.refused[key] = code
                    self.entries.pop(key, None)
                    self._append({"key": key, "done": True})
                self._condition.notify_all()


class Receipts:
    """Delivery receipts by submission key, awaited by the submitting thread."""

    def __init__(self):
        self._results = {}
        self._condition = threading.Condition()

    def record(self, key, position, result):
        with self._condition:
            self._results.setdefault(key, {})[int(position)] = result
            self._condition.notify_all()

    def wait(self, key, count, timeout):
        """Results for positions 0..count-1, or None for those still pending."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                have = self._results.get(key, {})
                done = [p for p in range(count) if p in have]
                rejected = have.get(-1)
                if rejected is not None or len(done) == count:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            have = self._results.pop(key, {})
            if have.get(-1) is not None:
                return have[-1]
            return [have.get(p) for p in range(count)]


def lane_of(key):
    """CHAT.THREAD of a LANE.SEQUENCE key."""
    return key.rsplit(".", 1)[0]


def parse(observation):
    """Classify one task observation from the channel service."""
    value = json.loads(observation)
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        raise ChannelError("malformed observation")
    return value

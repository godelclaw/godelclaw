"""Native mailbox arrivals become checkpointed agent input, never executable code.

The native receive is one transaction. The agent checkpoint is another:
Ready is retained until that checkpoint records its source identity. Recovery
then settles it without offering it again. No cross-store atomicity is claimed.
"""
from __future__ import annotations
import atexit
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading

_runtime = None
_runtime_lock = threading.RLock()
_name = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
ABI = "agent-input-v1"


def _token(value):
    if not isinstance(value, str) or not _name.fullmatch(value):
        raise ValueError("mailbox ticket must use letters, digits, dot, dash or underscore")
    return value


def source_id(instance, ticket):
    return "mailbox:" + _token(instance) + ":" + _token(ticket)


def source_ticket(value, instance):
    if not isinstance(value, str) or not value.startswith("mailbox:" + instance + ":"):
        raise ValueError("foreign mailbox source identity")
    ticket = value[len("mailbox:" + instance + ":"):]
    if source_id(instance, ticket) != value:
        raise ValueError("invalid mailbox source identity")
    return ticket


def _api(config):
    # Only operator-selected installed code can implement these handlers.
    tools = str(Path(config["tools"]).resolve())
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import native_mailbox
    import space_service_client
    if Path(native_mailbox.__file__).resolve().parent != Path(tools):
        raise RuntimeError("different native mailbox implementation already imported")
    return native_mailbox, space_service_client


def _committed(identity):
    import input_ack
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1":
        raise RuntimeError("mailboxes require durable agent checkpoints")
    return bool(input_ack.already_committed(identity))


def _offer(text, identity):
    import telegram
    return telegram.enqueue_mailbox_event(text, identity)


class Inbox:
    def __init__(self, config, *, committed=_committed, offer=_offer):
        if config.get("version") != 1:
            raise ValueError("unsupported mailbox configuration")
        self.config = dict(config)
        self.instance = _token(config["instance"])
        self.channels = dict(config["channels"])
        if not self.channels or any(not isinstance(k,str) or not k or "\0" in k or
                                    not isinstance(v,str) or not v or "\0" in v
                                    for k,v in self.channels.items()):
            raise ValueError("mailbox channels require configured senders")
        self.auto_receive = tuple(config.get("auto_receive", ()))
        if (len(set(self.auto_receive)) != len(self.auto_receive) or
            any(channel not in self.channels for channel in self.auto_receive)):
            raise ValueError("persistent inbox requires configured channels")
        self.capacity = config.get("max_active", 256)
        self.preview = config.get("preview_chars", 2048)
        self.watch_ms = config.get("watch_ms", 30000)
        if (type(self.capacity) is not int or not 1 <= self.capacity < 65536 or
            type(self.preview) is not int or not 0 < self.preview <= 65536 or
            type(self.watch_ms) is not int or not 0 < self.watch_ms <= 60000):
            raise ValueError("invalid mailbox admission, preview or watch budget")
        self.native, self.wire = _api(config)
        if config.get("storage_identity") is not None:
            identity = config["storage_identity"]
            if (not isinstance(identity,(tuple,list)) or len(identity)!=2 or
                not isinstance(identity[0],str) or type(identity[1]) is not int or identity[1]<1):
                raise ValueError("invalid pinned mailbox storage identity")
            self.identity = tuple(identity)
        policy = Path(config["tools"]).resolve().parent / "lib/petta/lib_mailbox.metta"
        bindings = json.dumps({"channels":self.channels,"auto_receive":sorted(self.auto_receive)},sort_keys=True).encode()
        program = hashlib.sha256(ABI.encode() + bindings + policy.read_bytes() +
                                 hashlib.sha256(Path(config["worker"]).read_bytes()).digest()).hexdigest()
        self.tag = "agent-mailbox:" + self.instance + ":" + program
        self.committed, self.offer = committed, offer
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = None
        self.monitor = None
        self.codex = None
        self.offered = set()
        self.error = ""
        self.pressure = ""
        self.notifications = self.scans = 0
        self.box = self._open()
        self.uuid = self.box.space.uuid
        self.identity = (self.uuid,self.box.space.id)
        try:
            self._seal(self.box, program)
            for channel in self.auto_receive:
                self.box.listen(channel,self.channels[channel],self.tag)
        except BaseException:
            self.box.close(); raise

    def _open(self):
        return self.native.Mailbox(self.config["socket"], self.config["space"],
                                   self.config["worker"], timeout=35,expected=getattr(self,"identity",None))

    def _seal(self, box, program):
        E, Var = self.wire.E, self.wire.Var
        seal = E("AgentMailboxProgram",1,self.instance,program)
        def operation(revision):
            rows = box._rows(E("AgentMailboxProgram",1,self.instance,Var(0)),revision)
            if rows and rows != (seal,):
                raise RuntimeError("mailbox program changed; explicit migration required")
            if not rows:
                box.space.add(seal)
        box._operation(operation)

    def _owner(self, ticket):
        return self.instance + ":mailbox:" + _token(ticket)

    def _registration(self, box, ticket):
        wait = box.registered(_token(ticket))
        continuation = self.wire.E("MailboxResume",1,self.tag,ticket)
        if (wait[6:9] != (self._owner(ticket),1,continuation) or
            self.channels.get(wait[3]) != wait[5]):
            raise RuntimeError("mailbox registration authority or program mismatch")
        return wait

    def wait(self, ticket, channel, correlation, *, intent=None):
        """Register one stable ticket and return immediately, including on replay."""
        _token(ticket)
        if ticket.startswith("inbox-"):
            raise ValueError("inbox- tickets are reserved for persistent arrivals")
        if channel not in self.channels:
            raise ValueError("mailbox channel has no configured binding")
        if channel in self.auto_receive:
            raise ValueError("persistent inbox already receives this channel; use mailbox-status")
        with self.lock:
            state = self.box.register(ticket,channel,correlation,self.channels[channel],
                self._owner(ticket),1,self.wire.E("MailboxResume",1,self.tag,ticket),
                capacity=self.capacity,intent=intent)
        self.start()
        return {"ticket":ticket,"state":state,"channel":channel,"correlation":correlation}

    def watch_codex(self, ticket, thread, turn):
        config=self.config.get("codex")
        if not config or config.get("channel") not in self.channels:
            raise ValueError("Codex mailbox binding is not configured")
        return self.wait(ticket,config["channel"],turn,
                         intent=self.wire.E("MailboxCodexWatch",1,thread,turn))

    def cancel(self, ticket):
        with self.lock:
            self._registration(self.box,ticket)
            value = self.box.cancel(ticket,self._owner(ticket),1)
        return {"ticket":ticket,"state":self._json(value)}

    def _json(self, value):
        if isinstance(value,self.wire.Symbol): return value.name
        if isinstance(value,tuple): return [self._json(x) for x in value]
        return value

    def read(self, ticket, offset=0, count=None):
        """Read inert text by a bounded page; reading grants no settlement."""
        count = self.preview if count is None else count
        if type(offset) is not int or offset<0 or type(count) is not int or not 0<count<=self.preview:
            raise ValueError("invalid mailbox read page")
        E, Var = self.wire.E, self.wire.Var
        with self.lock:
            wait = self._registration(self.box,ticket)
            def operation(revision):
                ready = self.box._one(self.box._rows(E("MailboxReady",1,ticket,*wait[6:9],Var(0)),revision))
                done = self.box._one(self.box._rows(E("MailboxSettled",1,ticket,*wait[6:8],Var(0)),revision))
                state = ready or done
                if state is None:
                    cancelled = self.box._rows(E("MailboxCancelled",1,ticket,*wait[6:8]),revision)
                    return {"ticket":ticket,"state":"cancelled" if cancelled else "waiting"}
                message = self.box._one(self.box._rows(E("MailboxMessage",1,state[-1],*wait[3:6],Var(0)),revision))
                if message is None: raise RuntimeError("mailbox message missing")
                body = message[-1]
                return {"ticket":ticket,"state":"ready" if ready else "settled","channel":wait[3],
                        "correlation":wait[4],"sender":wait[5],"message":message[2],
                        "offset":offset,"body":body[offset:offset+count],"characters":len(body),
                        "next":offset+count if offset+count<len(body) else None}
            return self.box._operation(operation)

    def acknowledge(self, identities):
        tickets = [source_ticket(value,self.instance) for value in identities]
        with self.lock:
            # Validate the complete batch before settling any of its members.
            for ticket, identity in zip(tickets,identities):
                self._registration(self.box,ticket)
                if not self.committed(identity): return 0
            for ticket in tickets:
                self.box.settle(ticket,self._owner(ticket),1)
        return 1

    def status(self):
        with self.lock:
            active = self.box.active(self.tag,self.capacity+1)
        health = None
        if self.config.get("bridge_health"):
            try:
                with open(self.config["bridge_health"]) as stream:
                    health=json.loads(stream.read(8192))
            except (OSError,ValueError): health={"state":"unavailable"}
        return {"instance":self.instance,"active":[{"ticket":row[2],"state":row[0].name} for row in active],
                "error":self.error,"backpressure":self.pressure,"codex_error":self.codex.error if self.codex else "",
                "directory_bridge":health,
                "notifications":self.notifications,"scans":self.scans}

    def scan(self, box):
        box.space.describe()
        captured = box.space.revision  # before reads, closing the query/watch race
        self.pressure = ""
        # A persistent receiver selects one occurrence per channel per pass.
        # Backpressure leaves the rest pending; full active views still include
        # every registered interactive wait, rather than a fixed oldest prefix.
        for channel in self.auto_receive:
            pending = box.pending(channel,self.channels[channel])
            if pending is None: continue
            ticket = "inbox-" + hashlib.sha256(pending[5].encode()).hexdigest()
            try:
                box.accept(ticket,pending[5],channel,self.channels[channel],self.tag,
                           self._owner(ticket),1,capacity=self.capacity)
            except self.native.MailboxError as error:
                if not str(error).startswith("active-wait capacity"):
                    raise
                self.pressure = "active-wait capacity"
            except self.wire.ServiceError as error:
                if error.reason!=self.wire.S("space-limit"): raise
                self.pressure = "native store capacity"
        active = box.active(self.tag,self.capacity+1)
        self.scans += 1
        retained = {row[2] for row in active}
        self.offered.intersection_update(retained)
        codex_bindings=[]
        for row in active:
            ticket = row[2]
            self._registration(box,ticket)
            result = box.resolve(ticket)
            if result[0] == self.wire.S("MailboxWaiting") and self.codex:
                def observation(revision):
                    return box._one(box._rows(self.wire.E("MailboxIntent",1,ticket,self.tag,self.wire.Var(0)),revision))
                intent=box._operation(observation)
                if intent:
                    watch=intent[4]
                    wait=self._registration(box,ticket)
                    if watch[:2]!=(self.wire.S("MailboxCodexWatch"),1) or watch[3]!=wait[4]:
                        raise RuntimeError("invalid stored Codex watch")
                    codex_bindings.append({"thread":watch[2],"turn":watch[3],"channel":wait[3],"sender":wait[5]})
            if result[0] != self.wire.S("MailboxResult"): continue
            identity = source_id(self.instance,ticket)
            if self.committed(identity):
                box.settle(ticket,self._owner(ticket),1)
                self.offered.discard(ticket)
            elif ticket not in self.offered:
                message = result[-1]
                data = {"ticket":ticket,"channel":message[3],"correlation":message[4],
                        "sender":message[5],"characters":len(message[6]),
                        "body":message[6][:self.preview],"read_more":len(message[6])>self.preview}
                if self.offer(json.dumps(data,ensure_ascii=False),identity):
                    self.offered.add(ticket)
        if self.codex: self.codex.update(codex_bindings)
        return captured

    def start(self):
        with self.lock:
            if self.thread is None:
                if self.config.get("codex"):
                    import mailbox_codex_runtime
                    self.codex=mailbox_codex_runtime.CodexSubscriptions(self)
                self.thread = threading.Thread(target=self._run,name="native-mailbox-"+self.instance,daemon=True)
                self.thread.start()
        return 1

    def _run(self):
        box = None
        try:
            while not self.stopped.is_set():
                try:
                    if box is None:
                        box = self._open(); self.monitor = box
                        if box.space.uuid != self.uuid:
                            raise RuntimeError("mailbox storage identity changed")
                    captured = self.scan(box)
                    self.error = ""
                    while not self.stopped.is_set():
                        changed = box.space.watch(captured,self.watch_ms)
                        if changed[0] == self.wire.S("changed"):
                            self.notifications += 1
                            break
                except Exception as error:
                    self.error = type(error).__name__ + ": " + str(error)
                    if box is not None:
                        try: box.close()
                        except Exception: pass
                        box = None; self.monitor = None
                    # Recovery reopens the same identity and checks state. No
                    # mutation plan or paid external operation is replayed.
                    self.stopped.wait(1)
        finally:
            if box is not None:
                try: box.close()
                except Exception: pass
            self.monitor = None

    def close(self):
        if self.stopped.is_set(): return
        self.stopped.set()
        if self.codex: self.codex.close()
        monitor = self.monitor
        if monitor and monitor.client.connection:
            try: monitor.client.connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.thread:
            self.thread.join(timeout=5)
        with self.lock:
            self.box.close()


def runtime():
    global _runtime
    path = os.environ.get("METTACLAW_MAILBOX_CONFIG", "")
    if not path: return None
    with _runtime_lock:
        if _runtime is None:
            config = json.loads(Path(path).read_text())
            if config["instance"] != os.environ.get("METTACLAW_INSTANCE"):
                raise RuntimeError("mailbox instance differs from agent identity")
            _committed(source_id(config["instance"],"startup-check"))
            _runtime = Inbox(config)
        return _runtime


def _call(method, *args):
    try:
        box = runtime()
        value = getattr(box,method)(*args) if box else {"state":"disabled"}
        return json.dumps(value,ensure_ascii=False)
    except Exception as error:
        return json.dumps({"state":"refused","reason":str(error)},ensure_ascii=False)


def start():
    return _call("start")


def wait(ticket, channel, correlation):
    return _call("wait",ticket,channel,correlation)


def cancel(ticket):
    return _call("cancel",ticket)


def watch_codex(ticket, thread, turn):
    return _call("watch_codex",ticket,thread,turn)


def read(ticket, offset=0, count=None):
    return _call("read",ticket,offset,count)


def status():
    return _call("status")


def acknowledge(identities):
    if not identities: return 1
    box = runtime()
    return box.acknowledge(identities) if box else 0


@atexit.register
def close():
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close(); _runtime = None

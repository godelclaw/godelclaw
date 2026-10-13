"""Native mailbox arrivals become checkpointed agent input, never executable code.

The native receive is one transaction. The agent checkpoint is another:
Ready is retained until that checkpoint records its source identity. Recovery
then settles it without offering it again. No cross-store atomicity is claimed.
"""
from __future__ import annotations
import atexit
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import uuid

_runtime = None
_runtime_lock = threading.RLock()
_starter = None
_starter_stop = None
_name = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
ABI = "agent-input-v1"
# A sibling's note starts with this line; the receiver honours it only for a
# configured sibling, and only to lower the note's standing to peer mail.
_from = re.compile(r"From: ([A-Za-z0-9_.-]{1,128})\n\n")
SIBLING_WAKES_PER_HOUR = 6
PEER_WINDOW_SECONDS = 3600
SEND_CHARS = 32768


def _write_health(path, value):
    if not path: return
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".mailbox-health-", dir=target.parent)
    except OSError: return
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream); stream.write("\n")
        os.replace(temporary, target)
    except OSError: pass
    finally:
        try: os.unlink(temporary)
        except OSError: pass


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


def _tool(config, name):
    # Isolate this release from older clients already loaded by recall or
    # other providers. Never replace their global modules or value classes.
    tools = str(Path(config["tools"]).resolve())
    package="_cetta_mailbox_sdk_"+hashlib.sha256(tools.encode()).hexdigest()[:16]
    if package not in sys.modules:
        spec=importlib.machinery.ModuleSpec(package,loader=None,is_package=True)
        spec.submodule_search_locations=[tools]
        sys.modules.setdefault(package,importlib.util.module_from_spec(spec))
    module=importlib.import_module(package+"."+name)
    if Path(module.__file__).resolve().parent != Path(tools):
        raise RuntimeError("configured native mailbox implementation differs")
    return module


def _api(config):
    return _tool(config,"native_mailbox"), _tool(config,"space_service_client")


def _siblings(config):
    siblings = dict(config.get("siblings") or {})
    if any(not isinstance(k,str) or not _name.fullmatch(k) or k == _own_name(config) or
           not isinstance(v,str) or not v for k,v in siblings.items()):
        raise ValueError("siblings map agent names to inbox directories")
    return siblings


def _own_name(config):
    # The mailbox name siblings address this agent by; the instance by default.
    return _token(config.get("name", config.get("instance")))


def peer_turns():
    """Admitted peer input can resume an idle loop without operator standing."""
    import telegram
    box = _runtime
    if box is None: return 0
    identities = {source_id(box.instance, ticket) for ticket in box.peer_offered.copy()}
    return box.peer_turn_budget if identities.intersection(telegram._current_batch_update_ids) else 0


def send(to, text, correlation=""):
    """Deliver a note to a sibling's inbox, stamped with this agent's name."""
    config = json.loads(Path(os.environ["METTACLAW_MAILBOX_CONFIG"]).read_text())
    siblings = _siblings(config)
    text = str(text)
    if to not in siblings:
        return "MAILBOX_SEND_REFUSED: no sibling mailbox named %s; siblings: %s" % (to, ", ".join(sorted(siblings)) or "none")
    if not text.strip() or len(text) > SEND_CHARS:
        return "MAILBOX_SEND_REFUSED: a note needs 1 to %d characters" % SEND_CHARS
    identity = uuid.uuid4().hex
    try:
        _tool(config,"mailbox_bridge").deliver(siblings[to], identity, str(correlation) or identity, "local",
                                               "From: %s\n\n%s" % (_own_name(config), text))
    except OSError as error:
        return "MAILBOX_SEND_FAILED: %s" % error
    return "MAILED %s id=%s chars=%d" % (to, identity, len(text))



def reply(ticket, text):
    """Reply to a stored peer occurrence, preserving its correlation."""
    try:
        box = runtime()
        if box is None:
            return "MAILBOX_REPLY_REFUSED: mailbox disabled"
        received = box.read(str(ticket))
        sender = _from.match(received.get("body", ""))
        if not sender or sender.group(1) not in box.siblings:
            return "MAILBOX_REPLY_REFUSED: ticket has no configured sibling sender"
        return send(sender.group(1), text, received["correlation"])
    except Exception as error:
        return "MAILBOX_REPLY_REFUSED: " + str(error)


def _committed(identity):
    import input_ack
    if os.environ.get("METTACLAW_OBSERVED_STRATEGY", "0") != "1":
        raise RuntimeError("mailboxes require durable agent checkpoints")
    return bool(input_ack.already_committed(identity))


def _offer(text, identity, sibling=None):
    import telegram
    if sibling:
        return telegram.enqueue_mailbox_event(text, identity, sibling)
    return telegram.enqueue_mailbox_event(text, identity)


def _request_dispatched(db, request, ticket):
    """A paired wait/send parks only after its matching send returned success.

    Until then a failed or unfinished dispatch needs a model decision. This
    projection can be rebuilt after a crash from the original proposal and
    receipts; it never sends the request again.
    """
    for row in db.execute("SELECT serial,commands FROM task_proposals WHERE request=? ORDER BY serial DESC",(request,)):
        commands=json.loads(row["commands"])
        index=next((i for i,c in enumerate(commands) if isinstance(c,list) and len(c)>=4 and c[:2]==["mailbox-wait",ticket]),None)
        if index is None: continue
        positions=[i for i,c in enumerate(commands) if i>index and isinstance(c,list) and len(c)==4 and
                   c[0]=="mailbox-send" and c[3]==commands[index][3]]
        if not positions: return True
        action=db.execute("SELECT ticket FROM actions WHERE serial=?",(row["serial"],)).fetchone()
        if not action or not db.execute("SELECT 1 FROM sqlite_master WHERE name='command_attempts'").fetchone():return False
        for position in positions:
            receipt=db.execute("SELECT result FROM command_attempts WHERE ticket=? AND position=?",(action[0],position)).fetchone()
            if not receipt or not receipt[0]:return False
            result=json.loads(receipt[0])
            if result.get("disposition")!="returned" or not str(result.get("result","")).startswith("MAILED "):return False
        return True
    return True


def _update_task_wait_state(db, request):
    states=list(db.execute("SELECT ticket,state FROM task_mailbox_waits WHERE request=?",(request,)))
    waiting=any(s[1]=='MailboxWait' for s in states) and not any(s[1]=='MailboxReady' for s in states)
    waiting=waiting and all(_request_dispatched(db,request,s[0]) for s in states if s[1]=='MailboxWait')
    if waiting:
        db.execute("UPDATE requests SET status='waiting-mailbox' WHERE id=? AND status IN ('active','processed')",(request,))
    else:
        db.execute("UPDATE requests SET status='active' WHERE id=? AND status='waiting-mailbox'",(request,))


def finish_task_dispatch(store, request):
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='task_mailbox_waits'").fetchone():
            _update_task_wait_state(db,request)


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
        self.siblings = _siblings(config)
        if (len(set(self.auto_receive)) != len(self.auto_receive) or
            any(channel not in self.channels for channel in self.auto_receive)):
            raise ValueError("persistent inbox requires configured channels")
        self.capacity = config.get("max_active", 256)
        self.preview = config.get("preview_chars", 2048)
        self.watch_ms = config.get("watch_ms", 30000)
        self.peer_limit = config.get("peer_wakes_per_hour", SIBLING_WAKES_PER_HOUR)
        self.peer_turn_budget = config.get("peer_turns", 2)
        if (type(self.capacity) is not int or not 1 <= self.capacity < 65536 or
            type(self.preview) is not int or not 0 < self.preview <= 65536 or
            type(self.watch_ms) is not int or not 0 < self.watch_ms <= 60000):
            raise ValueError("invalid mailbox admission, preview or watch budget")
        if (type(self.peer_limit) is not int or not 1 <= self.peer_limit <= 256 or
            type(self.peer_turn_budget) is not int or not 1 <= self.peer_turn_budget <= 20):
            raise ValueError("invalid peer wake or turn budget")
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
        self.peer_offered = set()
        self.task_arrivals = {}
        self.peer_retry_at = None
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

    def _peer_wake(self, box, sender, ticket):
        """Reserve a wake once per occurrence, using the native batch boundary.

        Full budgets leave Ready untouched. A deadline retries admission;
        restarts reconcile the same grant instead of resetting the budget.
        """
        E, Var = self.wire.E, self.wire.Var
        now = int(time.time() * 1000)
        period = int(PEER_WINDOW_SECONDS * 1000)
        def operation(revision):
            old = box._one(box._rows(E("AgentMailboxPeerBudget",1,self.instance,sender,Var(0)),revision))
            entries = old[-1] if old else ()
            if (not isinstance(entries,tuple) or len(entries) > 256 or
                any(not isinstance(e,tuple) or len(e)!=2 or not isinstance(e[0],str) or
                    type(e[1]) is not int or e[1] < 0 for e in entries)):
                raise RuntimeError("invalid stored peer wake budget")
            if any(e[0] == ticket for e in entries): return True, None
            recent = tuple(e for e in entries if now - e[1] < period)
            if len(recent) >= self.peer_limit:
                return False, (min(e[1] for e in recent) + period) / 1000
            replacement = E("AgentMailboxPeerBudget",1,self.instance,sender,recent+((ticket,now),))
            ops = (E("remove",old),) if old else ()
            box.space.batch(*ops,E("add",replacement))
            return True, None
        allowed, due = box._operation(operation)
        if due is not None:
            self.peer_retry_at = due if self.peer_retry_at is None else min(self.peer_retry_at,due)
            self.pressure = "peer wake budget"
        return allowed

    def wait(self, ticket, channel, correlation, *, intent=None, task="", note=""):
        """Register one stable ticket and return immediately, including on replay."""
        _token(ticket)
        if ticket.startswith("inbox-"):
            raise ValueError("inbox- tickets are reserved for persistent arrivals")
        if channel not in self.channels:
            raise ValueError("mailbox channel has no configured binding")
        with self.lock:
            options = {"context": (str(task), str(note) or "Resume the waiting task.")} if task else {}
            state = self.box.register(ticket,channel,correlation,self.channels[channel],
                self._owner(ticket),1,self.wire.E("MailboxResume",1,self.tag,ticket),
                capacity=self.capacity,intent=intent,**options)
        if state == "duplicate":
            state = self.read(ticket)["state"]
        if task:
            self.reconcile_tasks(self.box,self.box.active(self.tag,self.capacity+1))
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
            self.reconcile_tasks(self.box,self.box.active(self.tag,self.capacity+1))
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
                "peer_retry_at":self.peer_retry_at,
                "directory_bridge":health,
                "notifications":self.notifications,"scans":self.scans}

    def reconcile_tasks(self, box, active):
        """Recover the local scheduling projection from committed native waits.

        The native receive is authoritative. A crash before the local update
        is repaired on the next scan; completion and cancellation unpark work.
        This never dispatches an outgoing request again.
        """
        contexts = []
        for row in active:
            context = box.task_context(row[2])
            if context:
                if context[3] != self.tag:
                    raise RuntimeError("task continuation belongs to another program")
                contexts.append((row[2],context[4],context[5],row[0].name))
        import agent_checkpoint
        try:
            agent_checkpoint.path()
        except RuntimeError:
            if contexts: raise
            return  # A taskless mailbox can also be used without an agent ledger.
        store = agent_checkpoint.ledger()
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS task_mailbox_waits(ticket TEXT PRIMARY KEY, request TEXT, note TEXT, state TEXT)")
            old = [dict(r) for r in db.execute("SELECT * FROM task_mailbox_waits WHERE state IN ('MailboxWait','MailboxReady')")]
        present = {r[2] for r in active}
        for previous in old:
            if previous["ticket"] not in present:
                outcome = box.resolve(previous["ticket"])
                contexts.append((previous["ticket"],previous["request"],previous["note"],outcome[0].name))
        with store.transaction() as db:
            for ticket, request, note, state in contexts:
                db.execute("INSERT INTO task_mailbox_waits VALUES (?,?,?,?) ON CONFLICT(ticket) DO UPDATE SET state=excluded.state",
                           (ticket,request,note,state))
            for request in {c[1] for c in contexts}:
                _update_task_wait_state(db,request)

    def scan(self, box):
        box.space.describe()
        captured = box.space.revision  # before reads, closing the query/watch race
        self.pressure = ""
        self.peer_retry_at = None
        # Exact receives get their correlated message before the generic inbox
        # listener can consume it. Resolution is itself an atomic COMM.
        for row in box.active(self.tag,self.capacity+1):
            if not row[2].startswith("inbox-"):
                box.resolve(row[2])
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
        self.reconcile_tasks(box,active)
        retained = {row[2] for row in active}
        self.offered.intersection_update(retained)
        self.peer_offered.intersection_update(retained)
        self.task_arrivals = {key:value for key,value in self.task_arrivals.items()
                              if value["ticket"] in retained}
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
                context = box.task_context(ticket) if hasattr(box,"task_context") else None
                if context:
                    if context[3] != self.tag:
                        raise RuntimeError("task continuation belongs to another program")
                    data.update(task=context[4],resume_note=context[5])
                    self.task_arrivals[identity] = {"task":context[4],"note":context[5],"ticket":ticket}
                stamp = _from.match(message[6]) if message[3] in self.auto_receive else None
                if stamp and stamp.group(1) in self.siblings:
                    data["from"] = stamp.group(1)
                    if not self._peer_wake(box,data["from"],ticket): continue
                    self.peer_offered.add(ticket)
                    offered = self.offer(json.dumps(data,ensure_ascii=False),identity,sibling=data["from"])
                else:
                    offered = self.offer(json.dumps(data,ensure_ascii=False),identity)
                if offered:
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
                self._health()
        return 1

    def _health(self):
        """Diagnostic state is separate from scheduler progress or settlement."""
        value={"instance":self.instance,"watching":self.monitor is not None,
               "error":self.error,"backpressure":self.pressure,
               "peer_retry_at":self.peer_retry_at,
               "scans":self.scans,"notifications":self.notifications}
        _write_health(self.config.get("health_path"), value)

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
                    self._health()
                    while not self.stopped.is_set():
                        wait_ms = self.watch_ms
                        if self.peer_retry_at is not None:
                            left = self.peer_retry_at - time.time()
                            if left <= 0: break
                            wait_ms = min(wait_ms,max(1,int(left*1000)))
                        changed = box.space.watch(captured,wait_ms)
                        if changed[0] == self.wire.S("changed"):
                            self.notifications += 1
                            break
                except Exception as error:
                    self.error = type(error).__name__ + ": " + str(error)
                    if box is not None:
                        try: box.close()
                        except Exception: pass
                        box = None; self.monitor = None
                    self._health()
                    # Recovery reopens the same identity and checks state. No
                    # mutation plan or paid external operation is replayed.
                    self.stopped.wait(1)
        finally:
            if box is not None:
                try: box.close()
                except Exception: pass
            self.monitor = None
            self._health()

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


def _call(method, *args, **kwargs):
    try:
        box = runtime()
        value = getattr(box,method)(*args,**kwargs) if box else {"state":"disabled"}
        return json.dumps(value,ensure_ascii=False)
    except Exception as error:
        return json.dumps({"state":"refused","reason":str(error)},ensure_ascii=False)


def _start_when_available(stop):
    while not stop.is_set():
        try:
            box = runtime()
            if stop.is_set(): return
            if box: box.start()
            return
        except Exception as error:
            try:
                config = json.loads(Path(os.environ["METTACLAW_MAILBOX_CONFIG"]).read_text())
                _write_health(config.get("health_path"),
                    {"instance": config.get("instance"), "watching": False,
                     "error": type(error).__name__ + ": " + str(error),
                     "backpressure": "", "scans": 0, "notifications": 0})
            except Exception: pass
            stop.wait(1)


def start():
    """Arm recovery at boot without waiting for an optional store to start."""
    global _starter, _starter_stop
    if not os.environ.get("METTACLAW_MAILBOX_CONFIG"):
        return json.dumps({"state": "disabled"})
    with _runtime_lock:
        if _starter is None or not _starter.is_alive():
            _starter_stop = threading.Event()
            _starter = threading.Thread(target=_start_when_available,
                args=(_starter_stop,), name="native-mailbox-start", daemon=True)
            _starter.start()
    return json.dumps({"state": "starting"})


def wait(ticket, channel, correlation, note=""):
    import strategy_host
    task = strategy_host._focus if strategy_host.enabled() else ""
    result = _call("wait",ticket,channel,correlation,task=task,note=note)
    status = json.loads(result).get("state")
    if task and status == "waiting":
        store = strategy_host.ledger()
        with store.transaction() as db:
            store.event(db,"task-wait",request=task,ticket=str(ticket),note=str(note))
    return result


def task_arrivals(identities):
    box = _runtime
    if box is None: return []
    arrivals = getattr(box,"task_arrivals",{})
    return [arrivals[i] for i in identities if i in arrivals]


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
    box = None
    try:
        box = runtime()
        return box.acknowledge(identities) if box else 0
    except Exception as error:
        # The cognitive checkpoint is already durable. Keep the source
        # ready for watcher reconciliation rather than crash the engine.
        if box:
            box.error = type(error).__name__ + ": " + str(error)
            box._health()
        return 0


@atexit.register
def close():
    global _runtime, _starter, _starter_stop
    if _starter_stop: _starter_stop.set()
    if _starter and _starter is not threading.current_thread():
        _starter.join(timeout=5)
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close(); _runtime = None
        _starter = _starter_stop = None

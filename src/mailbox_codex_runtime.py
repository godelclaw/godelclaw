"""One event-driven Codex subscription connection for an agent's durable waits."""
import os
import select
import socket
import threading


class CodexSubscriptions:
    def __init__(self, inbox):
        from mailbox_codex import CodexBridge, transport
        self.inbox=inbox
        self.bridge_type=CodexBridge
        self.config=inbox.config["codex"]
        self.factory=transport(self.config["transport_module"])
        self.lock=threading.Lock()
        self.bindings=()
        self.read,self.write=os.pipe2(os.O_NONBLOCK|os.O_CLOEXEC)
        self.stopped=threading.Event()
        self.client=None
        self.error=""
        self.thread=threading.Thread(target=self._run,name="codex-mailbox-"+inbox.instance,daemon=True)
        self.thread.start()

    def update(self, bindings):
        value=tuple(sorted(tuple(sorted(item.items())) for item in bindings))
        with self.lock:
            if value==self.bindings: return
            self.bindings=value
        self._wake()

    def _wake(self):
        try: os.write(self.write,b"!")
        except BlockingIOError: pass  # the pipe already carries a wake

    def _drain(self):
        while True:
            try:
                if not os.read(self.read,4096): break
            except BlockingIOError: break

    def _current(self):
        with self.lock: return tuple(dict(item) for item in self.bindings)

    def _run(self):
        box=None; client=None; delay=.1
        bridge=None; bridge_bindings=()
        try:
            while not self.stopped.is_set():
                self._drain()
                bindings=self._current()
                if not bindings:
                    select.select([self.read],[],[],None)
                    continue
                try:
                    box=self.inbox._open()
                    if box.space.uuid!=self.inbox.uuid:
                        raise RuntimeError("mailbox storage identity changed")
                    client=self.factory(self.config["app_server_socket"])
                    self.client=client
                    if bridge is None or bindings!=bridge_bindings:
                        pending=bridge.pending if bridge else set()
                        bridge=self.bridge_type(box,bindings)
                        targets={(item["thread"],item["turn"]) for item in bindings}
                        bridge.pending=pending & targets
                        bridge_bindings=bindings
                    else:
                        bridge.mailbox=box
                    bridge.connect(client)  # subscribe, then reconcile full history
                    self.error=""; delay=.1
                    while not self.stopped.is_set():
                        if client.pending_notifications:
                            bridge.accept(client.pending_notifications.pop(0))
                            continue
                        ready=select.select([self.read,client.connection.socket],[],[],None)[0]
                        if self.read in ready:
                            self._drain()
                            if self._current()!=bindings: break
                        if client.connection.socket in ready:
                            # Idle select has no deadline; partial frame reads
                            # retain the configured transport's normal timeout.
                            bridge.accept(client.connection.receive_json())
                except Exception as error:
                    self.error=type(error).__name__+": "+str(error)
                    select.select([self.read],[],[],delay)
                    delay=min(5,delay*2)
                finally:
                    self.client=None
                    if client is not None:
                        try: client.close()
                        except Exception: pass
                        client=None
                    if box is not None:
                        try: box.close()
                        except Exception: pass
                        box=None
        finally:
            os.close(self.read); os.close(self.write)

    def close(self):
        self.stopped.set(); self._wake()
        client=self.client
        if client:
            try: client.connection.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.thread.join(timeout=5)

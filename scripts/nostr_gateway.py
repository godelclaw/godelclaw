#!/usr/bin/env python3
"""Bring Nostr agent mail into claw-mail, and send claw-mail out over Nostr.

Inbound: for each local agent's key, the gateway reads the mail addressed to it
on the relay. It keeps a message that is well formed, signed by a registered key
and opens for the agent's key, and sends it into the agent's claw-mail inbox as
mail headers and a body. The claw-mail sender is `nostr.<registry name>` within
the gateway's own account, so it cannot coincide with a local agent, and the
send identity is the event identifier, so a message read again is a duplicate
send, never a second delivery. When the author asked for receipts, the agent's
key publishes a delivered receipt.

Outbound: a local agent sends mail to the gateway's claw-mail inbox, `nostr` of
the gateway's account (for example `oruzi/nostr`), as a header block, a blank
line and the body:

    To: <registry name>
    Subject: <subject>
    In-Reply-To: <event id>      (optional)
    Type: <team type>            (optional)
    Task: <task id>              (optional)

The claw-mail sender (`account/agent`, whose account the service authenticates)
selects the key that signs, by the `[senders]` table, where `account/*` covers
every agent of an account. The signed event is saved before it is published, so
a retry after a crash republishes the same event and the relay keeps one. The
sender is told whether the message was sent, at its address in `[replies]`
(for an agent that reads the shared `agent-mail` bus, its bare name), and
otherwise at its own inbox.

Receipts: a delivered or read receipt for mail an agent sent is believed only
from the key the mail was addressed to, and the sender is told.

Running: each key holds one live subscription, so new mail arrives without
polling, and it resumes from its read position after any disconnect. Keys and
the outbox are served concurrently, and one slow channel never delays another;
within a key, mail is delivered in the order it was written. `--once` reads what
is stored, empties the outbox and exits.

The state is one small JSON file: per-key read positions, recently handled
identifiers, signed events not yet confirmed, and sent mail awaiting receipts.

It needs coincurve, cryptography and websockets, which the agent runtime does not.
"""
import argparse
import asyncio
import contextlib
import email.utils
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import tomllib
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import nostr_mail as nm  # noqa: E402

AGENT = re.compile(r"[A-Za-z0-9_.-]+")
EVENT_ID = re.compile(r"[0-9a-f]{64}")


def log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + text, file=sys.stderr, flush=True)


def header_value(value):
    """One header line: control characters, line breaks included, become spaces."""
    return "".join(" " if unicodedata.category(char).startswith("C") else char for char in str(value)).strip()


def render(event, letter, author, recipient):
    """The claw-mail text of a kept message: mail headers, a blank line, the body."""
    lines = ["From: %s <%s@nostr>" % (author, event["pubkey"]),
             "To: " + recipient,
             "Subject: " + header_value(letter["subject"]),
             "Date: " + email.utils.formatdate(event["created_at"], usegmt=True),
             "Message-ID: <%s@nostr>" % event["id"]]
    parents = [parent for parent in nm.tag_values(event, "e") if EVENT_ID.fullmatch(parent)]
    if parents:
        lines.append("In-Reply-To: <%s@nostr>" % parents[0])
    for field in ("type", "task"):
        if field in letter:
            lines.append("X-Team-%s: %s" % (field.title(), header_value(letter[field])))
    return "\n".join(lines) + "\n\n" + letter["body"]


def parse_outgoing(text):
    """(headers, body) of an outbound request; ValueError says what is wrong."""
    head, blank, body = text.partition("\n\n")
    if not blank:
        raise ValueError("write the headers, a blank line, then the body")
    fields = {}
    for line in head.splitlines():
        name, colon, value = line.partition(":")
        if not colon or not name.strip():
            raise ValueError("not a header line: " + line[:80])
        fields[name.strip().lower()] = value.strip()
    unknown = fields.keys() - {"to", "subject", "in-reply-to", "type", "task"}
    if unknown:
        raise ValueError("unknown header: " + ", ".join(sorted(unknown)))
    if not fields.get("to"):
        raise ValueError("no To: header")
    parent = fields.get("in-reply-to", "").strip("<>").removesuffix("@nostr")
    if parent and not EVENT_ID.fullmatch(parent):
        raise ValueError("In-Reply-To must name an event identifier")
    fields["in-reply-to"] = parent
    return fields, body


class Gateway:
    def __init__(self, config_path):
        path = Path(config_path).expanduser()
        config = tomllib.loads(path.read_text())
        relative = lambda value: (path.parent / Path(value).expanduser()) if not Path(value).expanduser().is_absolute() \
            else Path(value).expanduser()
        self.relay = config["relay"]
        self.names = nm.load_registry(relative(config["registry"]))
        self.keys = self._load_keys(relative(config["keys"]))
        self.state_path = relative(config.get("state", "~/.local/state/godelclaw-nostr/gateway.json"))
        self.mailer = config.get("mailer", "claw-mail")
        self.space = config.get("space", "agent-mail")
        self.outbox = config.get("outbox", "nostr")
        self.overlap = int(config.get("overlap", 600))
        self.receipt_days = float(config.get("receipt_days", 30))
        self.inbound = dict(config.get("inbound", {}))
        self.senders = dict(config.get("senders", {}))
        self.replies = dict(config.get("replies", {}))
        for name in list(self.inbound.values()) + [self.outbox]:
            if not AGENT.fullmatch(name.split("/")[-1]):
                raise ValueError("not a claw-mail name: " + name)
        missing = (set(self.inbound) | set(self.senders.values())) - set(self.keys)
        if missing:
            raise ValueError("no key for: " + ", ".join(sorted(missing)))
        self.state = self._load_state()

    @staticmethod
    def _load_keys(path):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise PermissionError("%s is readable by others; make it 0600" % path)
        return {name: entry["seckey_hex"] for name, entry in json.loads(path.read_text()).items()}

    def _load_state(self):
        try:
            state = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            state = {}
        for field in ("since", "recent", "receipted", "outbound", "sent"):
            state.setdefault(field, {})
        return state

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(self.state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state_path)

    def prune(self):
        """Forget what no read position can reach again, and sent mail past its receipt period."""
        horizon = min(self.state["since"].values(), default=0) - 2 * self.overlap
        for field in ("recent", "receipted"):
            self.state[field] = {i: t for i, t in self.state[field].items() if t >= horizon}
        oldest = time.time() - self.receipt_days * 86400
        self.state["sent"] = {i: r for i, r in self.state["sent"].items() if r["at"] >= oldest}

    async def mail(self, agent, *arguments):
        """One claw-mail call as `agent`; its JSON result. Calls run concurrently."""
        process = await asyncio.create_subprocess_exec(
            self.mailer, "--json", "--space", self.space, *arguments,
            env={**os.environ, "CLAW_MAIL_AGENT": agent},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await process.communicate()
        except asyncio.CancelledError:
            # Reap the call before giving up, so no process or pipe outlives it.
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(process.communicate())
            raise
        lines = out.decode(errors="replace").strip().splitlines()
        try:
            return json.loads(lines[-1])
        except (IndexError, ValueError):
            return {"status": "error", "reason": err.decode(errors="replace").strip()[-500:] or "no result"}

    async def tell(self, sender, ident, text):
        """Tell an outbound sender about its mail, once per identity."""
        result = await self.mail(self.outbox, "send", self.outbox, self.replies.get(sender, sender), text,
                                 "--id", ident)
        if result.get("status") not in ("accepted", "duplicate"):
            raise RuntimeError("could not tell %s: %s" % (sender, result.get("reason", result)))

    # ── inbound ──────────────────────────────────────────────────────────────
    async def receive(self, key_name, recipient, live):
        """Deliver the mail and receipts addressed to one key: what is stored,
        oldest first, and when `live`, then whatever arrives."""
        secret = self.keys[key_name]
        me = nm.public_key(secret)
        since = max(0, self.state["since"].get(key_name, 0) - self.overlap)
        query = [{"kinds": [nm.KIND_MAIL, nm.KIND_RECEIPT], "#p": [me], "since": since}]
        async with nm.Relay(self.relay, secret) as relay:
            async with contextlib.aclosing(relay.subscribe(query)) as stream:
                stored = []
                async for kind, event in stream:
                    if kind == "eose":
                        for old in sorted(stored, key=lambda e: (e.get("created_at", 0), e.get("id", ""))):
                            await self.take(key_name, recipient, secret, me, relay, old)
                        stored = None
                        self.prune()
                        self.save()
                        if not live:
                            return
                    elif stored is not None:
                        stored.append(event)
                    else:
                        await self.take(key_name, recipient, secret, me, relay, event)

    async def take(self, key_name, recipient, secret, me, relay, event):
        if not isinstance(event, dict) or event.get("id") in self.state["recent"]:
            return
        if event.get("kind") == nm.KIND_RECEIPT:
            await self.note_receipt(event)
        elif not await self.deliver(event, secret, me, recipient, relay):
            raise RuntimeError("claw-mail did not take %s; retrying from the read position" % event["id"][:16])
        stamp = event.get("created_at", 0) if isinstance(event.get("created_at"), int) else 0
        self.state["recent"][event["id"]] = stamp
        self.state["since"][key_name] = max(self.state["since"].get(key_name, 0), stamp)
        self.save()

    async def deliver(self, event, secret, me, recipient, relay):
        """Send one event into claw-mail if it is kept. False to retry it later."""
        author = self.names.get(event.get("pubkey"))
        letter = nm.open_letter(secret, event) if nm.verify(event) and author and event["pubkey"] != me else None
        if letter is None:
            log("not kept: %s (unsigned, unregistered or does not open)" % str(event.get("id"))[:16])
            return True
        sender = "nostr." + author
        if not AGENT.fullmatch(sender):
            log("not kept: registry name %r is not a claw-mail name" % author)
            return True
        result = await self.mail(sender, "send", sender, recipient, render(event, letter, author, recipient),
                                 "--id", event["id"])
        status = result.get("status")
        if status == "conflict":
            log("conflict on %s: the same identifier was delivered with other text" % event["id"])
            return True
        if status not in ("accepted", "duplicate"):
            log("claw-mail refused %s: %s" % (event["id"], result.get("reason", result)))
            return False
        if ["ack", "request"] in event["tags"] and event["id"] not in self.state["receipted"]:
            accepted, reason = await relay.publish(nm.receipt(secret, event, "delivered"))
            if accepted:
                self.state["receipted"][event["id"]] = event["created_at"]
            else:
                log("receipt for %s refused: %s" % (event["id"], reason))
        return True

    async def note_receipt(self, event):
        """Tell a sender about a receipt for its mail, believed only from the addressee."""
        fact = nm.receipt_fact(event)
        record = self.state["sent"].get(fact[0]) if fact else None
        if not record or not nm.verify(event) or event["pubkey"] != record["to_key"]:
            return
        message, how = fact
        await self.tell(record["sender"], "nostr-receipt-" + event["id"], "%s by %s: %s (event %s)"
                        % ("Delivered" if how == "delivered" else "Read", record["to"], record["subject"], message))

    # ── outbound ─────────────────────────────────────────────────────────────
    def sender_key(self, sender):
        if sender in self.senders:
            return self.senders[sender]
        account, slash, _ = sender.partition("/")
        return self.senders.get(account + "/*") if slash else None

    async def handle(self, delivery):
        """Publish one outbound request, tell its sender, settle it."""
        ident, sender = delivery["id"], delivery["from"]
        pending = self.state["outbound"].get(ident)
        if pending is None:
            by_name = {name: key for key, name in self.names.items()}
            key_name = self.sender_key(sender)
            try:
                if key_name is None:
                    raise ValueError("%s has no Nostr key at this gateway" % sender)
                fields, body = parse_outgoing(delivery["text"])
                recipient = by_name.get(fields["to"])
                if recipient is None:
                    raise ValueError("%s is not in the registry" % fields["to"])
            except ValueError as problem:
                await self.tell(sender, "nostr-outcome-" + ident, "Not sent: %s" % problem)
                await self.mail(self.outbox, "ack", self.outbox, ident)
                return
            event = nm.seal(self.keys[key_name], recipient, fields.get("subject", ""), body,
                            kind=fields.get("type"), task=fields.get("task"), reply_to=fields.get("in-reply-to"))
            pending = {"key": key_name, "to": fields["to"], "to_key": recipient, "event": event}
            self.state["outbound"][ident] = pending
            self.save()
        async with nm.Relay(self.relay, self.keys[pending["key"]]) as relay:
            accepted, reason = await relay.publish(pending["event"])
        subject = nm.open_letter(self.keys[pending["key"]], pending["event"])["subject"]
        if accepted:
            self.state["sent"][pending["event"]["id"]] = {
                "sender": sender, "to": pending["to"], "to_key": pending.get("to_key"),
                "subject": subject, "at": time.time()}
            self.save()
            await self.tell(sender, "nostr-outcome-" + ident,
                            "Sent to %s: %s (event %s)" % (pending["to"], subject, pending["event"]["id"]))
        else:
            await self.tell(sender, "nostr-outcome-" + ident,
                            "Not sent to %s: the relay refused it: %s" % (pending["to"], reason))
        await self.mail(self.outbox, "ack", self.outbox, ident)
        del self.state["outbound"][ident]
        self.save()

    async def drain(self):
        """Handle the outbox until it is empty."""
        while True:
            delivery = await self.mail(self.outbox, "next", self.outbox)
            if delivery.get("status") != "delivery":
                if delivery.get("status") != "idle":
                    log("outbox unavailable: %s" % delivery.get("reason", delivery))
                return
            await self.handle(delivery)

    # ── running ──────────────────────────────────────────────────────────────
    async def once(self):
        for key_name, recipient in self.inbound.items():
            try:
                await self.receive(key_name, recipient, live=False)
            except Exception as error:  # noqa: BLE001 - one key's failure must not stop the others
                log("inbound for %s failed: %s: %s" % (key_name, type(error).__name__, error))
        try:
            await self.drain()
        except Exception as error:  # noqa: BLE001
            log("outbound failed: %s: %s" % (type(error).__name__, error))

    def tick(self):
        asyncio.run(self.once())

    async def forever(self, name, step):
        """Run `step` again after every failure, backing off while failures repeat."""
        backoff = 1
        while True:
            started = time.monotonic()
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a lost relay or mailbox is retried
                if time.monotonic() - started > 60:
                    backoff = 1
                log("%s: %s: %s; again in %ds" % (name, type(error).__name__, error, backoff))
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def serve(self):
        async def outbox():
            while True:
                delivery = await self.mail(self.outbox, "wait", self.outbox, "--forever")
                if delivery.get("status") != "delivery":
                    raise RuntimeError("outbox unavailable: %s" % delivery.get("reason", delivery))
                await self.handle(delivery)
        tasks = [asyncio.create_task(self.forever("inbound for " + key_name,
                                                  lambda k=key_name, r=recipient: self.receive(k, r, live=True)))
                 for key_name, recipient in self.inbound.items()]
        tasks.append(asyncio.create_task(self.forever("outbound", outbox)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.save()

    def run(self):
        async def main():
            task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            for number in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(number, task.cancel)
            with contextlib.suppress(asyncio.CancelledError):
                await self.serve()
        asyncio.run(main())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=os.environ.get("NOSTR_GATEWAY_CONFIG",
                                                           "~/.config/godelclaw-nostr/gateway.toml"))
    parser.add_argument("--once", action="store_true", help="read what is stored, empty the outbox, exit")
    args = parser.parse_args(argv)
    gateway = Gateway(args.config)
    if args.once:
        gateway.tick()
    else:
        gateway.run()


if __name__ == "__main__":
    main()

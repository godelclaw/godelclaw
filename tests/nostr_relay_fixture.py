"""A closed Nostr relay for tests, following the policy that
Mettapedia.Protocols.Nostr.Relay models.

Every connection is challenged at once (NIP-42), and nothing is served before it
signs in with an allowlisted key and this relay's address. A signed-in key may
publish only its own events, and may read only events it wrote or that name it
in a `p` tag. A deletion request removes only its author's events. Ephemeral
events are relayed and not stored. The allowlist is read at every message, so a
removed key loses access at once.
"""
import asyncio
import json
import secrets
import threading
import time

import nostr_mail as nm


def accepts(event, query):
    """One NIP-01 filter: every condition holds, and a list condition by membership."""
    if "ids" in query and event["id"] not in query["ids"]:
        return False
    if "authors" in query and event["pubkey"] not in query["authors"]:
        return False
    if "kinds" in query and event["kind"] not in query["kinds"]:
        return False
    if "since" in query and event["created_at"] < query["since"]:
        return False
    if "until" in query and event["created_at"] > query["until"]:
        return False
    return all(set(nm.tag_values(event, key[1])) & set(values)
               for key, values in query.items() if key.startswith("#") and len(key) == 2)


def readable(event, key):
    return event["pubkey"] == key or key in nm.tag_values(event, "p")


class GatedRelay:
    """Run in its own thread: `with GatedRelay(allow) as relay: ... relay.url ...`."""

    def __init__(self, allow, auth_window=600, reject_future=1800, max_limit=5000):
        self.allow = set(allow)
        self.auth_window, self.reject_future, self.max_limit = auth_window, reject_future, max_limit
        self.events, self.clients, self.url = {}, [], None
        self.refused = set()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(10)
        return self

    def __exit__(self, *error):
        asyncio.run_coroutine_threadsafe(self._stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        self.loop.close()

    async def _start(self):
        from websockets.asyncio.server import serve
        self.server = await serve(self._connection, "127.0.0.1", 0)
        self.url = "ws://127.0.0.1:%d" % self.server.sockets[0].getsockname()[1]

    async def _stop(self):
        self.server.close()
        await self.server.wait_closed()

    def stored(self):
        return list(self.events.values())

    def subscribers(self, key):
        """How many open subscriptions a key holds."""
        return sum(len(c["subs"]) for c in list(self.clients) if c["key"] == key)

    def shut_out(self, key):
        """Refuse a key's sign-ins and close its connections."""
        async def close():
            self.refused.add(key)
            for client in list(self.clients):
                if client["key"] == key:
                    await client["socket"].close()
        asyncio.run_coroutine_threadsafe(close(), self.loop).result(10)

    def let_in(self, key):
        self.loop.call_soon_threadsafe(self.refused.discard, key)

    def signed_in(self, client):
        return client["key"] if client["key"] in self.allow else None

    async def _connection(self, socket):
        client = {"socket": socket, "challenge": secrets.token_hex(16), "key": None, "subs": {}}
        self.clients.append(client)
        try:
            await socket.send(json.dumps(["AUTH", client["challenge"]]))
            async for raw in socket:
                await self._message(client, json.loads(raw))
        except Exception:  # noqa: BLE001 - a broken connection only ends itself
            pass
        finally:
            self.clients.remove(client)

    async def _send(self, client, message):
        await client["socket"].send(json.dumps(message))

    async def _message(self, client, message):
        verb = message[0]
        if verb == "AUTH" and len(message) == 2:
            await self._auth(client, message[1])
        elif verb == "EVENT" and len(message) == 2:
            await self._event(client, message[1])
        elif verb == "REQ" and len(message) >= 3:
            await self._req(client, message[1], message[2:])
        elif verb == "CLOSE" and len(message) == 2:
            client["subs"].pop(message[1], None)

    async def _auth(self, client, event):
        ok = (nm.verify(event) and event["kind"] == nm.KIND_AUTH
              and client["challenge"] in nm.tag_values(event, "challenge")
              and abs(event["created_at"] - time.time()) <= self.auth_window
              and self.url in nm.tag_values(event, "relay") and event["pubkey"] in self.allow
              and event["pubkey"] not in self.refused)
        if ok:
            client["key"] = event["pubkey"]
        await self._send(client, ["OK", event.get("id", ""), ok, "" if ok else "restricted: sign-in refused"])

    async def _event(self, client, event):
        me = self.signed_in(client)
        reason = ("auth-required: sign in" if not me else
                  "invalid: bad event" if not nm.verify(event) else
                  "restricted: only your own events" if event["pubkey"] != me else
                  "invalid: AUTH events are not stored" if event["kind"] == nm.KIND_AUTH else
                  "invalid: too far in the future" if event["created_at"] > time.time() + self.reject_future else
                  None)
        if reason:
            return await self._send(client, ["OK", event.get("id", ""), False, reason])
        if 20000 <= event["kind"] < 30000:
            await self._broadcast(event)
            return await self._send(client, ["OK", event["id"], True, ""])
        if event["id"] in self.events:
            return await self._send(client, ["OK", event["id"], True, "duplicate: already have it"])
        if event["kind"] == 5:
            for named in nm.tag_values(event, "e"):
                if named in self.events and self.events[named]["pubkey"] == me:
                    del self.events[named]
        self.events[event["id"]] = event
        await self._send(client, ["OK", event["id"], True, ""])
        await self._broadcast(event)

    async def _req(self, client, sub, queries):
        me = self.signed_in(client)
        if not me:
            return await self._send(client, ["CLOSED", sub, "auth-required: sign in"])
        client["subs"][sub] = queries
        sent = set()
        for query in queries:
            matches = sorted((e for e in self.events.values() if readable(e, me) and accepts(e, query)),
                             key=lambda e: (-e["created_at"], e["id"]))
            for event in matches[:min(query.get("limit", self.max_limit), self.max_limit)]:
                if event["id"] not in sent:
                    sent.add(event["id"])
                    await self._send(client, ["EVENT", sub, event])
        await self._send(client, ["EOSE", sub])

    async def _broadcast(self, event):
        for client in list(self.clients):
            me = self.signed_in(client)
            if me and readable(event, me):
                for sub, queries in list(client["subs"].items()):
                    if any(accepts(event, query) for query in queries):
                        await self._send(client, ["EVENT", sub, event])

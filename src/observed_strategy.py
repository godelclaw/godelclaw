"""Durable managed-host transitions corresponding to ObservedStrategy.lean.

The native ledger is the checkpoint, not the replaceable prompt history. Reservations
precede effects; an interrupted reservation is uncertain and is never replayed.
Completion checkers are host supplied, never inferred from provider prose.
"""

import hashlib
import json
from pathlib import Path
import time
import uuid


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


class IdentityConflict(ValueError):
    """A retained identity already names different immutable source data."""


class Ledger:
    def __init__(self, path=None, *, initialize=True, timeout=10):
        # The records live in the native ledger; the path only names the instance's state directory.
        self.path = Path(path) if path is not None else None
        self.timeout = max(0.0, float(timeout))
        self._owner = None

    def _owner_candidate(self):
        """The native store's owner. Before its first capture the first caller sets one, so every
        process reads the same owner; once it has one it keeps it."""
        if self._owner is None:
            import input_ack
            self._owner = input_ack.ledger_state().get("owner") or input_ack.ledger_owner(uuid.uuid4().hex)
        return self._owner

    @staticmethod
    def _native():
        """Whether the requests, turns and actions are records of the native ledger (METTACLAW_INPUT_LEDGER_SOCKET)."""
        import input_ack
        return bool(input_ack._native_socket())

    # The meta keys of a new ledger; on the native store a key never written has this value.
    INITIAL_META = {"revision": 0, "mode": "godelclaw", "paused": False, "next_sample": 0, "pending": None,
                    "context_focus": None}

    def _native_meta(self):
        """The meta keys of the native store, decoded."""
        import input_ack
        meta = dict(self.INITIAL_META)
        meta.update(input_ack.ledger_state())
        for counter in ("clock", "notes", "attempts"):  # the store's own numbering, not meta keys
            meta.pop(counter, None)
        if "owner" not in meta:  # no turn was captured yet: the store's owner, set now if it had none
            meta["owner"] = self._owner_candidate()
        if "router_revision" in meta:
            meta["router_revision"] = json.loads(meta["router_revision"])
        return meta

    @staticmethod
    def _native_action(ticket, request):
        """SELECT * FROM actions WHERE ticket=? AND request=?, from the native store: a dict or None."""
        import input_ack
        found = [a for a in input_ack.actions(("ticket", ticket)) if a["request"] == request]
        return found[0] if found else None


    def accept(self, key, content, checker="operator", *, kind=None):
        kind = kind or ("message" if key.startswith("input:") else
                        "goal" if key.startswith("goal:") else
                        "autonomous" if key == "autonomous" else "task")
        if kind not in ("message", "notification", "task", "goal", "autonomous"):
            raise ValueError("unknown request kind")
        import input_ack
        content = str(content)
        answer = input_ack.request_accept(key, content, checker, kind, content[:180], digest(content))
        if answer == "conflict":
            raise IdentityConflict("request identity reused for different intent")
        return answer == "created"

    def control(self, *, mode=None, paused=None, router_revision=None):
        import input_ack
        input_ack.ledger_control(mode, paused, None if router_revision is None else encoded(router_revision))
        return


    def restore(self):
        import input_ack
        input_ack.ledger_restore()
        return

    @staticmethod
    def selectable(db, key):
        import input_ack
        row = input_ack.request_get(str(key))
        return bool(row and (row["status"] == "active" or
                             (row["status"] == "processed" and row["kind"] in ("message", "notification"))))

    def processed(self, key):
        """A durable input handover settles delivery, not the requested work."""
        import input_ack
        return input_ack.request_settle(str(key), time.time())


    def checkpoint(self, working, input_ids=(), request=None):
        """Commit the successor and its settled input identities atomically.

        Transport acknowledgement follows this commit. After a crash between
        them, the service's still-pending input is acknowledged from this
        journal without presenting it to cognition a second time.

        One native transaction stores the working set, the settled input ids, their
        notices' processed flags and the request's processed status (the request is a
        record of the same store: see input_ack.turn_checkpoint). An unreachable server
        raises LedgerUnavailable.
        """
        return self._native_checkpoint(working, input_ids, request)

    def _native_checkpoint(self, working, input_ids, request):
        import input_ack
        ids = [str(identity) for identity in input_ids]
        # The time of the processed records is the working set's own (a KeyError, and
        # nothing written, when ids come without it).
        saved = working["saved_at"] if ids else (working.get("saved_at") if isinstance(working, dict) else None)
        # Whether the request exists and may be settled is decided in the same native transaction.
        input_ack.turn_checkpoint(encoded(working), ids, str(request) if request else "", float(saved or 0.0))

    def working_checkpoint(self):
        import input_ack
        text = input_ack.working_set()
        return None if text is None else json.loads(text)

    def link_inputs(self, request, inputs):
        """Record which arrivals a request was formed from."""
        inputs = {str(i) for i in inputs}
        import input_ack
        if not input_ack.link_inputs(request, inputs):
            raise IdentityConflict("input envelope identity reused for different occurrences")
        return

    def requests_with_input(self, input_id):
        import input_ack
        return input_ack.linked(input_id)

    def disposition(self, key, disposition, note):
        """Agent workflow decisions never produce checker-certified achievement.
        A refusal leaves its reason in self.refusal."""
        statuses = {"keep": "active", "waiting-user": "waiting-user",
                    "defer": "deferred", "addressed": "addressed", "reopen": "active"}
        if disposition not in statuses or not isinstance(note, str) or not 1 <= len(note) <= 800:
            raise ValueError("use keep, waiting-user, defer, addressed or reopen with a short reason")
        import input_ack
        self.refusal = input_ack.request_disposition(key, disposition, note)
        return self.refusal is None

    def request_page(self, offset=0, limit=8):
        offset, limit = max(0, int(offset)), max(1, int(limit))
        import input_ack
        total, rows = input_ack.request_page(offset, limit)
        return dict(records=rows, total=total, next_offset=offset + len(rows) if offset + len(rows) < total else None)

    def request_record(self, key, offset=0):
        offset = max(0, int(offset))
        import input_ack
        found = input_ack.request_record(key, offset)
        if found is None:
            return dict(id=key, status="unknown")
        return dict(id=found["id"], status=found["status"], checker=found["checker"], kind=found["kind"],
                    processed=found["processed"], note=found["note"], content=found["slice"],
                    content_digest=found["digest"], offset=offset,
                    next_offset=offset+4000 if offset+4000 < found["length"] else None)

    @staticmethod
    def _native_active(request):
        """SELECT * FROM requests WHERE id=? AND status='active', from the native store: a dict or None."""
        import input_ack
        found = input_ack.request_get(str(request), text=True)
        if not found or found["status"] != "active":
            return None
        return dict(id=found["id"], content=found["content"], status=found["status"], checker=found["checker"])

    def status_of(self, key):
        """requests.status of a request, or None when there is none (SELECT status FROM requests WHERE id=?)."""
        import input_ack
        found = input_ack.request_get(str(key))
        return found["status"] if found else None

    def request_rows(self, ids):
        """id, content, status, kind, note of the requests among `ids` (all of them for None), oldest first:
        the join of requests and request_context the task projections read."""
        import input_ack
        return [dict(id=r["id"], content=r["content"], status=r["status"], kind=r["kind"], note=r["note"])
                for r in input_ack.request_rows(list(ids) if ids is not None else "all")]

    @staticmethod
    def native_request(key):
        """One request whole from the native store (id, kind, checker, status, processed, note, content), or None."""
        import input_ack
        return input_ack.request_get(str(key), text=True)

    def protected(self, focus, result_chars=6000):
        import input_ack
        native = input_ack.request_protected(focus)
        active, counts = native["unfinished"], native["counts"]
        request, request_length = native["focus"] if native["focus"] else ("", 0)
        results = []
        for item in self._native_results(focus):
            receipt = json.loads(item["receipt"])
            payload = str(receipt["payload"])
            results.append({**receipt, "payload": payload[:result_chars],
                            "payload_digest": digest(payload),
                            "truncated": len(payload) > result_chars})
        return {"owner": self._native_meta()["owner"], "focus": focus,
                "request": request, "unfinished": active,
                "counts": counts, "omitted_active": max(0, counts.get("active", 0) - len(active)),
                "request_truncated": request_length > 4000,
                "request_index": {"retrieve": ["request-page", 0], "scope": "all retained requests"},
                "results": results}

    @staticmethod
    def _native_results(focus):
        """The receipts the protected view shows, from the native store: those not yet observed and the
        newest of the focus, oldest first, at most two (as rows with the receipt's JSON text)."""
        import input_ack
        return [{"ticket": a["ticket"], "receipt": encoded(a["receipt"])}
                for a in input_ack.actions(("protected", "" if focus is None else focus))]

    def active(self):
        import input_ack
        return [dict(id=r["id"], content=r["content"], status=r["status"], checker=r["checker"])
                for r in input_ack.request_rows("active")]

    def recover_recorded_actions(self):
        """Recover only batches whose admitted commands all have durable results."""
        import input_ack
        ready = []
        for action in input_ack.actions(("status", "uncertain")):
            proposal = json.loads(action["command"])
            if not isinstance(proposal, dict) or "limit" not in proposal:
                continue
            commands = proposal["commands"]
            expected = min(len(commands), proposal["limit"])
            attempts = input_ack.attempts(ticket=action["ticket"])
            if (len(attempts) == expected and all(a["result"] is not None for a in attempts)
                    and all(a["position"] == i and json.loads(a["command"]) == commands[i]
                            for i, a in enumerate(attempts))):
                ready.append((action["ticket"], {"recovered_results": [json.loads(a["result"]) for a in attempts],
                                                 "deferred_commands": commands[expected:]}))
        for ticket, result in ready:
            self.report(ticket, encoded(result))
        return len(ready)


    OBSERVE_CHUNK = 20

    def observe_presented(self, calls):
        """Mark observed the actions an answered prompt presented: `calls` are the (ticket, position) calls it
        carried with their results; an action counts when its receipt exists and every attempt of it was
        carried (lean/pettaclaw/ObservedReceipts.lean). Answers how many it marked."""
        calls = [(str(ticket), int(position)) for ticket, position in calls]
        if not calls:
            return 0
        import input_ack
        pairs = input_ack.presented_receipts(calls)
        # Small transactions: a transaction's own edits are searched linearly, so its cost grows with
        # the square of its marks; twenty cost well under a second.
        for start in range(0, len(pairs), self.OBSERVE_CHUNK):
            input_ack.action_observe(pairs[start:start + self.OBSERVE_CHUNK])
        return len(pairs)

    def observe(self, tickets, presented):
        """The exact correlated result projection must be in the sent request."""
        import input_ack
        pairs = []
        for ticket in tickets:
            if ticket in presented:
                projection = next((r for r in presented[ticket] if r.get("ticket") == ticket), None)
                if projection and isinstance(projection.get("payload_digest"), str):
                    pairs.append((ticket, projection["payload_digest"]))
        input_ack.action_observe(pairs)
        return


    def capture(self, focus, boundary, router, now, guard="", wake_seconds=600, goal_lease=None):
        import input_ack
        captured = input_ack.turn_capture(focus, digest(boundary), encoded(router), guard, self._owner_candidate())
        if captured is None:
            return None
        serial, revision, mode, owner = captured
        return {"serial": serial, "revision": revision, "mode": mode, "owner": owner,
                "focus": focus, "boundary_digest": digest(boundary), "router": router, "guard": guard}

    def decision_pending(self, serial):
        """Whether this exact captured turn may still be decided."""
        return self._native_meta()["pending"] == serial

    def decide(self, serial, response, now, guard="", retry_after=1):
        import input_ack
        kind = response.get("kind", "text")
        if kind == "command" and "tool" not in response:
            # The tool is read only for the pending turn, after the check.
            if self._native_meta()["pending"] != serial:
                return "ignored"
            raise KeyError("tool")
        command = encoded(response.get("command")) if kind == "command" else ""
        return input_ack.turn_decide(serial, kind, response["tool"] if kind == "command" else "", command)

    def report(self, ticket, payload, disposition="returned", domain="unknown"):
        import input_ack
        return input_ack.action_report(ticket, payload, disposition, domain, digest(str(payload)))

    def finish(self, request, ticket, checkers, *, now=None):
        import time
        now = time.time() if now is None else now
        import input_ack
        work = self._native_active(request)
        action = self._native_action(ticket, request)
        if not work or not action or not action["observed"]:
            return False
        receipt = action["receipt"]
        checker = checkers.get(work["checker"])
        if (receipt["disposition"] != "returned" or receipt["domain"] != "success"
                or checker is None or not checker(dict(work), receipt)):
            return False
        return input_ack.action_finish(request, ticket)

    def retire(self, prefix, note):
        """Close the open cycles of a goal its agent retired; never an achievement."""
        import input_ack
        return input_ack.request_retire(prefix, note[:800])

    def cancel(self, request, operator_authorized=False):
        if not operator_authorized:
            return False
        import input_ack
        return input_ack.request_cancel(request)

    def compact(self, keep=0):
        # Replaceable presentation history is separate from requests, issued
        # actions and receipts; the native store keeps no event journal to compact.
        return

    def attest(self, request, ticket, checker):
        """A trusted task oracle may establish domain success for its receipt."""
        import input_ack
        work = self._native_active(request)
        action = self._native_action(ticket, request)
        if not work or not action or not action["receipt"] or not action["observed"]:
            return False
        if action["receipt"]["disposition"] != "returned" or not checker(dict(work), dict(action["receipt"])):
            return False
        return input_ack.action_attest(request, ticket)


    def projection(self):
        import input_ack
        actions = [dict(ticket=a["ticket"], request=a["request"], serial=a["serial"], tool=a["tool"],
                        command=a["command"], status=a["status"],
                        receipt=None if a["receipt"] is None else encoded(a["receipt"]), observed=a["observed"])
                   for a in input_ack.actions("all")]
        return {"meta": self._native_meta(),
                "requests": [dict(id=r["id"], content=r["content"], status=r["status"], checker=r["checker"])
                             for r in input_ack.request_rows("all")],
                "actions": actions, "events": []}

    def finish_exact_results(self):
        """Run request-specific, operator-registered exact-output checkers."""
        import input_ack
        exact = {r["id"]: r["checker"] for r in input_ack.request_rows("active")
                 if r["checker"][:7].isascii() and r["checker"][:7].lower() == "sha256:"}
        found = [a for key in exact for a in input_ack.actions(("request", key))
                 if a["observed"] and a["receipt"] is not None]
        candidates = [dict(id=a["request"], checker=exact[a["request"]], ticket=a["ticket"])
                      for a in sorted(found, key=lambda action: action["serial"])]
        return self._finish_exact(candidates)

    def _finish_exact(self, candidates):
        count = 0
        for candidate in candidates:
            expected = candidate["checker"].removeprefix("sha256:")
            checker = lambda work, receipt: digest(str(receipt["payload"])) == expected
            if self.attest(candidate["id"], candidate["ticket"], checker):
                count += self.finish(candidate["id"], candidate["ticket"], {candidate["checker"]: checker})
        return count

    def admission_denial(self, focus, now):
        return "cognition-paused" if self._native_meta()["paused"] else None



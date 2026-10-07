"""Weighted service for the existing goal inventory.

Definitions stay in goals.metta; this module attaches durable scheduling
metadata to those same names in the protected strategy checkpoint. Selection
and charging refine FairGoalScheduling/FairGoalStrategy in MeTTapedia. A unit
is an admitted provider attempt, not completion, tokens, or elapsed CPU time.
"""

import json
import re

from observed_strategy import digest, encoded


POLICY = re.compile(r'^\(goal-policy ([^\s()]+) share:(\d+) every:(once|\d+) urgency:(\d+) checker:(operator|inventory-review)\)$')


def definitions(path):
    import goals
    lines = goals._read_lines(str(path))
    if lines is None:
        raise ValueError("goal inventory is unavailable")
    records, _flags, _budget, other = goals._parse(lines)
    policies = {}
    for line in other:
        if not line.strip().startswith("(goal-policy"):
            continue
        match = POLICY.fullmatch(line.strip())
        if not match:
            raise ValueError("malformed goal-policy")
        name, share, interval, urgency, checker = match.groups()
        if name in policies:
            raise ValueError("duplicate goal-policy")
        policies[name] = dict(share=int(share), interval=None if interval == "once" else int(interval),
                              baseUrgency=int(urgency), checker=checker)
    result = []
    for record in records:
        # Self-reported verification and affect are not completion evidence.
        definition = {k: record[k] for k in ("name", "area", "note", "blocked")}
        definition.update(policies.get(record["name"], dict(
            share=1, interval=None, baseUrgency=0, checker="operator")))
        result.append(definition)
    return result


def runnable(goal):
    return (not goal["blocked"] and not goal["finished"] and goal["share"] > 0
            and (goal["interval"] is None or goal["age"] >= goal["interval"]))


def select(goals):
    best = None
    for goal in goals:
        if not runnable(goal):
            continue
        if best is None:
            best = goal
            continue
        left = goal["service"] * best["share"]
        right = best["service"] * goal["share"]
        if left < right or (left == right and goal["baseUrgency"] + goal["age"] > best["baseUrgency"] + best["age"]):
            best = goal
    return best


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS goal_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, state TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS goal_reviews (digest TEXT PRIMARY KEY, ticket TEXT NOT NULL, inventory TEXT NOT NULL, entries TEXT NOT NULL)")


def accounts(db, now):
    result = []
    for row in db.execute("SELECT * FROM goal_accounts ORDER BY id"):
        state = json.loads(row["state"])
        elapsed = max(0, int(now) - state["clock"])
        if not state["finished"]:
            state["age"] += elapsed
        state["clock"] = max(state["clock"], int(now))
        result.append(dict(state, id=row["id"], name=row["name"]))
    return result


def save(db, goal):
    db.execute("UPDATE goal_accounts SET state=? WHERE id=?", (encoded(
        {k: v for k, v in goal.items() if k not in ("id", "name")}), goal["id"]))


def inventory(goals):
    return [{"name": g["name"], **g["definition"]} for g in goals if g["present"]]


def synchronize(store, definitions, now):
    """No accounting reset on edits, removal/readdition, restart or reweighting."""
    names = [d["name"] for d in definitions]
    if len(names) != len(set(names)):
        raise ValueError("goal IDs must be unique")
    with store.transaction() as db:
        schema(db)
        old = accounts(db, now)
        by_name = {g["name"]: g for g in old}
        floor = select(old)
        for definition in definitions:
            name = definition["name"]
            goal = by_name.get(name)
            if goal is None:
                share = definition["share"]
                service = (floor["service"] * share + floor["share"] - 1) // floor["share"] if floor else 0
                state = dict(share=share, service=service, age=definition["interval"] or 0,
                             round=0, finished=False, clock=int(now), present=True)
                cursor = db.execute("INSERT INTO goal_accounts(name,state) VALUES (?,?)", (name, encoded(state)))
                goal = dict(state, id=cursor.lastrowid, name=name)
                store.event(db, "goal-admit", goal=goal["id"], service=service, share=share)
            elif goal["share"] != definition["share"]:
                # Preserve virtual service on an explicit policy change. This
                # is a new stable-share epoch, outside the fixed-cohort proof.
                if goal["share"] > 0:
                    goal["service"] = (goal["service"] * definition["share"] + goal["share"] - 1) // goal["share"] if definition["share"] else goal["service"]
                goal["share"] = definition["share"]
                store.event(db, "goal-reweight", goal=goal["id"], service=goal["service"], share=goal["share"])
            goal.update(definition=definition, present=True, blocked=definition["blocked"] != "none" or goal.get("cancelled", False),
                        interval=definition["interval"], baseUrgency=definition["baseUrgency"])
            save(db, goal)
        for goal in old:
            if goal["name"] not in names:
                goal.update(present=False, blocked=True)
                save(db, goal)


def snapshot(store, now):
    with store.transaction() as db:
        schema(db)
        goals = accounts(db, now)
        chosen = select(goals)
        return {"inventory": inventory(goals), "inventory_digest": digest(inventory(goals)),
                "selected": chosen,
                "queue": [{k: g[k] for k in ("id", "name", "share", "service", "age", "round", "blocked", "finished", "interval", "baseUrgency")} for g in goals if g["present"]]}


def cycle_key(owner, goal):
    return "goal:%s:%s:%s" % (owner, goal["id"], goal["round"])


def accept_selected(store, view):
    goal = view["selected"]
    if goal is None:
        return None
    with store.transaction() as db:
        key = cycle_key(store.get(db, "owner"), goal)
    checker = "inventory-review" if goal["definition"]["checker"] == "inventory-review" else "operator"
    store.accept(key, "Goal %s, cycle %s. Respect its current constraints; selection does not authorize new work." % (goal["name"], goal["round"]), checker)
    return key


def charge_capture(store, db, lease, focus, serial, now):
    """Called only inside Ledger.capture's successful checkpoint transaction."""
    goals = accounts(db, now)
    goal = select(goals)
    if (goal is None or goal["id"] != lease["id"] or goal["round"] != lease["round"]
            or goal["service"] != lease["service"]
            or digest(inventory(goals)) != lease["inventory_digest"]
            or cycle_key(store.get(db, "owner"), goal) != focus):
        return None
    before = goal["service"]
    goal["service"] += 1
    save(db, goal)
    # The numeric projection can be replayed by Lean without private names.
    queue = [{k: g[k] for k in ("id", "share", "service", "age", "round", "blocked", "finished", "interval", "baseUrgency")} for g in goals]
    next(g for g in queue if g["id"] == goal["id"])["service"] = before
    store.event(db, "goal-charge", goal=goal["id"], cycle=goal["round"], serial=serial,
                before=before, after=goal["service"], share=goal["share"], queue=queue)
    return dict(id=goal["id"], round=goal["round"], service=goal["service"], share=goal["share"])


def completed(store, db, request, now):
    """The existing observed-receipt checker has already accepted completion."""
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='goal_accounts'").fetchone()
    if not exists:
        return
    for goal in accounts(db, now):
        if cycle_key(store.get(db, "owner"), goal) == request:
            goal.update(age=0, round=goal["round"] + 1, finished=goal["interval"] is None)
            save(db, goal)
            store.event(db, "goal-complete", goal=goal["id"], round=goal["round"], service=goal["service"])
            return


def may_complete(store, db, request, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='goal_accounts'").fetchone():
        return True
    for goal in accounts(db, now):
        if cycle_key(store.get(db, "owner"), goal) == request:
            return not goal["blocked"] and not goal["finished"]
    return not request.startswith("goal:" + store.get(db, "owner") + ":")


def cancelled(store, db, request, now):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='goal_accounts'").fetchone():
        return
    for goal in accounts(db, now):
        if cycle_key(store.get(db, "owner"), goal) == request:
            goal.update(blocked=True, cancelled=True)
            save(db, goal)
            store.event(db, "goal-cancel", goal=goal["id"], service=goal["service"])
            return


def save_review(store, ticket, inventory_digest, entries, now):
    """Record coverage of the inventory, not truth of the written assessments."""
    if isinstance(entries, str):
        entries = json.loads(entries)
    with store.transaction() as db:
        goals = accounts(db, now)
        current = inventory(goals)
        if inventory_digest != digest(current):
            raise ValueError("inventory changed; read goals again")
        if not isinstance(entries, list) or len(entries) != len(current):
            raise ValueError("review must cover every current goal exactly once")
        if {e.get("goal") for e in entries if isinstance(e, dict)} != {g["name"] for g in current}:
            raise ValueError("review goal names do not match inventory")
        for entry in entries:
            if entry.get("disposition") not in ("keep", "defer", "propose-close"):
                raise ValueError("review disposition must be keep, defer or propose-close")
            for key in ("assessment", "evidence", "next_action"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError("each review entry needs assessment, evidence and next_action")
        action = db.execute("SELECT request FROM actions WHERE ticket=? AND status='issued'", (ticket,)).fetchone()
        if not action:
            raise ValueError("review needs an issued, correlated action")
        goal = next((g for g in goals if cycle_key(store.get(db, "owner"), g) == action[0]), None)
        if goal is None or goal["definition"]["checker"] != "inventory-review":
            raise ValueError("current goal does not request an inventory review")
        identity = digest([ticket, current, entries])
        db.execute("INSERT OR IGNORE INTO goal_reviews VALUES (?,?,?,?)", (identity, ticket, encoded(current), encoded(entries)))
        return "GOAL_REVIEW_SAVED " + identity


def finish_reviews(store, now):
    with store.transaction() as db:
        schema(db)
        current = encoded(inventory(accounts(db, now)))
        candidates = list(db.execute("SELECT a.request,a.ticket,r.digest FROM actions a JOIN goal_reviews r ON r.ticket=a.ticket JOIN requests q ON q.id=a.request WHERE a.observed=1 AND a.receipt IS NOT NULL AND q.status='active' AND q.checker='inventory-review' AND r.inventory=?", (current,)))
    for row in candidates:
        marker = "GOAL_REVIEW_SAVED " + row["digest"]
        checker = lambda work, receipt, marker=marker: marker in receipt["payload"]
        if store.attest(row["request"], row["ticket"], checker):
            store.finish(row["request"], row["ticket"], {"inventory-review": checker}, now=now)

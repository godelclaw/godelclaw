#!/usr/bin/env python3
"""Export saved scheduling decisions for replay by FairGoalScheduling.lean.

Only numeric goal IDs and scheduler fields enter the Lean artifact. Python
checks correspondence with provider samples and durable service accounts.
Lean checks exact selection and charging independently of Python's selector.
"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def read(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        return {table: [dict(r) for r in db.execute("SELECT * FROM " + table + " ORDER BY rowid")]
                for table in ("goal_accounts", "samples", "events")}


def natural(value):
    if type(value) is not int or value < 0:
        raise ValueError("invalid natural number")
    return str(value)


def lean_goal(goal):
    fields = [k + " := " + natural(goal[k]) for k in ("id", "share", "service", "age", "round", "baseUrgency")]
    for k in ("blocked", "finished"):
        if type(goal[k]) is not bool:
            raise ValueError("invalid Boolean")
        fields.append(k + " := " + str(goal[k]).lower())
    interval = goal["interval"]
    fields.append("recurrence := " + (".once" if interval is None else ".every " + natural(interval)))
    return "{ " + ", ".join(fields) + " }"


def export(view):
    samples = {r["serial"]: json.loads(r["snapshot"]) for r in view["samples"]}
    services, charges = {}, {}
    source = ["import FairGoalScheduling", "open GoalSystem FairGoalScheduling",
              "set_option maxRecDepth 16384", "set_option maxHeartbeats 4000000"]
    for row in view["events"]:
        event = json.loads(row["event"])
        kind = event["kind"]
        if kind in ("goal-admit", "goal-reweight"):
            id = event["goal"]
            natural(event["service"]); natural(event["share"])
            if kind == "goal-admit" and id in services:
                raise ValueError("duplicate goal admission")
            services[id] = event["service"]
        elif kind in ("goal-complete", "goal-cancel"):
            if services.get(event["goal"]) != event["service"]:
                raise ValueError("completion changed service")
        elif kind == "goal-charge":
            id, serial = event["goal"], event["serial"]
            if serial in charges or serial not in samples:
                raise ValueError("duplicate or uncaptured charge")
            if services.get(id) != event["before"] or event["after"] != event["before"] + 1:
                raise ValueError("service accounting discontinuity")
            expected = dict(id=id, round=event["cycle"], service=event["after"], share=event["share"])
            if samples[serial].get("goal") != expected:
                raise ValueError("charge differs from provider capture")
            queue = event["queue"]
            if len({g["id"] for g in queue}) != len(queue):
                raise ValueError("duplicate queue ID")
            if {g["id"]: g["service"] for g in queue} != services:
                raise ValueError("decision queue differs from service history")
            chosen = next(g for g in queue if g["id"] == id)
            index = len(charges)
            source += [f"def queue{index} : List Goal := [" + ", ".join(lean_goal(g) for g in queue) + "]",
                       f"example : (FairGoalScheduling.select queue{index}).map Goal.id = some {natural(id)} := by decide",
                       f"example : chargeGoal {natural(id)} 1 {lean_goal(chosen)} = " + lean_goal(dict(chosen, service=event["after"])) + " := by decide"]
            services[id] = event["after"]
            charges[serial] = id
    if set(charges) != {s for s, snap in samples.items() if "goal" in snap}:
        raise ValueError("captured goal has no charge")
    actual = {r["id"]: json.loads(r["state"])["service"] for r in view["goal_accounts"]}
    if actual != services:
        raise ValueError("saved accounts differ from history")
    return "\n".join(source) + "\n", dict(charges=len(charges), accounts=len(services),
                                           correspondence="pass", lean_replay="pending")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--lean-output", type=Path, required=True)
    args = parser.parse_args()
    source, result = export(read(args.ledger))
    args.lean_output.write_text(source)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

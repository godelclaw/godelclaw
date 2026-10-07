"""Offline replies for the real goal-review command path."""
import json
from pathlib import Path


def setup():
    import goals
    Path(goals._goals_path()).write_text(
        '(goal research Work sti:0.5 lti:0.5 vibes:() blocked-by:none last-verified:never note:"Check evidence")\n'
        '(goal review Work sti:0.5 lti:0.5 vibes:() blocked-by:none last-verified:never note:"Review constraints")\n'
        '(goal-policy review share:1 every:86400 urgency:1000 checker:inventory-review)\n')


def reply(*args):
    import strategy_host as host
    import llm_policy
    import cognitive_health
    ticket = llm_policy.admit("fixture", 64)
    llm_policy.complete(ticket, {"choices": [{"finish_reason": "stop"}], "usage": {"completion_tokens": 1}})
    cognitive_health.turn_started("fixture")
    cognitive_health.turn_completed(8)
    view = host._goal_view
    if view["selected"]["definition"]["checker"] == "inventory-review":
        entries = [dict(goal=g["name"], disposition="keep", assessment="Current fixture goal",
                        evidence="Inventory note; no external claim", next_action="Read current evidence")
                   for g in view["inventory"]]
        return "((goal-review-save " + json.dumps(view["inventory_digest"]) + " " + json.dumps(entries) + "))"
    return "((help shell))"


def check():
    import strategy_host as host
    queue = host.goal_snapshot()["queue"]
    assert [g["service"] for g in queue] == [2, 1], queue
    assert queue[1]["round"] == 1 and not queue[1]["finished"], queue
    assert len(host.ledger().projection()["actions"]) == 3
    assert sum(r["status"] == "achieved" for r in host.ledger().projection()["requests"]) == 1
    print("FAIR_GOAL_REVIEW_OK")
    return 1

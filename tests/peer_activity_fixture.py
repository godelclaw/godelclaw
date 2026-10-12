"""Controlled peer admission for the real MeTTa life-policy boundary."""
from types import SimpleNamespace
import os
import mailbox_agent
import telegram

_calls = 0


def arm():
    mailbox_agent._runtime = SimpleNamespace(instance="lila", peer_offered={"inbox-a"}, peer_turn_budget=2,
                                             close=lambda: None)
    telegram._current_batch_update_ids = {"mailbox:lila:inbox-a"}
    return 1


def unrelated():
    telegram._current_batch_update_ids = {"123"}
    return 1


def clear():
    mailbox_agent._runtime = None
    telegram._current_batch_update_ids = set()
    return 1


def setup():
    import cognitive_health
    import helper
    import strategy_host
    import synthetic_llm
    global _calls
    _calls = 0
    os.environ["METTACLAW_INSTANCE"] = "lila"
    os.environ["METTACLAW_HEARTBEAT_INTERVAL_SECONDS"] = "0"
    helper._working_boot_cache = {}
    helper.working_boot = lambda: 3
    strategy_host.guard = lambda **kw: "peer-fixture-control"
    telegram._metta_unread_chats = lambda: set()
    def chat(*args):
        global _calls
        _calls += 1
        cognitive_health.turn_started("peer-fixture")
        cognitive_health.turn_completed(10)
        return "((help mailbox-send))"
    synthetic_llm.chat = chat
    arm()
    telegram.enqueue_mailbox_event('received peer note', 'mailbox:lila:inbox-a', 'godel')
    return 1


def check():
    assert _calls == 1, "an admitted peer must resume an idle loop exactly once"
    return 1

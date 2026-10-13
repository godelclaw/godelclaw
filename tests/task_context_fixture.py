"""A deterministic provider driving the actual task/context boundary."""
import json
import os
from pathlib import Path
import tempfile
import strategy_host
import synthetic_llm
import cognitive_health
import telegram
import helper
import task_context

_calls = 0


def setup():
    global _directory, _calls
    _calls = 0
    _directory = tempfile.TemporaryDirectory()
    path = Path(_directory.name)
    (path / "source.txt").write_text("EARLY_FINDING: this API requires finite evidence.")
    helper._working_boot_cache = {}
    helper.working_boot = lambda: 8
    strategy_host.guard = lambda **kw: "task-fixture-control"
    strategy_host._stimulus_free = lambda: True
    telegram._metta_unread_chats = lambda: set()
    def chat(*args):
        global _calls
        prompt = str(args[-1])
        # The transport adapters can place the request before extra options.
        prompt = " ".join(str(a) for a in args)
        _calls += 1
        cognitive_health.turn_started("task-fixture")
        cognitive_health.turn_completed(10)
        if _calls == 1:
            commands = [["read-file", str(path/"source.txt")]] * 13
            commands += [["read-file",str(path/"missing")],["read-file",str(path/"source.txt")],["write-file",str(path/"must-not-exist"),"bad"]]
        elif _calls == 2:
            assert "EARLY_FINDING" in prompt and "not_run" in prompt
            assert not (path/"must-not-exist").exists()
            commands = [["task-note","The source requires finite evidence; the later read failed."]]
        else:
            assert "EARLY_FINDING" in prompt, "earlier evidence was lost"
            assert "requires finite evidence; the later read failed" in prompt
            commands = [["task-history",strategy_host._focus]]
        return "(" + " ".join("(" + c[0] + " " + " ".join(json.dumps(x) for x in c[1:]) + ")" for c in commands) + ")"
    synthetic_llm.chat = chat
    return 1


def check():
    assert _calls == 3
    view = task_context.projection(strategy_host.ledger(),strategy_host._focus)
    assert len(view["transcript"]) == 18, len(view["transcript"])
    assert view["transcript"][14]["disposition"] == "not_run"
    assert len(view["pending"]) == 2
    return 1

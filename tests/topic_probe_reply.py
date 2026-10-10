"""Scripted foreground decisions for the real concurrent-worker probe."""
import json
import os
import time
from urllib.parse import urlparse
import cognitive_health
import strategy_host
import topic_jobs

calls = 0
job = None


def setup():
    origin = os.environ["METTACLAW_TOPIC_TEST_ORIGIN"]
    parsed = urlparse(origin)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
    assert not parsed.username and not parsed.password
    # Launchers may load an instance's provider defaults. This offline probe
    # explicitly binds its child workers to the local fixture after loading.
    os.environ.update(SYNTHETIC_BASE_URL=origin, SYNTHETIC_MODEL="fixture-model",
                      SYNTHETIC_API_KEY="fixture-secret", SYNTHETIC_TIMEOUT="10",
                      METTACLAW_LLM_STREAM="0", METTACLAW_LLM_SOFT_SECONDS="10",
                      METTACLAW_LLM_HARD_SECONDS="20", METTACLAW_LLM_CONNECT_SECONDS="2",
                      METTACLAW_LLM_FIRST_BYTE_SECONDS="10", METTACLAW_LLM_IDLE_SECONDS="10")
    return 1


def reply(*args):
    global calls, job
    calls += 1
    cognitive_health.turn_started("probe")
    cognitive_health.turn_completed(10)
    if calls == 1:
        return '((background-model "research" "fast probe finding"))'
    if calls == 2:
        store = topic_jobs._store()
        with store.transaction() as db:
            row = db.execute("SELECT id FROM model_jobs ORDER BY rowid DESC LIMIT 1").fetchone()
            assert row, "MeTTa did not start the model job"
            job = row[0]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            evidence = json.loads(topic_jobs.results(job))
            if evidence["state"] not in ("queued", "running"):
                assert evidence["state"] == "returned", evidence
                return '((model-result "' + job + '"))'
            time.sleep(.02)
        raise AssertionError("native worker did not settle")
    if calls == 3:
        # This must be established by the actual protected receipt projection,
        # before the foreground provider gets its next decision.
        with topic_jobs._store().transaction() as db:
            assert db.execute("SELECT observed FROM model_jobs WHERE id=?", (job,)).fetchone()[0]
        return '((model-adopt "' + job + '"))'
    raise AssertionError("unexpected extra foreground call")


def verified():
    with topic_jobs._store().transaction() as db:
        row = db.execute("SELECT topic,observed,adopted FROM model_jobs WHERE id=?", (job,)).fetchone()
        notes = db.execute("SELECT notes FROM topic_contexts WHERE name='research'").fetchone()[0]
    receipts = json.loads(strategy_host.command_receipts())
    assert calls == 3 and tuple(row) == ("research", 1, 1)
    assert "fast probe finding" in notes
    assert [r["command"][0] for r in receipts] == ["model-adopt", "model-result", "background-model"], receipts
    return 1

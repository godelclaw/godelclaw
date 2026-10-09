"""Scripted foreground decisions for the real concurrent-worker probe."""
import json
import time
import cognitive_health
import strategy_host
import topic_jobs

calls = 0
job = None


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

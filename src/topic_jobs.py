"""Agent-owned topic notes and read access to historical model results."""
import re
import observed_strategy as strategy

def _native():
    """With the native ledger the topics and the historical jobs are its records (src/input_ledger.metta)."""
    import input_ack
    return bool(input_ack._native_socket())


def _call(verb, arguments=()):
    import input_ack
    return input_ack._ledger_call(verb, tuple(arguments))


def _nonce():
    import uuid
    return uuid.uuid4().hex


def _native_topics():
    from execution_gateway.cetta_worker import S
    answer = _call("topics")
    if not (isinstance(answer, tuple) and len(answer) == 3 and answer[0] == S("ledger:topics")):
        raise RuntimeError("input ledger topics: unexpected answer %r" % (answer,))
    return answer[1], [dict(name=row[0], revision=row[1]) for row in answer[2]]


def _name(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("topic name must be 1-64 letters, digits, dots, underscores or hyphens")
    return value


def select(name):
    name = _name(name)
    _call("topic-select", (name, _nonce()))
    return "active topic: " + name


def note(text):
    text = str(text)
    name = _call("topic-note", (text, _nonce()))
    return "saved topic notes: " + (name if isinstance(name, str) else _native_topics()[0])


def topics():
    active, rows = _native_topics()
    return strategy.encoded({"active": active, "topics": rows})


def context():
    row = _call("topic-context")
    return ("TOPIC_NOTES " + strategy.encoded(dict(name=row[0], notes=row[1]))
            if isinstance(row, tuple) and len(row) == 2 and row[1] else "")


def results(job):
    row = _call("model-job", (str(job),))
    return (strategy.encoded(dict(id=row[0], topic=row[1], state=row[2], result=row[3]))
            if isinstance(row, tuple) and len(row) == 4 else "unknown historical model job")


def status():
    return strategy.encoded([dict(id=row[0], topic=row[1], state=row[2]) for row in _call("model-jobs")])


def notify(store=None):
    return 0


def foreground_revision():
    active, rows = _native_topics()
    return next(([row["name"], row["revision"]] for row in rows if row["name"] == active), None)

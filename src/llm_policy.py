"""Optional operator admission policy shared by every cognitive mode.

Upstream profiles do not add pacing, caps, or retries. Managed profiles use
operator-supplied caps; zero means disabled. No mode's energy/fuel state is
rewritten here. The durable ledger contains metadata only, never prompts.
"""
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class AdmissionDenied(RuntimeError):
    def __init__(self, reason, retry_after):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(0, float(retry_after))


def active_mode():
    return os.environ.get("METTACLAW_ACTIVE_LOOP_MODE", "godelclaw")


def configuration(mode=None):
    mode = mode or active_mode()
    path = os.environ.get("METTACLAW_LLM_POLICY_PATH")
    config = {}
    if path:
        # A configured but broken policy is an operator error, not permission
        # to silently remove a cap.
        config = json.loads(Path(path).read_text())
        if not isinstance(config, dict):
            raise ValueError("LLM policy must be an object")
    default = "upstream" if mode in ("iter", "omega") else "managed"
    profile = os.environ.get("METTACLAW_LLM_POLICY", config.get("profile", default))
    overrides = config.get("modes", {}).get(mode, {})
    profile = overrides.get("profile", profile)
    if profile not in ("upstream", "managed"):
        raise ValueError("LLM profile must be upstream or managed")
    result = {"profile": profile}
    fields = {"requests_per_hour": 0, "output_tokens_per_day": 0,
              "failures_per_window": 0, "failure_window_seconds": 300,
              "quota_cooldown_seconds": 300}
    for name, default_value in fields.items():
        raw = os.environ.get("METTACLAW_LLM_" + name.upper(),
                             overrides.get(name, config.get(name, default_value)))
        value = int(raw)
        if value < 0:
            raise ValueError("LLM policy limits must be nonnegative")
        result[name] = value
    return result


def ledger_path():
    explicit = os.environ.get("METTACLAW_LLM_LEDGER_PATH")
    if explicit:
        return Path(explicit)
    health = os.environ.get("METTACLAW_COGNITIVE_HEALTH_PATH")
    if health:
        return Path(health).with_name("llm-budget.sqlite3")
    home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return home / os.environ.get("METTACLAW_INSTANCE", "default") / "llm-budget.sqlite3"


@contextlib.contextmanager
def transaction():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, started REAL NOT NULL, "
                     "reserved INTEGER NOT NULL, status TEXT NOT NULL, output INTEGER, "
                     "provider TEXT NOT NULL, mode TEXT NOT NULL, finish TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS cooldowns (provider TEXT PRIMARY KEY, until REAL NOT NULL)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def admit(provider, max_output, *, now=None, mode=None):
    now = time.time() if now is None else float(now)
    mode = mode or active_mode()
    policy = configuration(mode)
    maximum = int(max_output)
    if maximum <= 0:
        raise ValueError("max output tokens must be positive")
    ticket = uuid.uuid4().hex
    with transaction() as conn:
        if policy["profile"] == "managed":
            cooldown = conn.execute("SELECT until FROM cooldowns WHERE provider=?", (provider,)).fetchone()
            if cooldown and cooldown[0] > now:
                raise AdmissionDenied("provider-cooldown", cooldown[0] - now)
            cap = policy["requests_per_hour"]
            rows = conn.execute("SELECT started FROM requests WHERE started>? ORDER BY started", (now - 3600,)).fetchall()
            if cap and len(rows) >= cap:
                raise AdmissionDenied("request-budget", rows[len(rows)-cap][0] + 3600 - now)
            day = int(now // 86400) * 86400
            used = conn.execute("SELECT COALESCE(SUM(COALESCE(output,reserved)),0) FROM requests WHERE started>=?", (day,)).fetchone()[0]
            cap = policy["output_tokens_per_day"]
            if cap and used + maximum > cap:
                raise AdmissionDenied("output-token-budget", day + 86400 - now)
            cap = policy["failures_per_window"]
            span = policy["failure_window_seconds"]
            failed = conn.execute("SELECT started FROM requests WHERE provider=? AND status='failed' AND started>? ORDER BY started", (provider, now-span)).fetchall()
            if cap and len(failed) >= cap:
                raise AdmissionDenied("retry-budget", failed[len(failed)-cap][0] + span - now)
        conn.execute("INSERT INTO requests VALUES(?,?,?,'pending',NULL,?,?,NULL)",
                     (ticket, now, maximum, provider, mode))
        # Expired accounting windows no longer own admission authority.
        conn.execute("DELETE FROM requests WHERE started<?", (now-172800,))
    # A managed goal is charged at the actual admission boundary, before a
    # provider can run. Strict upstream callers have no prepared host capture.
    try:
        import strategy_host
        strategy_host.provider_admitted()
    except BaseException:
        fail(ticket, sent=False, now=now)
        raise
    return ticket


def complete(ticket, result):
    usage = result.get("usage", {})
    output = usage.get("completion_tokens", usage.get("output_tokens"))
    if output is not None:
        output = max(0, int(output))
    finish = str(result["choices"][0].get("finish_reason", "unknown"))[:64]
    with transaction() as conn:
        changed = conn.execute("UPDATE requests SET status='completed',output=?,finish=? WHERE id=? AND status='pending'",
                               (output, finish, ticket)).rowcount
    return changed == 1


def fail(ticket, *, status=None, retry_after=None, sent=True, now=None):
    now = time.time() if now is None else float(now)
    with transaction() as conn:
        row = conn.execute("SELECT provider,mode FROM requests WHERE id=? AND status='pending'", (ticket,)).fetchone()
        if not row:
            return False
        # A sent request without authoritative usage keeps its reservation:
        # a network timeout cannot prove the provider did no billable work.
        conn.execute("UPDATE requests SET status='failed',output=? WHERE id=?",
                     (None if sent else 0, ticket))
        policy = configuration(row[1])
        if policy["profile"] == "managed" and status in (402, 429):
            delay = policy["quota_cooldown_seconds"]
            if retry_after is not None:
                delay = max(delay, min(86400, max(0, float(retry_after))))
            until = now + delay
            conn.execute("INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET until=MAX(until,excluded.until)", (row[0], until))
    return True


def snapshot():
    with transaction() as conn:
        counts = dict(conn.execute("SELECT status,COUNT(*) FROM requests GROUP BY status"))
        output = conn.execute("SELECT COALESCE(SUM(COALESCE(output,reserved)),0) FROM requests").fetchone()[0]
    return {"policy": configuration(), "requests": counts, "accounted_output_tokens": output}

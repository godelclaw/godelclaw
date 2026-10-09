"""Host plan for a read-only CeTTa worker; no Python HTTP or tool dispatch."""
import json
import os
from types import SimpleNamespace
import agent_checkpoint
import llm_policy
import provider_protocol
import synthetic_llm
import topic_jobs


def prepare():
    job = os.environ["METTACLAW_TOPIC_WORKER_ID"]
    ticket = None
    try:
        store = agent_checkpoint.ledger()
        with store.transaction() as db:
            row = db.execute("SELECT * FROM model_jobs WHERE id=?", (job,)).fetchone()
            if not row or row["state"] != "queued":
                raise RuntimeError("no pending owned model job")
            snapshot = json.loads(row["snapshot"])
            question = row["question"]
        selected = synthetic_llm.current_model()
        provider = synthetic_llm._provider_for(selected)
        if not provider["key"]:
            raise RuntimeError("provider is not configured")
        tokens = max(1, min(4096, int(os.environ.get("METTACLAW_BACKGROUND_TOKENS", "1024"))))
        prompt = ("You are a read-only background strand of the same agent. Return findings, evidence and uncertainties as text. "
                  "You cannot call tools, send messages, finish tasks or alter notes. Foreground review decides adoption.\n"
                  "CAPTURED CONTEXT " + json.dumps({"topic": row["topic"], "revision": row["revision"],
                   "notes": snapshot["notes"], "dependencies": snapshot["dependencies"],
                   "other_strands_at_capture": snapshot.get("peers", [])}, ensure_ascii=False) +
                  "_cache_boundary_" + question)
        endpoint, body = provider_protocol.command_body(provider["protocol"], provider.get("model", selected), tokens, "low", prompt)
        ticket = llm_policy.admit(provider["name"], tokens)
        alias = "TOPIC_LLM_" + provider["name"].upper()
        prefix = "CETTA_IO_AUTH_" + alias
        os.environ[prefix + "_URL_PREFIX"] = provider["base"] + "/"
        os.environ[prefix + "_HEADER"] = "x-api-key" if provider["name"] == "anthropic" else "Authorization"
        os.environ[prefix + "_ENV"] = {"synthetic": "SYNTHETIC_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[provider["name"]]
        os.environ[prefix + "_PREFIX"] = "" if provider["name"] == "anthropic" else "Bearer "
        headers = [["http:header", "Content-Type", "application/json"], ["http:credential", alias]]
        if provider["name"] == "anthropic":
            headers.append(["http:header", "anthropic-version", "2023-06-01"])
        return SimpleNamespace(ready=1, job=job, ticket=ticket, sent=False,
            protocol=provider["protocol"], url=provider["base"] + endpoint, headers=headers,
            body=json.dumps(body, ensure_ascii=False), timeout=int(float(os.environ.get("SYNTHETIC_TIMEOUT", "300"))*1000), cap=65536)
    except Exception as error:
        if ticket:
            llm_policy.fail(ticket, sent=False)
        topic_jobs.record(job, "failed", "provider admission failed: " + type(error).__name__)
        return SimpleNamespace(ready=0)


def field(plan, name):
    return getattr(plan, str(name))


def submitted(plan):
    plan.sent = True
    return 1


def finish(plan, status, body):
    try:
        status = int(status)
        if not 200 <= status < 300:
            llm_policy.fail(plan.ticket, status=status, sent=True)
            return topic_jobs.record(plan.job, "failed", "http-status " + str(status))
        result = provider_protocol.normalize_response(plan.protocol, json.loads(str(body)))
        if not llm_policy.complete(plan.ticket, result):
            return topic_jobs.record(plan.job, "failed", "duplicate provider completion")
        choice = result["choices"][0]
        text = choice["message"].get("content")
        if choice.get("finish_reason") != "stop" or choice["message"].get("tool_calls") or not isinstance(text, str) or not text.strip():
            return topic_jobs.record(plan.job, "failed", "provider did not return complete read-only text")
        return topic_jobs.record(plan.job, "returned", text)
    except Exception as error:
        return failed(plan, "decode-error:" + type(error).__name__)


def failed(plan, reason):
    llm_policy.fail(plan.ticket, sent=plan.sent)
    return topic_jobs.record(plan.job, "failed", str(reason)[:128])

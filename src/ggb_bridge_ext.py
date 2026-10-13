import datetime
import json
import os
import subprocess




def _python_executable():
    # Under embedded PeTTa, sys.executable is SWI-Prolog, not Python.  Resolve
    # Python explicitly; run.sh puts the configured PeTTa environment first on
    # PATH, and operators may pin an absolute interpreter through the env.
    return os.environ.get("METTACLAW_PYTHON_EXECUTABLE", "python3")

EV_CLI = os.environ.get("METTACLAW_GGB_EVIDENCE_CLI", "")

def ggbL3Share(agent, content, key, strength, confidence, goal, timestamp, origin):
    try:
        if not EV_CLI:
            return 'error'
        r = subprocess.run([_python_executable(), EV_CLI, 'add', str(agent), str(content), str(key),
                           str(strength), str(confidence), str(goal), str(timestamp), str(origin)],
                          capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout.strip())
        return data.get('id', 'error')
    except Exception:
        return 'error'

def ggbL3Query(query_str, limit=5):
    try:
        if not EV_CLI:
            return []
        r = subprocess.run([_python_executable(), EV_CLI, 'query', str(query_str), str(limit)],
                          capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout.strip())
        return data.get('results', [])
    except Exception:
        return []

def ggbL3Revise(agent):
    try:
        if not EV_CLI:
            return 'error'
        r = subprocess.run([_python_executable(), EV_CLI, 'by-agent', str(agent)],
                          capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout.strip())
        return data.get('status', 'error')
    except Exception:
        return 'error'

PEER_CLI = os.environ.get("METTACLAW_GGB_PEER_CLI", "")

def ggbSendToPeer(peer, sender, subject, body):
    try:
        if not PEER_CLI:
            return 'error'
        r = subprocess.run([_python_executable(), PEER_CLI, 'send', str(peer), str(sender), str(subject), str(body)],
                          capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout.strip())
        return data.get('status', 'error')
    except Exception:
        return 'error'


def ggbTick(agent, iteration, results_repr):
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    results = str(results_repr)[:200] if results_repr else "no-results"
    summary = "iteration=%s results=%s" % (iteration, results)
    ev_id = ggbL3Share(
        agent, summary, "tick", 0.5, 0.7, "ggb-tick", timestamp, "self"
    )
    return "tick:" + str(ev_id)


def ggbNow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def nalRevisionF(f1, c1, f2, c2):
    f1, c1, f2, c2 = float(f1), float(c1), float(f2), float(c2)
    w1 = c1 / (1 - c1) if c1 < 1.0 else 1e10
    w2 = c2 / (1 - c2) if c2 < 1.0 else 1e10
    W = w1 + w2
    return (w1 * f1 + w2 * f2) / W

def nalRevisionC(f1, c1, f2, c2):
    f1, c1, f2, c2 = float(f1), float(c1), float(f2), float(c2)
    w1 = c1 / (1 - c1) if c1 < 1.0 else 1e10
    w2 = c2 / (1 - c2) if c2 < 1.0 else 1e10
    W = w1 + w2
    return W / (W + 1.0)


def ggbConsensusTick(agent, iteration):
    return "Proposal promotion has been removed; write changes directly."

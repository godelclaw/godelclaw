"""Autonomic goal-stack bookkeeping over the instance's inventory.

Pure bookkeeping over memory/goals.metta — no LLM tokens. The kernel runs
at wake events (boot, human-armed turn, heartbeat re-arm), NOT every loop
iteration, so a 50-turn burst counts as one wake for decay purposes.

File atoms (one per line; kernel-owned atoms marked *):
  (goal-budget 3.0)
  (goal <name> <area> sti:<0-1> lti:<0-1> vibes:(<v>*)
        blocked-by:<none|token|"text"> last-verified:<never|token> note:"<text>")
  (goal-flag <name> <stuck|saturated|cull-review>)   *
  (mood Cn:x C:x Ct:x I:x J:x A:x S:x Co:x Sp:x)     *

Streak counters and the outbound-log offset live in memory/goals_kernel.json;
dropped goals are appended to memory/goals_compost.log (nothing is ever lost).
"""

import json
import hashlib
import os
import re
import threading
import time

_LOCK = threading.Lock()
_runtime_human_wake = None

DECAY = 0.95            # STI backstop decay per wake
ALPHA = 0.05            # LTI consolidation rate
GAMMA = 0.69            # affect-gestalt EMA retention per affect sample
STUCK_WAKES = 3         # unchanged blocked-by across this many wakes -> stuck
CULL_STI = 0.05         # sti below this AND lti below CULL_LTI -> cull-review
CULL_LTI = 0.1
DEFAULT_BUDGET = 3.0
MOOD_DIMS = ("Cn", "C", "Ct", "I", "J", "A", "S", "Co", "Sp")

# \s* after each field colon: a stray space must not reject the goal
# (cost the agent six live turns of misdiagnosis on 2026-07-18).
_NUMBER = r'-?(?:\d+(?:\.\d*)?|\.\d+)'
_GOAL_RE = re.compile(
    r'^\(goal\s+(?P<name>[^\s()"]+)\s+(?P<area>[^\s()"]+)'
    rf'\s+sti:\s*(?P<sti>{_NUMBER})\s+lti:\s*(?P<lti>{_NUMBER})'
    r'\s+vibes:\s*\((?P<vibes>[^()]*)\)'
    r'\s+blocked-by:\s*(?P<blocked>none|"[^"]*"|[^\s()"]+)'
    r'\s+last-verified:\s*(?P<verified>[^\s()"]+)'
    r'\s+note:\s*(?:"(?P<noteq>[^"]*)"|(?P<note>[^()"]*))\)\s*$')
_LEGACY_GOAL_RE = re.compile(
    r'^\(goal\s+(?P<name>[^\s()"]+)\s+(?P<area>[^\s()"]+)'
    rf'\s+sti:\s*(?P<sti>{_NUMBER})\s+lti:\s*(?P<lti>{_NUMBER})'
    r'\s+vibes:\s*\((?P<vibes>[^()]*)\)'
    r'\s+blocked-by:\s*(?P<blocked>none|"[^"]*"|[^\s()"]+)\)\s*$')
_FLAG_RE = re.compile(r'^\(goal-flag\s+(\S+)\s+(stuck|saturated|cull-review)\)\s*$')
_BUDGET_RE = re.compile(rf'^\(goal-budget\s+({_NUMBER})\)\s*$')
_MOOD_RE = re.compile(r'^\(mood\s.*\)\s*$')
_AFFECT_RE = re.compile(r'⋄⟨([^⟩]*)⟩')
_AFFECT_PAIR_RE = re.compile(rf'(Cn|Ct|Co|Sp|C|I|J|A|S):({_NUMBER})')


def _goal_dict(m):
    """Normalized goal dict from a canonical or legacy goal match."""
    g = m.groupdict()
    note = g.pop("noteq", None)
    free_note = g.pop("note", None)
    g["note"] = note if note is not None else (free_note or "").strip()
    g["verified"] = g.get("verified") or "never"
    g["sti"] = min(1.0, max(0.0, float(g["sti"])))
    g["lti"] = min(1.0, max(0.0, float(g["lti"])))
    return g


def _goals_path(path=None):
    return str(path or os.environ.get(
        "METTACLAW_GOALS_PATH",
        os.path.join(os.path.dirname(os.environ.get(
            "METTACLAW_HISTORY_PATH", "memory/history.metta")), "goals.metta")))


def _kernel_path(goals_path):
    return os.path.join(os.path.dirname(goals_path), "goals_kernel.json")


def _compost_path(goals_path):
    return os.path.join(os.path.dirname(goals_path), "goals_compost.log")


def _read_lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return None


def _write_lines(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _fmt_goal(g):
    return ('(goal {name} {area} sti:{sti} lti:{lti} vibes:({vibes}) '
            'blocked-by:{blocked} last-verified:{verified} note:"{note}")'
            .format(**g))


def _parse(lines):
    """Parse, migrate legacy records, and enforce one record per goal name.

    Canonical records win over legacy duplicates. Within the same format, the
    last record wins while retaining the name's first stack position. Unknown
    non-goal lines remain untouched; malformed goal-looking lines fail loudly
    so they can never become a second, inert goal store again.
    """
    goals, flags, other = [], set(), []
    positions, canonical = {}, {}
    budget = None
    for lineno, line in enumerate(lines, 1):
        m = _GOAL_RE.match(line)
        is_canonical = bool(m)
        if not m:
            m = _LEGACY_GOAL_RE.match(line)
        if m:
            goal = _goal_dict(m)
            name = goal["name"]
            if name not in positions:
                positions[name] = len(goals)
                canonical[name] = is_canonical
                goals.append(goal)
            elif is_canonical or not canonical[name]:
                goals[positions[name]] = goal
                canonical[name] = is_canonical
            continue
        m = _FLAG_RE.match(line)
        if m:
            flags.add((m.group(1), m.group(2)))
            continue
        m = _BUDGET_RE.match(line)
        if m:
            budget = float(m.group(1))
            continue
        if _MOOD_RE.match(line):
            continue  # kernel-owned; re-emitted each pass
        if line.lstrip().startswith("(goal ") or line.lstrip().startswith(
                "(goal-budget"):
            raise ValueError("malformed goal record at line %d" % lineno)
        other.append(line)
    return goals, flags, budget, other


def _emit(goals, flags, budget, other, mood):
    lines = list(other)
    if lines and lines[-1].strip():
        lines.append("")
    lines.append("(goal-budget %s)" % _r(budget))
    for g in goals:
        g = dict(g, sti=_r(g["sti"]), lti=_r(g["lti"]))
        lines.append(_fmt_goal(g))
    for name, flag in sorted(flags):
        lines.append("(goal-flag %s %s)" % (name, flag))
    if mood:
        lines.append("(mood %s)" % " ".join(
            "%s:%s" % (d, _r(mood[d])) for d in MOOD_DIMS))
    return lines


def _r(x):
    return ("%.4f" % float(x)).rstrip("0").rstrip(".") or "0"


def _load_kernel_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _mood_samples(log_path, offset):
    """Affect vectors from outbound sends appended since offset."""
    samples = []
    try:
        size = os.path.getsize(log_path)
        if offset > size:
            offset = 0
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("kind") != "outbound":
                    continue
                for trace in _AFFECT_RE.findall(rec.get("text", "")):
                    pairs = dict(_AFFECT_PAIR_RE.findall(trace))
                    if len(pairs) >= 6:
                        samples.append({d: float(v) for d, v in pairs.items()})
            offset = fh.tell()
    except OSError:
        pass
    return samples, offset


def kernel_pass(goals_path=None, log_path=None, wake_token=None,
                _with_status=False):
    """One autonomic wake: decay, consolidate, flag, normalize, mood-EMA."""
    with _LOCK:
        try:
            path = _goals_path(goals_path)
            lines = _read_lines(path)
            if lines is None:
                lines = [";; goal stack — syntax in SKILLS; kernel decays/flags at each wake"]
            goals, flags, budget, other = _parse(lines)
            if budget is None:
                budget = DEFAULT_BUDGET

            state = _load_kernel_state(_kernel_path(path))
            previous_wake_digest = state.get("last_human_wake")
            wake_digest = None
            if wake_token is not None and str(wake_token):
                wake_digest = hashlib.sha256(
                    str(wake_token).encode("utf-8")
                ).hexdigest()
                if state.get("last_human_wake") == wake_digest:
                    result = "goal-kernel: human wake already applied"
                    return (False, result) if _with_status else result
            streaks = state.get("streaks", {})

            for g in goals:
                g["sti"] *= DECAY
                g["lti"] = g["lti"] + ALPHA * max(0.0, g["sti"] - g["lti"])
                prev = streaks.get(g["name"], {})
                if g["blocked"] != "none" and g["blocked"] == prev.get("blocked"):
                    streak = prev.get("streak", 0) + 1
                else:
                    streak = 0
                streaks[g["name"]] = {"blocked": g["blocked"], "streak": streak}
                if streak + 1 >= STUCK_WAKES:
                    flags.add((g["name"], "stuck"))
                else:
                    flags.discard((g["name"], "stuck"))
                if g["sti"] < CULL_STI and g["lti"] < CULL_LTI:
                    flags.add((g["name"], "cull-review"))
                else:
                    flags.discard((g["name"], "cull-review"))

            total = sum(g["sti"] for g in goals)
            if total > budget > 0:
                scale = budget / total
                for g in goals:
                    g["sti"] *= scale

            names = {g["name"] for g in goals}
            flags = {(n, f) for n, f in flags if n in names}
            streaks = {n: s for n, s in streaks.items() if n in names}

            mood = {d: float(state.get("mood", {}).get(d, 0.0)) for d in MOOD_DIMS}
            have_mood = bool(state.get("mood"))
            lp = str(log_path or os.environ.get("METTACLAW_TELEGRAM_LOG_PATH", ""))
            offset = int(state.get("log_offset", 0))
            if lp:
                samples, offset = _mood_samples(lp, offset)
                for s in samples:
                    if not have_mood:
                        mood = {d: s.get(d, 0.0) for d in MOOD_DIMS}
                        have_mood = True
                    else:
                        for d in MOOD_DIMS:
                            mood[d] = GAMMA * mood[d] + (1 - GAMMA) * s.get(d, mood[d])

            _write_lines(path, _emit(goals, flags, budget, other,
                                     mood if have_mood else None))
            state = {"streaks": streaks, "log_offset": offset,
                     "mood": mood if have_mood else {}}
            if wake_digest is not None:
                state["last_human_wake"] = wake_digest
            elif previous_wake_digest:
                state["last_human_wake"] = previous_wake_digest
            with open(_kernel_path(path) + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(_kernel_path(path) + ".tmp", _kernel_path(path))

            flagged = ["%s:%s" % (n, f) for n, f in sorted(flags)]
            result = ("goal-kernel: %d goals, sti %s/%s%s"
                      % (len(goals), _r(sum(g["sti"] for g in goals)), _r(budget),
                         (", flags " + " ".join(flagged)) if flagged else ""))
            return (True, result) if _with_status else result
        except Exception as exc:
            result = "goal-kernel error: %s" % exc
            return (False, result) if _with_status else result


def human_wake(wake_token, goals_path=None, log_path=None):
    """Recognize one presented batch and attempt its maintenance once.

    Goal bookkeeping is not cognition authority. If its durable write fails,
    the in-process identity still prevents a retry storm while the fresh input
    remains allowed to arm cognition.
    """
    global _runtime_human_wake
    token = str(wake_token or "")
    runtime_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    applied, message = kernel_pass(
        goals_path, log_path, wake_token=token, _with_status=True
    )
    with _LOCK:
        if message.startswith("goal-kernel error:"):
            fresh = _runtime_human_wake != runtime_digest
        else:
            fresh = applied
        _runtime_human_wake = runtime_digest
    return {"fresh": int(fresh), "maintenance_applied": int(applied),
            "message": message}


def goals_view(goals_path=None):
    lines = _read_lines(_goals_path(goals_path))
    if not lines:
        return "(no goals yet — add one with (goal-write ...))"
    text = "\n".join(lines)
    import strategy_host
    if strategy_host.fair_enabled() and goals_path is None:
        view = strategy_host.goal_snapshot()
        text += "\nFAIR_GOALS " + json.dumps(view, ensure_ascii=False, sort_keys=True)
    return text


def affect_view(goals_path=None):
    """Read the persisted discounted affect gestalt without advancing it."""
    path = _goals_path(goals_path)
    state = _load_kernel_state(_kernel_path(path))
    mood = state.get("mood") or {}
    if not mood:
        return "gamma:%s (no affect observations yet)" % _r(GAMMA)
    return "gamma:%s %s" % (
        _r(GAMMA),
        " ".join("%s:%s" % (d, _r(float(mood.get(d, 0.0))))
                 for d in MOOD_DIMS),
    )


def _compost_entries(path, limit=4):
    """Most recently composted goals, newest first."""
    try:
        with open(_compost_path(path), encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        stamp, _, rest = line.partition(" ")
        reason, _, record = rest.partition(" ")
        name = re.match(r"\(goal\s+(\S+)", record)
        out.append("%s %s (%s)" % (stamp[:10], name.group(1) if name else "?",
                                   reason))
    return out


def work_state_view(goals_path=None, max_chars=6000, note_chars=180):
    """The goal stack as work state rather than as a dump.

    Active / Blocked / Completed / Next Move — the shape a coding agent
    hands to its successor. Everything here is derived on read from the
    same file the kernel already maintains at every wake, so there is
    nothing extra to refresh and nothing that can rot: the digests that
    died before this one all needed the agent to remember to update them.
    """
    path = _goals_path(goals_path)
    lines = _read_lines(path)
    if not lines:
        return "(no goals yet — add one with (goal-write ...))"
    try:
        goals, flags, _budget, _other = _parse(lines)
    except ValueError as exc:
        return "work state unavailable: %s" % exc
    flagged = {}
    for name, flag in flags:
        flagged.setdefault(name, []).append(flag)

    def line_for(g, blocked=False):
        note = " ".join(str(g["note"]).split())[:note_chars]
        marks = "".join(" !" + f for f in sorted(flagged.get(g["name"], [])))
        head = "%s [%s sti:%s" % (g["name"], g["area"], _r(g["sti"]))
        if blocked:
            head += " blocked-by:%s" % g["blocked"]
        if g["verified"] != "never":
            head += " verified:%s" % g["verified"]
        return "  - %s]%s %s" % (head, marks, note)

    # Ordered by when the agent last verified progress, not by STI. STI
    # decays with no source and sits at a rounding floor, so ranking by it
    # would dress an arbitrary order as a priority — and choosing what to do
    # next is the reader's job, not this renderer's. Raw sti stays visible
    # because it is data; the ordering claim is what had to go.
    ranked = sorted(
        enumerate(goals),
        key=lambda pair: (pair[1]["verified"] != "never",
                          pair[1]["verified"], -pair[0]),
        reverse=True)
    active = [g for _i, g in ranked if g["blocked"] == "none"]
    blocked = [g for _i, g in ranked if g["blocked"] != "none"]
    done = _compost_entries(path)

    # "last-verified" is a timestamp the agent writes when it decides it has
    # checked something. That is a self-report, not a verification, and the
    # header says so — the whole point of dropping Next Move was to stop
    # lending unearned authority to a field, and the ordering label is a
    # field too.
    out = ["Active (by last self-reported check, newest first):"]
    out += [line_for(g) for g in active] or ["  (none)"]
    out += ["Blocked:"] + ([line_for(g, True) for g in blocked] or ["  (none)"])
    out += ["Completed:"] + (["  - " + d for d in done] or ["  (none)"])
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    import helper
    return helper.change_mark("work-state", text) + "\n" + text


def attention_view(goals_path=None):
    """Pure, read-only ranking of the authoritative goal snapshot.

    STI remains the primary signal. LTI breaks equal-STI ties, so the fast
    variable's four-decimal floor no longer erases all ordering information.
    Stable file order breaks exact ties. This view neither pays wages nor
    rewrites attention state; it claims ranking, not conservation.
    """
    lines = _read_lines(_goals_path(goals_path))
    if not lines:
        return "(no ranked goals)"
    try:
        parsed, _flags, _budget, _other = _parse(lines)
    except ValueError as exc:
        return "attention unavailable: %s" % exc
    ranked = sorted(enumerate(parsed),
                    key=lambda pair: (-pair[1]["sti"],
                                      -pair[1]["lti"], pair[0]))
    return " > ".join(
        "%s(sti:%s lti:%s)" % (g["name"], _r(g["sti"]), _r(g["lti"]))
        for _index, g in ranked) or "(no ranked goals)"


def _sexpr_from_pylist(value):
    """Render a janus-marshalled expression back into s-expression text.

    A bare (goal ...) atom crosses the Python bridge as a nested LIST, and
    str() of that list is Python syntax — which no s-expression regex can
    ever match. This was why (goal-write <atom>) failed on every attempt
    while looking correct: the format was fine, the marshalling was not.
    A trailing field token like 'vibes:' followed by a list is rejoined as
    'vibes:(...)' — the reader splits them, we merge them back.
    """
    if not isinstance(value, list):
        return str(value)
    parts = []
    for item in value:
        rendered = ("(" + " ".join(_sexpr_from_pylist(x) for x in item) + ")"
                    if isinstance(item, list) else str(item))
        if parts and parts[-1].endswith(":") and rendered.startswith("("):
            parts[-1] += rendered
        else:
            parts.append(rendered)
    return "(" + " ".join(parts) + ")"


def _normalize_goal_text(atom):
    """Whatever arrives — s-expression text, a python-list repr of it, comma
    or space vibes, a space after 'vibes:', a missing vibes field — becomes
    the one canonical line the parser accepts."""
    text = str(atom).strip()
    if text.startswith("["):
        try:
            import ast
            text = _sexpr_from_pylist(ast.literal_eval(text))
        except (ValueError, SyntaxError):
            pass
    # the reader splits 'vibes:(' into 'vibes: (' — rejoin
    text = re.sub(r"(\bvibes:|\bblocked-by:|\blast-verified:|\bnote:|\bsti:|\blti:)\s+\(", r"\1(", text)
    # commas and spaces inside vibes both mean "list of vibes"
    text = re.sub(r"vibes:\s*\(([^)]*)\)",
                  lambda m: "vibes:(" + " ".join(
                      w for w in re.split(r"[,\s]+", m.group(1)) if w) + ")",
                  text)
    # a goal without vibes gets an empty vibes list rather than a rejection
    if text.startswith("(goal ") and "vibes:" not in text:
        text = re.sub(r"(\s+blocked-by:)", r" vibes:()\1", text, count=1)
    return text


def goal_write(atom, goals_path=None):
    """Add or update one goal (or the budget) from its S-expression text."""
    with _LOCK:
        path = _goals_path(goals_path)
        text = _normalize_goal_text(atom)

        mb = _BUDGET_RE.match(text)
        if mb:
            budget = float(mb.group(1))
            if not 0.5 <= budget <= 10:
                return "goal-budget must be in [0.5, 10]"
        m = _GOAL_RE.match(text)
        if not (m or mb):
            return ('rejected: expected (goal <name> <area> sti:<0-1> lti:<0-1> '
                    'vibes:(..) blocked-by:<none|hyphenated-token> '
                    'last-verified:<never|timestamp> note:free text) on one '
                    'line — no quotes needed, hyphenate blocked-by')
        lines = _read_lines(path) or []
        try:
            goals, flags, old_budget, other = _parse(lines)
        except ValueError as exc:
            return "goal-write rejected: %s" % exc
        state = _load_kernel_state(_kernel_path(path))
        mood = state.get("mood") or None
        if mb:
            _write_lines(path, _emit(goals, flags, budget, other, mood))
            return "budget set to %s" % _r(budget)
        g = _goal_dict(m)
        existing = [i for i, cur in enumerate(goals) if cur["name"] == g["name"]]
        if existing:
            goals[existing[0]] = g
            verdict = "goal %s updated" % g["name"]
        else:
            goals.append(g)
            verdict = "goal %s added" % g["name"]
        _write_lines(path, _emit(goals, flags,
                                 old_budget if old_budget is not None else DEFAULT_BUDGET,
                                 other, mood))
        return verdict


def _retire_in_ledger(name, reason):
    """Close the goal's open cycles in the request ledger, never as achieved."""
    try:
        import strategy_host
        return strategy_host.retire_goal(name, reason)
    except Exception:
        return 0


def goal_drop(name, reason, goals_path=None):
    """Remove a goal and compost it. Reason 'solved' claims achievement and
    needs a real last-verified; any other reason retires the goal. Its open
    ledger cycles close without counting as achieved, and goal-write can
    bring the goal back."""
    with _LOCK:
        path = _goals_path(goals_path)
        name, reason = str(name), str(reason)
        lines = _read_lines(path) or []
        try:
            goals, flags, budget, other = _parse(lines)
        except ValueError as exc:
            return "goal-drop rejected: %s" % exc
        match = [g for g in goals if g["name"] == name]
        if not match:
            return "no goal named %s" % name
        g = match[0]
        if not reason.strip():
            return "rejected: give a reason, or 'solved' for a verified goal"
        if reason == "solved" and g["verified"] == "never":
            return ("rejected: last-verified is 'never' — verify progress "
                    "(goal-write with a last-verified timestamp) before "
                    "claiming solved, or retire it with another reason")
        try:
            with open(_compost_path(path), "a", encoding="utf-8") as fh:
                fh.write("%s %s %s\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%S"), reason, _fmt_goal(g)))
        except OSError as exc:
            return "goal-drop failed: compost log was not written: %s" % exc
        goals = [x for x in goals if x["name"] != name]
        flags = {(n, f) for n, f in flags if n != name}
        state = _load_kernel_state(_kernel_path(path))
        _write_lines(path, _emit(goals, flags,
                                 budget if budget is not None else DEFAULT_BUDGET,
                                 other, state.get("mood") or None))
    closed = _retire_in_ledger(name, reason)
    return "goal %s composted (%s)%s — remember a compost note" % (
        name, reason, "; %d open cycle(s) closed, not achieved" % closed if closed else "")

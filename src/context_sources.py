"""Typed, bounded context views over the agent's authoritative records.

This module is replaceable policy.  It neither changes the event ledger nor
chooses actions; it only names and bounds the views supplied to a model turn.
"""

from dataclasses import dataclass
import hashlib
import os
import threading
import json
from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    max_chars: int
    keep_tail: bool = False


@dataclass(frozen=True)
class Observation:
    source_id: str
    status: str
    text: str
    revision: str
    epoch: str = ""


def context_epoch():
    """Bind retained projections to this task, workspace and instructions.

    The epoch identifies an observation context. It does not promote an
    assistant's historical notes into operator instructions for that task.
    """
    import strategy_host
    if not strategy_host.enabled():
        return "unmanaged"
    with strategy_host._lock:
        task = strategy_host._focus or "autonomous"
    workspace = os.path.realpath(os.environ.get("METTACLAW_PROJECT_ROOT", os.getcwd()))
    soul = Path(os.environ.get("METTACLAW_PROMPT_PATH", "memory/soul.md"))
    try:
        instructions = hashlib.sha256(soul.read_bytes()).hexdigest()
    except OSError:
        import uuid
        instructions = "unavailable:" + uuid.uuid4().hex
    body = json.dumps((task, workspace, instructions), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def evidence_role(source_id):
    if source_id in ("pins", "work-state", "recent-proposals"):
        return "assistant-memory"
    if source_id == "conversation":
        return "conversation-record"
    return "operational-observation"


SOURCE_SPECS = (
    SourceSpec("work-state", 8000),
    SourceSpec("commitment-state", 2000),
    SourceSpec("task-phase", 3000),
    SourceSpec("active-query-declaration", 3000),
    SourceSpec("affect-gestalt", 2000),
    SourceSpec("pins", 6000, True),
    SourceSpec("effect-receipts", 14000, True),
    SourceSpec("development-state", 10000, True),
    SourceSpec("recent-proposals", 10000, True),
    SourceSpec("relevant-files", 4000),
    SourceSpec("project-evidence", 8000),
    SourceSpec("coding-sessions", 2000),
    SourceSpec("project-capabilities", 5000),
    SourceSpec("conversation", 30000, True),
)

# Prompt layout for provider prefix caching.  Sources are rendered from the
# least to the most changeable, with a boundary after each cacheable group:
# first the ones that change rarely, then the conversation, which changes when
# a message arrives, then everything that changes every turn.  A provider may
# reuse any prefix that ends at a boundary; text after the last boundary is
# expected to differ from turn to turn.
CACHE_BOUNDARY = "_cache_boundary_"
STABLE_SOURCE_IDS = ("project-capabilities", "development-state",
                     "project-evidence", "coding-sessions")
MESSAGE_SOURCE_IDS = ("conversation",)


def _clip(text, spec):
    text = str(text)
    if len(text) <= spec.max_chars:
        return text
    if spec.keep_tail:
        return "…\n" + text[-(spec.max_chars - 2):]
    return text[:spec.max_chars - 1] + "…"


class Projector:
    """Admit known/absent/unavailable source observations.

    A temporary loader failure retains the last known projection.  A genuine
    empty result is different: it intentionally clears that source.
    """

    def __init__(self):
        self._known = {}
        self._lock = threading.RLock()
        self._epoch = ""

    @staticmethod
    def _revision(value):
        return "sha256:" + hashlib.sha256(
            str(value).encode("utf-8", "replace")
        ).hexdigest()

    def observe(self, spec, loader, epoch=None):
        epoch = self._epoch if epoch is None else epoch
        key = (epoch, spec.source_id)
        try:
            value = loader()
        except Exception:
            with self._lock:
                previous = self._known.get(key)
            if previous is None:
                return Observation(spec.source_id, "unavailable", "", "", epoch)
            return Observation(
                spec.source_id, "stale-known", previous.text,
                previous.revision, epoch)

        if value is None or not str(value).strip():
            with self._lock:
                self._known.pop(key, None)
            return Observation(
                spec.source_id, "absent", "", self._revision(""), epoch)

        raw = str(value)
        text = _clip(raw, spec)
        observed = Observation(
            spec.source_id, "known", text, self._revision(raw), epoch)
        with self._lock:
            self._known[key] = observed
        return observed

    def project(self, sources, epoch=""):
        with self._lock:
            if self._epoch != epoch:
                self._known.clear()
                self._epoch = epoch
            return tuple(self.observe(spec, loader, epoch) for spec, loader in sources)

    @staticmethod
    def render_observations(observations):
        blocks = []
        for item in observations:
            header = "SOURCE[%s] status=%s revision=%s" % (
                item.source_id, item.status, item.revision or "unavailable")
            header += " context-epoch=%s evidence=%s" % (
                item.epoch or "unbound", evidence_role(item.source_id))
            blocks.append(header + ("\n" + item.text if item.text else ""))
        return "\n\n".join(blocks)

    def render(self, sources):
        return self.render_observations(self.project(sources))


_PROJECTOR = Projector()


def _supplied(views):
    """(source_id, text) pairs a caller computed itself. Anything malformed
    is ignored, so those sources load here as they otherwise would; one bad
    entry costs only itself, so a caller's valid views are never discarded
    with it. A blank text is ignored too: a caller that found nothing must
    not turn its source into a valid empty observation, which would clear the
    source instead of leaving it to its loader. (No loader's own answer is
    ever blank.)"""
    supplied = {}
    try:
        entries = iter(views or ())
    except TypeError:
        return {}
    for entry in entries:
        try:
            source_id, text = entry
        except (TypeError, ValueError):
            continue
        if (isinstance(source_id, str) and isinstance(text, str)
                and text.strip()):
            supplied[source_id] = text
    return supplied


def _loaders(conversation=None, views=None):
    import active_queries
    import coding_estate
    import commitment_projection
    import development_state
    import effect_receipts
    import goals
    import helper
    import pins
    import project_evidence
    import project_capabilities
    import task_phase
    import telegram
    import strategy_host

    history = helper.path_from_env(
        "METTACLAW_HISTORY_PATH", "./memory/history.metta")
    turns = helper.int_env("METTACLAW_RECENT_ACTION_TURNS", 12)
    events = helper.int_env("METTACLAW_CONVERSATION_EVENTS", 64)
    specs = {spec.source_id: spec for spec in SOURCE_SPECS}
    # Source texts the caller computed with these same arguments and
    # conditions: on CeTTa, the two action views and the pins body are
    # computed natively.
    views = _supplied(views)
    return (
        (specs["work-state"], goals.work_state_view),
        (specs["commitment-state"], commitment_projection.view),
        (specs["task-phase"], task_phase.view),
        (specs["active-query-declaration"], active_queries.view),
        (specs["affect-gestalt"], goals.affect_view),
        # A native body is marked here, by the same owner (and state) as the
        # body pins.py's own reader makes when no native body was supplied.
        (specs["pins"],
         lambda: pins.mark_context_body(views["pins-body"])
         if "pins-body" in views else pins.context_view()),
        (specs["effect-receipts"], lambda: "Task receipts are in TASK_CONTEXT." if strategy_host.enabled() else effect_receipts.view()),
        (specs["development-state"], development_state.view),
        (specs["recent-proposals"],
         lambda: "Task proposals and their dispositions are in TASK_CONTEXT." if strategy_host.enabled() else views["recent-proposals"] if "recent-proposals" in views else helper.recent_actions(history, limit=turns)),
        (specs["relevant-files"],
         lambda: views["relevant-files"] if "relevant-files" in views else helper.relevant_files(history, limit=turns)),
        (specs["project-evidence"], project_evidence.view),
        (specs["coding-sessions"], coding_estate.context_view),
        (specs["project-capabilities"], project_capabilities.view),
        # The MeTTa Telegram client renders the conversation itself.
        (specs["conversation"],
         (lambda: conversation) if conversation is not None
         else (lambda: telegram.conversation_window(max_events=events,
                    chat_id=getattr(telegram, "_reply_chat_id", ""))
               if strategy_host.conversation_mode() == "chat" else telegram.route_view())),
    )


def layout(observations):
    """Render observations least-changeable first, with cache boundaries."""
    groups = (
        [o for o in observations if o.source_id in STABLE_SOURCE_IDS],
        [o for o in observations if o.source_id in MESSAGE_SOURCE_IDS],
        [o for o in observations
         if o.source_id not in STABLE_SOURCE_IDS + MESSAGE_SOURCE_IDS],
    )
    rank = {source_id: index for index, source_id in enumerate(
        STABLE_SOURCE_IDS + MESSAGE_SOURCE_IDS)}
    groups[0].sort(key=lambda o: rank[o.source_id])
    separator = "\n\n" + CACHE_BOUNDARY + "\n\n"
    return separator.join(
        Projector.render_observations(group) for group in groups)


def bundle(conversation=None, views=None):
    """One ordered context bundle; never raises across the MeTTa boundary.

    views are (source_id, text) pairs of sources already computed by the
    caller; any source not among them is loaded here."""
    try:
        observations = _PROJECTOR.project(_loaders(conversation, views), context_epoch())
        projection = layout(observations)
        return projection
    except Exception:
        return "SOURCE[context-bundle] status=unavailable"


def bundle_with_views(views, *conversation):
    """bundle() for MeTTa, which cannot pass conversation=None."""
    return bundle(*conversation, views=views)

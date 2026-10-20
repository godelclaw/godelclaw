"""Typed, bounded context views over the agent's authoritative records.

This module is replaceable policy.  It neither changes the event ledger nor
chooses actions; it only names and bounds the views supplied to a model turn.
"""

from dataclasses import dataclass
import hashlib
import os
import threading


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
                     "project-evidence")
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

    @staticmethod
    def _revision(value):
        return "sha256:" + hashlib.sha256(
            str(value).encode("utf-8", "replace")
        ).hexdigest()

    def observe(self, spec, loader):
        try:
            value = loader()
        except Exception:
            with self._lock:
                previous = self._known.get(spec.source_id)
            if previous is None:
                return Observation(spec.source_id, "unavailable", "", "")
            return Observation(
                spec.source_id, "stale-known", previous.text,
                previous.revision)

        if value is None or not str(value).strip():
            with self._lock:
                self._known.pop(spec.source_id, None)
            return Observation(
                spec.source_id, "absent", "", self._revision(""))

        raw = str(value)
        text = _clip(raw, spec)
        observed = Observation(
            spec.source_id, "known", text, self._revision(raw))
        with self._lock:
            self._known[spec.source_id] = observed
        return observed

    def project(self, sources):
        return tuple(self.observe(spec, loader) for spec, loader in sources)

    @staticmethod
    def render_observations(observations):
        blocks = []
        for item in observations:
            header = "SOURCE[%s] status=%s revision=%s" % (
                item.source_id, item.status, item.revision or "unavailable")
            blocks.append(header + ("\n" + item.text if item.text else ""))
        return "\n\n".join(blocks)

    def render(self, sources):
        return self.render_observations(self.project(sources))


_PROJECTOR = Projector()


def _loaders(conversation=None):
    import active_queries
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
    return (
        (specs["work-state"], goals.work_state_view),
        (specs["commitment-state"], commitment_projection.view),
        (specs["task-phase"], task_phase.view),
        (specs["active-query-declaration"], active_queries.view),
        (specs["affect-gestalt"], goals.affect_view),
        (specs["pins"], pins.context_view),
        (specs["effect-receipts"], lambda: "Task receipts are in TASK_CONTEXT." if strategy_host.enabled() else effect_receipts.view()),
        (specs["development-state"], development_state.view),
        (specs["recent-proposals"],
         lambda: "Task proposals and their dispositions are in TASK_CONTEXT." if strategy_host.enabled() else helper.recent_actions(history, limit=turns)),
        (specs["relevant-files"],
         lambda: helper.relevant_files(history, limit=turns)),
        (specs["project-evidence"], project_evidence.view),
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


def bundle(conversation=None):
    """One ordered context bundle; never raises across the MeTTa boundary."""
    try:
        observations = _PROJECTOR.project(_loaders(conversation))
        projection = layout(observations)
        return projection
    except Exception:
        return "SOURCE[context-bundle] status=unavailable"

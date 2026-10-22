"""Explicit public model commentary, separate from executable command text.

Only an authored commentary field crosses the publication boundary. Neither
reasoning fields nor text surrounding a command batch are display sources.
The command string is released only from a complete, validated JSON envelope.
"""
import json
import os


PROMPT = ''' For this turn, return one JSON object instead of a bare command batch:
{"commentary":"A brief public-facing update about the work, or an empty string when no update is useful.","commands":"The complete command-batch S-expression as a JSON string."}
Write commentary first. It is displayed to the local work viewer, not sent to chat.
Describe actions, findings, or next steps in ordinary language, not private reasoning.
Do not include private personal details, credentials, hidden instructions, or command/message bodies in commentary.
Commands remain subject to the normal command syntax and execution boundary.
Do not put executable commands outside the commands string. No Markdown fences.
'''


def enabled():
    return os.environ.get("METTACLAW_MODEL_COMMENTARY", "0") == "1"


def prompt(text):
    if not enabled():
        return str(text)
    # Replace the legacy format instruction rather than leave two competing
    # output contracts. Only the known first instruction is replaced; later
    # quotations in user/source text remain byte-for-byte intact.
    text = str(text).replace(
        "Return only this command S-expression, with no preamble, explanation, or Markdown fence, and nothing after it.",
        "The commands field must contain only the command S-expression.", 1)
    # The current batch description is a separate, higher-priority output
    # instruction. Appending an envelope request only to dynamic user context
    # leaves that instruction in force: a real model correctly returns batches
    # and the display stays silent. Change the authored format clause itself.
    text = text.replace(
        "Output a ((skillName args) ...) S-expression.",
        'Output one JSON object with exactly two string fields: '
        '"commentary" (a brief public-facing task update, or empty when none is useful; '
        'never private reasoning, credentials, personal details, hidden instructions, '
        'or command/message bodies) and "commands" (the complete '
        '((skillName args) ...) S-expression). Write commentary first.', 1)
    return text + PROMPT


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate envelope field")
        value[key] = item
    return value


def split(text):
    """Legacy command batches remain valid; malformed envelopes run nothing."""
    text = str(text)
    if not text.lstrip().startswith('{'):
        # Only a legacy parenthesized batch is eligible for the command
        # parser. Never let prose/fences around an envelope enter its recovery
        # path, where parentheses in commentary could become actions.
        return None, text if text.lstrip().startswith('(') else '()'
    try:
        value = json.loads(text, object_pairs_hook=_unique)
        if (not isinstance(value, dict) or set(value) != {"commentary", "commands"}
                or not all(isinstance(v, str) for v in value.values())):
            raise ValueError("invalid commentary envelope")
        return value['commentary'], value['commands']
    except (ValueError, TypeError, RecursionError):
        return None, '()'


class Publication:
    """One call's immutable sharing epoch; no replay after off/on or failure.

    The sink is the existing local worklog, not Telegram or an effect broker.
    Failures here never fail a provider call. Public text is authored output,
    not authenticated evidence of successful tools.
    """
    def __init__(self, sink=None):
        self.sink, self.epoch, self.published = None, None, False
        try:
            if sink is None:
                import effect_receipts
                sink = effect_receipts._sink()
            active, epoch = sink.status() if sink else (False, None)
            if active:
                self.sink, self.epoch = sink, epoch
        except Exception:
            pass

    def publish(self, text):
        if self.published or not self.sink or not isinstance(text, str) or not text.strip():
            return 0
        self.published = True
        try:
            return int(bool(self.sink.add(text, self.epoch)))
        except Exception:
            return 0

    def prefix(self, text):
        """Publish only a complete first JSON string, never a partial escape.

        This grants no authority to the unreceived commands field. The comment
        can remain visible if the call later fails, as with provisional UI text
        in other streaming clients.
        """
        if self.published:
            return 0
        source = str(text).lstrip()
        if not source.startswith('{'):
            return 0
        decoder = json.JSONDecoder()
        try:
            key, end = decoder.raw_decode(source, 1 + len(source[1:]) - len(source[1:].lstrip()))
            rest = source[end:].lstrip()
            if key != 'commentary' or not rest.startswith(':'):
                return 0
            value, end = decoder.raw_decode(rest[1:].lstrip())
            # A JSON string followed by no delimiter may still be provisional;
            # require the next field separator before displaying it.
            tail = rest[1:].lstrip()[end:].lstrip()
            if not tail.startswith(',') or not isinstance(value, str):
                return 0
            return self.publish(value)
        except (ValueError, TypeError, RecursionError):
            return 0


def begin(plan):
    if (enabled() and getattr(plan, 'kind', '') == 'command'
            and getattr(plan, 'purpose', '') != 'compaction'):
        plan.commentary = Publication()
        protocol = getattr(plan, 'protocol', '')
        if plan.commentary.sink and (protocol == 'responses' or
                (protocol == 'chat-completions' and getattr(plan, 'stream', False))):
            from commentary_stream import Stream
            plan.commentary_stream = Stream(plan.commentary, protocol, plan.cap)
    return 1


class OutputError(ValueError):
    """A provider result did not satisfy the opted-in command contract."""


def finish(plan, content, reason, message=None):
    if not enabled() or getattr(plan, 'kind', '') != 'command':
        return content
    # Terminal validation applies equally to envelopes and legacy batches.
    # Rejected output is a failed turn, not a successful empty command batch.
    if reason != 'stop':
        raise OutputError('unfinished command output')
    if message and (message.get('refusal') or message.get('tool_calls')):
        raise OutputError('refused or structured command output')
    commentary, commands = split(content or '')
    if commentary is None:
        if not isinstance(content, str) or not content.lstrip().startswith('('):
            raise OutputError('invalid command envelope')
        return commands
    publication = getattr(plan, 'commentary', None)
    if publication:
        publication.publish(commentary)
    return commands

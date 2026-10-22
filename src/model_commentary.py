"""Explicit public model commentary, separate from executable command text.

Only an authored commentary field crosses the publication boundary. Neither
reasoning fields nor text surrounding a command batch are display sources.
The command string is released only from a complete, validated JSON envelope.
"""
import json
import os


PROMPT = ''' For this turn, use the advertised `metta` tool exactly once for any
action or reply. Its `program` argument is the complete command-batch
S-expression. Ordinary assistant text is optional public work commentary: it is
displayed to the local work viewer, not sent to chat. Describe actions, findings,
or next steps there, never private reasoning, credentials, personal details,
hidden instructions, or command/message bodies. Do not put executable commands
in assistant text. If the provider does not offer the `metta` tool, use the
compatibility JSON object instead:
{"commentary":"brief public update or empty","commands":"((skillName args) ...)"}
'''


TOOL_NAME = "metta"


def tool_schema():
    """The one typed authority boundary for an ordinary agent turn."""
    return {"type": "function", "function": {
        "name": TOOL_NAME,
        "description": (
            "Run one complete GödelClaw MeTTa command batch. Use an advertised "
            "messaging command inside the batch when a person should receive prose."),
        "parameters": {
            "type": "object",
            "properties": {"program": {
                "type": "string",
                "description": "Complete ((skillName args) ...) S-expression."}},
            "required": ["program"],
            "additionalProperties": False}}}


def tool_schema_json():
    """Portable spelling for MeTTa request builders."""
    return json.dumps(tool_schema(), separators=(",", ":"), ensure_ascii=False)


def enabled():
    return os.environ.get("METTACLAW_MODEL_COMMENTARY", "0") == "1"


def enabled_flag():
    """Text flag for the historical PeTTa foreign-value boundary."""
    return "true" if enabled() else "false"


def prompt(text):
    if not enabled():
        return str(text)
    # Replace the legacy format instruction rather than leave two competing
    # output contracts. Only the known first instruction is replaced; later
    # quotations in user/source text remain byte-for-byte intact.
    text = str(text).replace(
        "Return only this command S-expression, with no preamble, explanation, or Markdown fence, and nothing after it.",
        "Use the advertised metta tool for the command S-expression.", 1)
    # The current batch description is a separate, higher-priority output
    # instruction. Appending an envelope request only to dynamic user context
    # leaves that instruction in force: a real model correctly returns batches
    # and the display stays silent. Change the authored format clause itself.
    text = text.replace(
        "Output a ((skillName args) ...) S-expression.",
        'Use the advertised metta tool exactly once; its program field is the complete '
        '((skillName args) ...) S-expression. Optional assistant text is a brief public '
        'task update, never private reasoning, credentials, personal details, hidden '
        'instructions, or command/message bodies.', 1)
    return text + PROMPT


def output_tail():
    """Keep the active format instruction after quoted task history."""
    return ('\n\nREQUIRED_COMMAND_OUTPUT: Call the advertised metta tool exactly once; '
            'its program field must be the complete ((skillName args) ...) batch. '
            'If no metta tool is available, return only '
            '{"commentary":"brief public update or empty",'
            '"commands":"((skillName args) ...)"}. '
            'To deliver a prose review, use mail-send or another advertised messaging skill '
            'inside the program. Assistant text is public work commentary, never commands.') if enabled() else ""


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
    framed = text.strip()
    if framed.startswith('```'):
        lines = framed.split('\n')
        if (len(lines) < 3 or lines[0].strip().lower() not in ('```json', '```')
                or lines[-1].strip() != '```'):
            return None, '()'
        text = '\n'.join(lines[1:-1])
        if not text.lstrip().startswith('{'):
            return None, '()'
    if not text.lstrip().startswith('{'):
        # Only a legacy parenthesized batch enters command recovery. An
        # optional whole JSON fence is framing, never a search for commands
        # in prose, partial output or an arbitrary code block.
        return None, text if text.lstrip().startswith('(') else '()'
    try:
        value = json.loads(text, object_pairs_hook=_unique)
        if (not isinstance(value, dict) or set(value) != {"commentary", "commands"}
                or not all(isinstance(v, str) for v in value.values())):
            raise ValueError("invalid commentary envelope")
        return value['commentary'], value['commands']
    except (ValueError, TypeError, RecursionError):
        return None, '()'


def _native_commands(message):
    """Validate one normalized provider tool call and return its MeTTa program."""
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list) or len(calls) != 1:
        raise OutputError("invalid native tool output")
    call = calls[0]
    if (not isinstance(call, dict) or call.get("type") != "function"
            or not isinstance(call.get("id"), str) or not call["id"]):
        raise OutputError("invalid native tool output")
    function = call.get("function")
    if (not isinstance(function, dict) or set(function) != {"name", "arguments"}
            or function.get("name") != TOOL_NAME):
        raise OutputError("invalid native tool output")
    arguments = function.get("arguments")
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments, object_pairs_hook=_unique)
    except (ValueError, TypeError, RecursionError):
        raise OutputError("invalid native tool output") from None
    if (not isinstance(arguments, dict) or set(arguments) != {"program"}
            or not isinstance(arguments.get("program"), str)):
        raise OutputError("invalid native tool output")
    return arguments["program"]


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
    # Terminal validation precedes every authority-bearing extraction. Native
    # tools carry commands structurally; assistant text can only be commentary.
    if message and message.get('refusal'):
        raise OutputError('refused or structured command output')
    calls = message.get('tool_calls') if isinstance(message, dict) else None
    if calls:
        if reason != 'tool_calls':
            raise OutputError('invalid native tool output')
        commands = _native_commands(message)
        publication = getattr(plan, 'commentary', None)
        if publication and isinstance(content, str):
            publication.publish(content)
        return commands
    # Compatibility envelopes and legacy batches remain completion-only. A
    # bare prose answer is invalid because silently treating it as no-op could
    # acknowledge a human request without delivering the reply.
    if reason != 'stop':
        raise OutputError('unfinished command output')
    commentary, commands = split(content or '')
    if commentary is None:
        if not isinstance(content, str) or not content.lstrip().startswith('('):
            raise OutputError('invalid command envelope')
        return commands
    publication = getattr(plan, 'commentary', None)
    if publication:
        publication.publish(commentary)
    return commands

from dataclasses import dataclass
from enum import Enum
import json
import re


MAX_ACTIONS = 5
MAX_INPUT_BYTES = 65536
MAX_NESTING = 4096

# This migration decoder follows Patrick Hammer's corrected mettaclaw parser.
# The first commit records Patrick's OmegaClaw import; the second explicitly
# replaces that parser as broken. Keeping both ids preserves those two distinct
# provenance layers instead of flattening them into generic "upstream".
PATRICK_LINE_DECODER_COMMITS = (
    "edb1a811b06ff2ba54936c8e2ddcc72f3f1e2608",
    "0aa488eeb4dca3bd4032df5a742da8e1c2e7d13d",
)
PATRICK_LINE_COMMANDS = frozenset(
    {
        "append-file",
        "demote",
        "episodes",
        "metta",
        "pin",
        "promote",
        "query",
        "read-file",
        "remember",
        "search",
        "send",
        "shell",
        "write-file",
    }
)


class WireErrorCode(str, Enum):
    INPUT_TOO_LARGE = "InputTooLarge"
    UNSUPPORTED_BOM = "UnsupportedBom"
    NO_ACTIONS = "NoActions"
    SYNTAX = "Syntax"
    TOO_MANY_ACTIONS = "TooManyActions"
    ACTION_NOT_EXPRESSION = "ActionNotExpression"
    INVALID_ACTION_HEAD = "InvalidActionHead"
    INVALID_AFFECT = "InvalidAffect"
    DUPLICATE_AFFECT = "DuplicateAffect"


class WireError(ValueError):
    def __init__(self, code, offset, action_index=0):
        self.code = WireErrorCode(code)
        self.offset = int(offset)
        self.action_index = int(action_index)
        super().__init__(f"{self.code.value} at byte {self.offset}")


@dataclass(frozen=True)
class DecodedTurn:
    actions: tuple[str, ...]
    affect: str | None = None
    encoding: str = "sexpr-sequence"

    def as_legacy_list(self):
        return "(" + " ".join(self.actions) + ")"


@dataclass(frozen=True)
class DecoderObservation:
    decoder: str
    accepted: bool
    action_count: int = 0
    encoding: str | None = None
    error_code: str | None = None


_AFFECT_VALUE = r"[-+]?(?:1(?:\.0*)?|0?(?:\.\d+))"
_AFFECT_BODY = (
    rf"Cn:{_AFFECT_VALUE}[ \t]+C:{_AFFECT_VALUE}[ \t]+"
    rf"Ct:{_AFFECT_VALUE}[ \t]+I:{_AFFECT_VALUE}[ \t]+"
    rf"J:{_AFFECT_VALUE}[ \t]+A:{_AFFECT_VALUE}[ \t]+"
    rf"S:{_AFFECT_VALUE}[ \t]+Co:{_AFFECT_VALUE}"
    rf"(?:[ \t]+Sp:{_AFFECT_VALUE})?"
)
_UNICODE_AFFECT = re.compile(rf"^⋄⟨{_AFFECT_BODY}⟩$")
_ASCII_AFFECT = re.compile(rf"^\[{_AFFECT_BODY}\]$")


def _byte_offset(text, char_offset):
    return len(text[:char_offset].encode("utf-8"))


def _error(text, code, char_offset, action_index=0):
    raise WireError(code, _byte_offset(text, char_offset), action_index)


def _skip_layout(text, offset, stop=None):
    stop = len(text) if stop is None else stop
    while offset < stop:
        if text[offset].isspace():
            offset += 1
            continue
        if text[offset] == ";":
            newline = text.find("\n", offset + 1, stop)
            offset = stop if newline < 0 else newline + 1
            continue
        break
    return offset


def _read_expression(text, offset, action_index, stop=None):
    stop = len(text) if stop is None else stop
    if offset >= stop or text[offset] != "(":
        _error(text, WireErrorCode.ACTION_NOT_EXPRESSION, offset, action_index)

    depth = 0
    in_string = False
    escaped = False
    i = offset
    while i < stop:
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
        elif char == ";":
            newline = text.find("\n", i + 1, stop)
            i = stop if newline < 0 else newline + 1
            continue
        elif char == "(":
            depth += 1
            if depth > MAX_NESTING:
                _error(text, WireErrorCode.SYNTAX, i, action_index)
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
            if depth < 0:
                _error(text, WireErrorCode.SYNTAX, i, action_index)
        i += 1
    _error(text, WireErrorCode.SYNTAX, stop, action_index)


def _validate_action_head(text, start, end, action_index):
    head = _skip_layout(text, start + 1, end - 1)
    if head >= end - 1 or text[head] in {'"', "(", ")", "$"}:
        _error(text, WireErrorCode.INVALID_ACTION_HEAD, head, action_index)
    token_end = head
    while token_end < end - 1:
        char = text[token_end]
        if char.isspace() or char in "();":
            break
        token_end += 1
    if token_end == head:
        _error(text, WireErrorCode.INVALID_ACTION_HEAD, head, action_index)


def _is_affect_line(value):
    return bool(_UNICODE_AFFECT.fullmatch(value) or _ASCII_AFFECT.fullmatch(value))


def _read_affect(text, offset, action_index):
    tail = text[offset:].strip()
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    if len(lines) > 1 and all(_is_affect_line(line) for line in lines):
        _error(text, WireErrorCode.DUPLICATE_AFFECT, offset, action_index)
    if len(lines) != 1 or not _is_affect_line(lines[0]):
        _error(text, WireErrorCode.INVALID_AFFECT, offset, action_index)
    return lines[0]


def _read_sequence(text, start=0, stop=None, allow_outer_list=False):
    stop = len(text) if stop is None else stop
    actions = []
    spans = []
    affect = None
    offset = start
    while True:
        offset = _skip_layout(text, offset, stop)
        if offset >= stop:
            break
        if text.startswith("⋄⟨", offset) or text[offset] == "[":
            affect = _read_affect(text[:stop], offset, len(actions))
            offset = stop
            break
        if text[offset] != "(":
            _error(text, WireErrorCode.ACTION_NOT_EXPRESSION, offset, len(actions))
        end = _read_expression(text, offset, len(actions), stop)
        head = _skip_layout(text, offset + 1, end - 1)
        outer_list_shape = (
            allow_outer_list
            and not actions
            and (head >= end - 1 or text[head] == "(")
        )
        if not outer_list_shape:
            _validate_action_head(text, offset, end, len(actions))
        actions.append(text[offset:end].strip())
        spans.append((offset, end))
        if len(actions) > MAX_ACTIONS:
            _error(text, WireErrorCode.TOO_MANY_ACTIONS, offset, len(actions) - 1)
        offset = end
    return actions, spans, affect


def decode_sexpr_output(value):
    text = str(value)
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise WireError(WireErrorCode.INPUT_TOO_LARGE, MAX_INPUT_BYTES, 0)
    if text.startswith("\ufeff"):
        raise WireError(WireErrorCode.UNSUPPORTED_BOM, 0, 0)

    actions, spans, affect = _read_sequence(text, allow_outer_list=True)
    encoding = "sexpr-sequence"

    # During migration, accept one outer list of actions but never emit it as
    # the canonical wire representation.
    if len(actions) == 1 and affect is None:
        start, end = spans[0]
        inner = _skip_layout(text, start + 1, end - 1)
        if inner >= end - 1:
            actions = []
            encoding = "legacy-sexpr-list"
        elif text[inner] == "(":
            actions, _, inner_affect = _read_sequence(text, start + 1, end - 1)
            if inner_affect is not None:
                _error(text, WireErrorCode.INVALID_AFFECT, inner, 0)
            encoding = "legacy-sexpr-list"

    if not actions:
        raise WireError(WireErrorCode.NO_ACTIONS, len(text.encode("utf-8")), 0)
    if len(actions) > MAX_ACTIONS:
        raise WireError(WireErrorCode.TOO_MANY_ACTIONS, 0, MAX_ACTIONS)
    return DecodedTurn(tuple(actions), affect, encoding)


def _patrick_starts_command_line(line):
    candidate = line.lstrip()
    if candidate.startswith("("):
        candidate = candidate[1:].lstrip()
    if not candidate:
        return False
    command = candidate.split(maxsplit=1)[0].rstrip(")")
    return command in PATRICK_LINE_COMMANDS


def _patrick_command_blocks(text):
    blocks = []
    current = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            if current:
                current.append(raw_line)
            continue
        if _patrick_starts_command_line(raw_line):
            if current:
                blocks.append("\n".join(current).strip())
            current = [raw_line]
        elif current:
            current.append(raw_line)
        else:
            raise WireError(WireErrorCode.INVALID_ACTION_HEAD, 0, 0)
    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def _json_string_if_complete(value):
    if not value.startswith('"'):
        return None
    try:
        decoded, end = json.JSONDecoder().raw_decode(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, str) or value[end:].strip():
        return None
    return decoded


def _patrick_file_arguments(rest, action_index):
    if not rest:
        return ()
    if rest.startswith('"'):
        try:
            filename, end = json.JSONDecoder().raw_decode(rest)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise WireError(WireErrorCode.SYNTAX, 0, action_index) from None
        if not isinstance(filename, str):
            raise WireError(WireErrorCode.SYNTAX, 0, action_index)
        content = rest[end:].strip()
    else:
        parts = rest.split(maxsplit=1)
        filename = parts[0]
        content = parts[1].strip() if len(parts) > 1 else ""
    arguments = [json.dumps(filename, ensure_ascii=False)]
    if content:
        decoded = _json_string_if_complete(content)
        arguments.append(json.dumps(
            content if decoded is None else decoded,
            ensure_ascii=False,
        ))
    return tuple(arguments)


def decode_patrick_line_output(value):
    text = str(value)
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise WireError(WireErrorCode.INPUT_TOO_LARGE, MAX_INPUT_BYTES, 0)
    if text.startswith("\ufeff"):
        raise WireError(WireErrorCode.UNSUPPORTED_BOM, 0, 0)

    actions = []
    for action_index, block in enumerate(_patrick_command_blocks(text)):
        # Establish command boundaries before decoding payload escape tokens.
        # An encoded newline in relayed text must not gain command authority.
        line = block.replace("_quote_", '"').replace("_newline_", "\n").strip()
        if line.startswith("(-"):
            line = "(pin -" + line[2:]
        elif line.startswith("-"):
            line = "pin " + line
        if line.startswith("(") and line.endswith(")"):
            line = line[1:-1].strip()
        elif line.startswith("("):
            line = line[1:].strip()
        parts = line.split(maxsplit=1)
        if not parts or parts[0] not in PATRICK_LINE_COMMANDS:
            raise WireError(WireErrorCode.INVALID_ACTION_HEAD, 0, action_index)
        command = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""
        if command in {"write-file", "append-file"}:
            arguments = _patrick_file_arguments(rest, action_index)
        elif rest:
            decoded = _json_string_if_complete(rest)
            arguments = (
                json.dumps(rest if decoded is None else decoded, ensure_ascii=False),
            )
        else:
            arguments = ()
        actions.append(
            "(" + " ".join((command, *arguments)).rstrip() + ")"
        )

    # Reuse the strict decoder as the syntax, action-count, and AST boundary.
    decoded = decode_sexpr_output("\n".join(actions))
    return DecodedTurn(decoded.actions, decoded.affect, "patrick-lines")


def observe_model_output(value):
    observations = []
    for name, decoder in (
        ("StrictSexpr", decode_sexpr_output),
        ("PatrickLines", decode_patrick_line_output),
    ):
        try:
            decoded = decoder(value)
        except WireError as error:
            observations.append(
                DecoderObservation(name, False, error_code=error.code.value)
            )
        else:
            observations.append(
                DecoderObservation(
                    name,
                    True,
                    action_count=len(decoded.actions),
                    encoding=decoded.encoding,
                )
            )
    return tuple(observations)

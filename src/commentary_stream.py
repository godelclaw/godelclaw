"""Bounded provisional public-text decoding; never a completion/command parser.

Only the first authored commentary JSON string is eligible for publication.
Final provider decoding remains independent and authoritative for commands.
"""
import json


class EnvelopePrefix:
    def __init__(self, publication, limit=16384):
        self.publication, self.limit = publication, limit
        self.parts, self.size = [], 0
        self.quoted = self.escaped = self.done = False

    def feed(self, text):
        if self.done:
            return
        for char in text:
            self.size += len(char.encode('utf-8'))
            if self.size > self.limit:
                self.done = True
                self.parts.clear()
                return
            self.parts.append(char)
            if self.quoted:
                if self.escaped:
                    self.escaped = False
                elif char == '\\':
                    self.escaped = True
                elif char == '"':
                    self.quoted = False
            elif char == '"':
                self.quoted = True
            elif char == ',':
                self.done = True
                self.publication.prefix(''.join(self.parts))
                self.parts.clear()
                return


class Stream:
    """One forward-only SSE cursor, bounded lines/events and public prefix.

    Reasoning, summaries, encrypted content, tools and completed-item bodies
    are intentionally not sources. Malformed streams disable this observer;
    they cannot alter the separate terminal decoder's result.
    """
    def __init__(self, publication, protocol, cap):
        self.prefix = EnvelopePrefix(publication)
        self.protocol, self.cap = protocol, int(cap)
        self.offset = self.total = self.events = 0
        self.line, self.data, self.event = [], [], ''
        self.line_size = self.event_size = 0
        self.after_cr = self.disabled = False
        self.identity = self.output = None
        self.finished = False

    def feed(self, text):
        if self.disabled or self.prefix.done:
            return
        try:
            first = self.total == 0
            self.total += len(text.encode('utf-8'))
            if self.total > self.cap:
                raise ValueError('observer byte bound')
            for index, char in enumerate(text):
                if first and index == 0 and char == '\ufeff':
                    continue
                if self.after_cr:
                    self.after_cr = False
                    if char == '\n':
                        continue
                if char in '\r\n':
                    self._line(''.join(self.line))
                    self.line, self.line_size = [], 0
                    self.after_cr = char == '\r'
                    if self.prefix.done:
                        return
                else:
                    self.line_size += len(char.encode('utf-8'))
                    if self.line_size > 65536:
                        raise ValueError('observer line bound')
                    self.line.append(char)
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            self.disabled = True
            self.line, self.data = [], []

    def _line(self, line):
        if not line:
            if self.data:
                self.events += 1
                if self.events > 65536 or self.finished:
                    raise ValueError('observer event bound or terminal')
                self._event(self.event, '\n'.join(self.data))
            self.data, self.event, self.event_size = [], '', 0
            return
        if line.startswith(':'):
            return
        field, sep, value = line.partition(':')
        value = value[1:] if value.startswith(' ') else value
        if field == 'event':
            self.event = value
        elif field == 'data':
            self.event_size += len(value.encode('utf-8')) + 1
            if self.event_size > 65536:
                raise ValueError('observer event bound')
            self.data.append(value if sep else '')

    def _event(self, event, data):
        if data == '[DONE]':
            self.finished = True
            return
        value = json.loads(data)
        if not isinstance(value, dict) or event == 'error' or 'error' in value:
            raise ValueError('observer provider error')
        if self.protocol == 'chat-completions':
            identity = value.get('id')
            if not isinstance(identity, str) or not identity:
                raise ValueError('observer identity')
            if self.identity is None:
                self.identity = identity
            if identity != self.identity:
                raise ValueError('observer mixed identity')
            choices = value['choices']
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError('observer choices')
            for choice in choices:
                if not isinstance(choice, dict):
                    raise ValueError('observer choice')
                if type(choice.get('index')) is not int or choice['index'] != 0:
                    raise ValueError('observer choice')
                delta = choice['delta']
                if not isinstance(delta, dict) or delta.get('role') not in (None, 'assistant'):
                    raise ValueError('observer delta')
                if delta.get('refusal') or delta.get('tool_calls'):
                    raise ValueError('observer nontext')
                text = delta.get('content')
                if text is not None:
                    if not isinstance(text, str):
                        raise ValueError('observer text')
                    self.prefix.feed(text)
                if choice.get('finish_reason') is not None:
                    self.finished = True
        else:
            kind = value.get('type', event)
            if event and event != kind:
                raise ValueError('observer event type')
            if kind in ('response.failed', 'response.incomplete', 'error'):
                raise ValueError('observer response failed')
            if kind == 'response.completed':
                self.finished = True
            if kind == 'response.output_text.delta':
                output = (value.get('item_id'), value.get('output_index'), value.get('content_index'))
                if (not isinstance(output[0], str) or not output[0] or
                        any(type(i) is not int or i < 0 for i in output[1:])):
                    raise ValueError('observer output identity')
                if self.output is None:
                    self.output = output
                if output != self.output:
                    raise ValueError('observer mixed output')
                text = value['delta']
                if not isinstance(text, str):
                    raise ValueError('observer text')
                self.prefix.feed(text)

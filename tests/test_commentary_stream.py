"""Provisional commentary framing is bounded and separate from commands."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from commentary_stream import Stream
import model_commentary
from test_provider_stream import frame
from test_responses_stream_engine import event


class Sink:
    def __init__(self): self.notes = []
    def status(self): return True, 1
    def add(self, text, epoch): self.notes.append(text); return True


class StreamTests(unittest.TestCase):
    def observer(self, protocol='chat-completions', cap=1048576):
        sink = Sink()
        return Stream(model_commentary.Publication(sink), protocol, cap), sink

    def test_every_character_split_unicode_escape_and_crlf(self):
        for ascii_ in (True, False):
            text = json.dumps({'commentary':'Check ž 🐈, newline\nnext.', 'commands':'((nop))'}, ensure_ascii=ascii_)
            body = '\ufeff:ping\n\n' + frame({'reasoning_content':'PRIVATE'})
            body += ''.join(frame({'content':c}) for c in text)
            body = body.replace('\n', '\r\n')
            for step in (1, 7, len(body)):
                observer, sink = self.observer()
                for i in range(0, len(body), step): observer.feed(body[i:i+step])
                self.assertEqual(sink.notes, ['Check ž 🐈, newline\nnext.'])

    def test_no_publication_until_complete_first_string_and_separator(self):
        observer, sink = self.observer()
        for text in ('{"commentary":', '"ready"'):
            observer.feed(frame({'content':text}))
            self.assertEqual(sink.notes, [])
        observer.feed(frame({'content':', "commands":"((shell'}))
        self.assertEqual(sink.notes, ['ready'])
        self.assertFalse(hasattr(observer, 'commands'))

    def test_reasoning_tools_and_nonfirst_commentary_are_not_sources(self):
        for delta in ({'reasoning_content':'{"commentary":"private",'},
                      {'reasoning':'{"commentary":"private",'},
                      {'tool_calls':[{'arguments':'{"commentary":"private",'}]},
                      {'refusal':'{"commentary":"private",'},
                      {'content':'{"commands":"((nop))", "commentary":"later",'}):
            observer, sink = self.observer(); observer.feed(frame(delta))
            self.assertEqual(sink.notes, [])

    def test_response_deltas_only(self):
        observer, sink = self.observer('responses')
        for kind in ('response.reasoning_text.delta', 'response.reasoning_summary_text.delta',
                     'response.function_call_arguments.delta', 'response.output_item.done'):
            observer.feed(event({'type':kind, 'delta':'{"commentary":"private",',
                                 'item':{'text':'{"commentary":"private",'}}))
        source = json.dumps({'commentary':'Responses ž 🐈', 'commands':'((nop))'}, ensure_ascii=True)
        for c in source:
            observer.feed(event({'type':'response.output_text.delta', 'item_id':'msg',
                'output_index':1, 'content_index':0, 'delta':c}))
        self.assertEqual(sink.notes, ['Responses ž 🐈'])

    def test_malformed_and_mixed_streams_disable_observer(self):
        for body in ('data: {\n\n', frame({'content':'{'}) + frame({'content':'x'}, id_='foreign'),
                     'data: {"id":"x","choices":[null]}\n\n',
                     frame({'content':3}), frame({}, 'stop') + frame({'content':'x'})):
            observer, sink = self.observer(); observer.feed(body)
            self.assertTrue(observer.disabled)
            observer.feed(frame({'content':'{"commentary":"not shown",'}))
            self.assertEqual(sink.notes, [])

    def test_mixed_response_output_or_missing_identity_is_rejected(self):
        for output in ({}, {'item_id':'msg', 'output_index':True, 'content_index':0},
                       {'item_id':'other', 'output_index':1, 'content_index':0}):
            observer, sink = self.observer('responses')
            observer.feed(event({'type':'response.output_text.delta','item_id':'msg',
                'output_index':1,'content_index':0,'delta':'{"commentary":'}))
            observer.feed(event({'type':'response.output_text.delta',**output,'delta':'"bad",'}))
            self.assertTrue(observer.disabled)
            self.assertEqual(sink.notes, [])

    def test_bounds(self):
        for body, cap in (('x'*65537, 100000), ('data: '+'x'*65530+'\ndata: xxxxx\n',100000),
                          (frame({'content':'x'*17000}),100000), (frame({'content':'x'}),4)):
            observer, sink = self.observer(cap=cap); observer.feed(body)
            self.assertTrue(observer.disabled or observer.prefix.done)
            self.assertEqual(sink.notes, [])

    def test_sse_multiline_data(self):
        observer, sink = self.observer()
        body = frame({'content':'{"commentary":"ready",'}).replace('"choices":', '\ndata: "choices":')
        observer.feed(body)
        self.assertEqual(sink.notes, ['ready'])


if __name__ == '__main__': unittest.main()

"""Streaming identity, framing, completion and tool-batch controls."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import provider_stream


def frame(delta, finish=None, id_="reply", usage=None):
    result = dict(id=id_, choices=[dict(index=0, delta=delta, finish_reason=finish)])
    if usage is not None:
        result["usage"] = usage
    return "data: " + json.dumps(result, ensure_ascii=False) + "\n\n"


class ProviderStreamTest(unittest.TestCase):
    def test_reasoning_text_unicode_and_final_usage(self):
        stream = (": ping\n\n" + frame({"role": "assistant", "reasoning_content": "thinking"}) +
                  frame({"content": "žluťoučký 🐈"}) + frame({}, "stop") +
                  'data: {"id":"reply","choices":[],"usage":{"completion_tokens":12}}\n\n' +
                  "data: [DONE]\n\n")
        result = json.loads(provider_stream.chat(stream))
        self.assertEqual(result["choices"][0]["message"]["content"], "žluťoučký 🐈")
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"], "thinking")
        self.assertEqual(result["usage"]["completion_tokens"], 12)
        self.assertEqual(provider_stream.chat(stream.replace("\n", "\r\n")), provider_stream.chat(stream))
        self.assertEqual(provider_stream.chat(stream.replace("\n", "\r")), provider_stream.chat(stream))

    def test_interleaved_tool_deltas_keep_their_own_arguments(self):
        stream = frame({"tool_calls": [
            dict(index=1, id="two", type="function", function=dict(name="second", arguments="{")),
            dict(index=0, id="one", type="function", function=dict(name="first", arguments="{"))]})
        stream += frame({"tool_calls": [dict(index=0, function=dict(arguments='"x":1}'))]})
        stream += frame({"tool_calls": [dict(index=1, function=dict(arguments='"y":2}'))]}, "tool_calls")
        stream += "data: [DONE]\n\n"
        tools = json.loads(provider_stream.chat(stream))["choices"][0]["message"]["tool_calls"]
        self.assertEqual([t["id"] for t in tools], ["one", "two"])
        self.assertEqual([json.loads(t["function"]["arguments"]) for t in tools], [{"x": 1}, {"y": 2}])

    def test_incomplete_and_foreign_data_cannot_produce_a_response(self):
        finished = frame({"content": "candidate"}, "stop")
        for stream in (finished, finished + 'data: {"id":"reply"}', frame({}) + "data: [DONE]\n\n",
                       frame({"content": "x"}) + frame({}, "stop", "other") + "data: [DONE]\n\n",
                       finished + "data: [DONE]\n\n" + frame({}),
                       'event: error\ndata: {"error":"no"}\n\n'):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                provider_stream.chat(stream)

    def test_complete_done_marker_at_eof_is_not_a_truncated_answer(self):
        complete = frame({"content": "ok"}, "stop")
        for ending in ("data: [DONE]", "data: [DONE]\n", "data: [DONE]\r\n"):
            with self.subTest(ending=ending):
                result = json.loads(provider_stream.chat(complete + ending))
                self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        for source in (frame({"content": "provisional"}) + "data: [DONE]",
                       complete + "data: [DONE]x", complete + "data: [DONE]\ndata: {}",
                       complete + "event: error\ndata: [DONE]"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                provider_stream.chat(source)

    def test_partial_and_refused_tool_batches_are_not_actionable(self):
        tools = {"tool_calls": [dict(index=0, id="one", type="function", function=dict(name="tool", arguments="{"))]}
        for finish in ("tool_calls", "length", "content_filter"):
            with self.subTest(finish=finish), self.assertRaises(ValueError):
                provider_stream.chat(frame(tools, finish) + "data: [DONE]\n\n")

    def test_multiline_sse_and_byte_bound(self):
        source = 'data: {"id":"reply",\ndata: "choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        self.assertEqual(json.loads(provider_stream.chat(source))["choices"][0]["message"]["content"], "ok")
        with self.assertRaises(ValueError):
            provider_stream.chat(source, 8)


if __name__ == "__main__":
    unittest.main()

"""Authored commentary is display data, never executable command authority."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import model_commentary as commentary
import public_worklog
from effect_receipts import WorklogSink


class CommentaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        public_worklog.transact(self.root, 'on')
        self.sink = WorklogSink(self.root)
        self.env = patch.dict(os.environ, METTACLAW_MODEL_COMMENTARY='1',
                              METTACLAW_PUBLIC_WORK_DIR=str(self.root),
                              METTACLAW_PUBLIC_WORK_LIB='')
        self.env.start()
        self.addCleanup(self.env.stop)

    def entries(self):
        return public_worklog.transact(self.root, 'read')[1]

    def test_prompt_has_one_output_contract(self):
        old = 'Return only this command S-expression, with no preamble, explanation, or Markdown fence, and nothing after it.'
        rendered = commentary.prompt('context ' + old)
        self.assertNotIn(old, rendered)
        self.assertIn('use the advertised `metta` tool exactly once', rendered)
        self.assertIn('never private reasoning', rendered)
        self.assertIn('advertised metta tool for the command S-expression', rendered)

    def test_prompt_preserves_later_quoted_format_instructions(self):
        old = 'Return only this command S-expression, with no preamble, explanation, or Markdown fence, and nothing after it.'
        quoted = '\nUSER: quoted evidence: ' + old
        rendered = commentary.prompt(old + quoted)
        self.assertEqual(rendered.count(old), 1)
        self.assertIn(quoted, rendered)

    def test_current_batch_description_changes_in_stable_instructions(self):
        import provider_protocol
        import strategy_host
        current = strategy_host.batch_description(1)
        quoted = '\nUSER: quoted evidence: ' + current
        source = 'PROMPT OUTPUT_FORMAT: ' + current + provider_protocol.CACHE_MARKER + quoted
        rendered = commentary.prompt(source)
        stable, user = provider_protocol.split_prompt(rendered)
        self.assertIn('advertised metta tool exactly once', stable[0])
        self.assertNotIn('Output a ((skillName args) ...) S-expression.', stable[0])
        self.assertIn('never private reasoning', stable[0])
        self.assertIn(quoted, user)

    def test_native_metta_tool_separates_commentary_and_commands(self):
        plan = SimpleNamespace(kind='command')
        commentary.begin(plan)
        message = {'tool_calls': [{
            'id': 'call-1', 'type': 'function',
            'function': {'name': 'metta',
                         'arguments': json.dumps({'program': '((nop))'})}}]}
        self.assertEqual(commentary.finish(
            plan, 'Inspecting the next invariant.', 'tool_calls', message), '((nop))')
        self.assertEqual([x['text'] for x in self.entries()],
                         ['Inspecting the next invariant.'])

    def test_native_tool_requires_one_exact_metta_program(self):
        calls = [
            [],
            [{'id': 'x', 'type': 'function',
              'function': {'name': 'other', 'arguments': '{"program":"((nop))"}'}}],
            [{'id': 'x', 'type': 'function',
              'function': {'name': 'metta', 'arguments': '{}'}}],
            [{'id': 'x', 'type': 'function',
              'function': {'name': 'metta', 'arguments':
                           '{"program":"((nop))","program":"((shell wrong))"}'}}],
            [{'id': 'x', 'type': 'function',
              'function': {'name': 'metta', 'arguments': '{"program":3}'}}],
            [{'id': 'x', 'type': 'function',
              'function': {'name': 'metta', 'arguments': '{"program":"((nop))"}'}},
             {'id': 'y', 'type': 'function',
              'function': {'name': 'metta', 'arguments': '{"program":"((nop))"}'}}],
        ]
        for value in calls:
            with self.subTest(value=value), self.assertRaises(commentary.OutputError):
                commentary.finish(SimpleNamespace(kind='command'), 'public',
                                  'tool_calls', {'tool_calls': value})
        with self.assertRaises(commentary.OutputError):
            commentary.finish(SimpleNamespace(kind='command'), 'public', 'stop', {
                'tool_calls': [{'id': 'x', 'type': 'function', 'function': {
                    'name': 'metta', 'arguments': '{"program":"((nop))"}'}}]})
        self.assertEqual(self.entries(), [])

    def test_structured_calls_are_not_reinterpreted(self):
        plan = SimpleNamespace(kind='structured')
        commentary.begin(plan)
        content = '{"commentary":"ignored","commands":"((nop))"}'
        self.assertFalse(hasattr(plan, 'commentary'))
        self.assertEqual(commentary.finish(plan, content, 'stop'), content)
        self.assertEqual(self.entries(), [])

    def test_internal_compaction_creates_no_public_observer(self):
        plan = SimpleNamespace(kind='command', purpose='compaction', protocol='responses', cap=4096)
        commentary.begin(plan)
        self.assertFalse(hasattr(plan, 'commentary'))
        self.assertFalse(hasattr(plan, 'commentary_stream'))
        self.assertEqual(self.entries(), [])

    def test_legacy_commands_unchanged(self):
        self.assertEqual(commentary.split('((nop))'), (None, '((nop))'))

    def test_prose_and_unframed_data_cannot_enter_command_recovery(self):
        envelope = json.dumps({'commentary': 'Example ((nop))', 'commands': '((nop))'})
        for source in ('Here is my output: ' + envelope,
                       'Working on ((nop))', 'nop'):
            with self.subTest(source=source):
                self.assertEqual(commentary.split(source), (None, '()'))

    def test_single_fence_preserves_the_exact_envelope_and_terminal_gate(self):
        value = json.dumps(dict(commentary='Checked λ\u2028; literal ``` in data.', commands='((nop))'), ensure_ascii=False)
        for header in ('```json', '```JSON', '```'):
            framed = header + '\n' + value + '\n```'
            self.assertEqual(commentary.split(framed), commentary.split(value))
            for cut in range(1, len(framed)):
                self.assertEqual(commentary.split(framed[:cut])[1], '()')
            plan = SimpleNamespace(kind='command')
            for reason in ('length', 'refusal', 'content_filter', 'tool_calls', 'unknown'):
                with self.assertRaises(commentary.OutputError):
                    commentary.finish(plan, framed, reason)
            with self.assertRaises(commentary.OutputError):
                commentary.finish(plan, framed, 'stop', {'refusal': 'declined'})
            self.assertEqual(commentary.finish(plan, framed, 'stop'), '((nop))')

    def test_fences_do_not_admit_surrounding_prose_or_other_programs(self):
        value = json.dumps(dict(commentary='Example ((shell "wrong"))', commands='((nop))'))
        for framed in ('prefix\n```json\n' + value + '\n```',
                       '```json\n' + value + '\n```\nsuffix',
                       '```json\n' + value + '\n```\n```json\n' + value + '\n```',
                       '```metta\n((shell "wrong"))\n```',
                       '```\n((shell "wrong"))\n```',
                       '```json\n{"commentary":"ok","commands":"((nop))","commands":"((shell wrong))"}\n```'):
            self.assertEqual(commentary.split(framed), (None, '()'))

    def test_complete_envelope_separates_authored_text(self):
        content = json.dumps({'commentary': 'Checking the display.', 'commands': '((nop))'})
        plan = SimpleNamespace(kind='command')
        commentary.begin(plan)
        self.assertEqual(commentary.finish(plan, content, 'stop'), '((nop))')
        self.assertEqual(self.entries()[0]['text'], 'Checking the display.')
        commentary.finish(plan, content, 'stop')
        self.assertEqual(len(self.entries()), 1)

    def test_partial_and_invalid_envelopes_release_no_commands(self):
        for text in ('{"commentary":"hi", "commands":"((shell',
                     '{"commentary":4,"commands":"((nop))"}',
                     '{"commentary":"a","commentary":"b","commands":"((nop))"}',
                     '{"commentary":"a","commands":"((nop))","reasoning":"private"}',
                     '{"commentary":"a","commands":"((nop))"} trailing'):
            with self.subTest(text=text):
                self.assertEqual(commentary.split(text), (None, '()'))

    def test_every_prefix_never_releases_commands(self):
        value = json.dumps({'commentary': 'Check žluťoučký 🐈\nnext.', 'commands': '((nop))'}, ensure_ascii=True)
        for cut in range(1, len(value)):
            part = value[:cut]
            self.assertEqual(commentary.split(part)[1], '()')
        self.assertEqual(commentary.split(value)[1], '((nop))')

    def test_early_complete_comment_only_once(self):
        publication = commentary.Publication(self.sink)
        source = json.dumps({'commentary': 'Check ž 🐈\nnext.', 'commands': '((shell "not run"))'})
        for index in range(len(source) + 1):
            publication.prefix(source[:index])
        self.assertEqual([x['text'] for x in self.entries()], ['Check ž 🐈\nnext.'])

    def test_reasoning_and_command_fields_are_not_public_sources(self):
        for source in ('{"reasoning":"private", "commentary":"ignored",',
                       '{"commands":"((send \\"private\\"))",',
                       'private prose ((nop))',
                       '{"commentary": "partial\\uD83D',
                       '{"commentary": "not finished"'):
            self.assertEqual(commentary.Publication(self.sink).prefix(source), 0)
        self.assertEqual(self.entries(), [])

    def test_off_on_epoch_cannot_replay_old_output(self):
        publication = commentary.Publication(self.sink)
        public_worklog.transact(self.root, 'off')
        public_worklog.transact(self.root, 'on')
        self.assertEqual(publication.publish('old call'), 0)
        self.assertEqual(self.entries(), [])

    def test_off_at_call_start_drops_even_if_enabled_later(self):
        public_worklog.transact(self.root, 'off')
        publication = commentary.Publication(self.sink)
        public_worklog.transact(self.root, 'on')
        self.assertEqual(publication.publish('old call'), 0)
        self.assertEqual(self.entries(), [])

    def test_length_and_refusal_do_not_release_enveloped_commands(self):
        source = json.dumps({'commentary': 'not final', 'commands': '((nop))'})
        for reason in ('length', 'refusal', 'content_filter'):
            plan = SimpleNamespace(kind='command')
            commentary.begin(plan)
            with self.assertRaises(commentary.OutputError):
                commentary.finish(plan, source, reason)
        self.assertEqual(self.entries(), [])

    def test_terminal_gate_also_applies_to_legacy_commands(self):
        for source in ('((nop))', '{"commentary":"hi","commands":"((nop))"}'):
            for reason in ('length', 'refusal', 'content_filter', 'tool_calls', 'unknown', None):
                with self.subTest(source=source, reason=reason):
                    with self.assertRaises(commentary.OutputError):
                        commentary.finish(SimpleNamespace(kind='command'), source, reason)
        self.assertEqual(self.entries(), [])

    def test_refusal_or_tool_calls_with_stop_are_not_commands(self):
        for message in ({'refusal': 'fixture refusal'}, {'tool_calls': [{'id': 'fixture'}]}):
            with self.assertRaises(commentary.OutputError):
                commentary.finish(SimpleNamespace(kind='command'), '((nop))', 'stop', message)
        self.assertEqual(self.entries(), [])

    def test_invalid_output_signals_failure_not_successful_noop(self):
        for source in ('', 'prose', '{"commentary":"hi","commands":"((nop))"} trailing'):
            with self.assertRaises(commentary.OutputError):
                commentary.finish(SimpleNamespace(kind='command'), source, 'stop')
        self.assertEqual(commentary.finish(SimpleNamespace(kind='command'), '()', 'stop'), '()')

    def test_disabled_is_exact_compatibility(self):
        source = '{"commentary":"public","commands":"((nop))"}'
        with patch.dict(os.environ, METTACLAW_MODEL_COMMENTARY='0'):
            self.assertEqual(commentary.prompt('legacy'), 'legacy')
            self.assertEqual(commentary.finish(SimpleNamespace(kind='command'), source, 'stop'), source)

    def test_sink_failure_is_nonfatal_and_not_retried(self):
        class Broken:
            def status(self): return True, 1
            def add(self, *args): raise OSError('not public')
        publication = commentary.Publication(Broken())
        self.assertEqual(publication.publish('hello'), 0)
        self.assertTrue(publication.published)


if __name__ == '__main__':
    unittest.main()

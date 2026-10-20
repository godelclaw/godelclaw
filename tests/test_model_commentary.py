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
        self.assertIn('return one JSON object', rendered)
        self.assertIn('not private reasoning', rendered)
        self.assertIn('commands field must contain only', rendered)

    def test_structured_calls_are_not_reinterpreted(self):
        plan = SimpleNamespace(kind='structured')
        commentary.begin(plan)
        content = '{"commentary":"ignored","commands":"((nop))"}'
        self.assertFalse(hasattr(plan, 'commentary'))
        self.assertEqual(commentary.finish(plan, content, 'stop'), content)
        self.assertEqual(self.entries(), [])

    def test_legacy_commands_unchanged(self):
        self.assertEqual(commentary.split('((nop))'), (None, '((nop))'))

    def test_prose_and_fenced_envelopes_cannot_enter_command_recovery(self):
        envelope = json.dumps({'commentary': 'Example ((nop))', 'commands': '((nop))'})
        for source in ('```json\n' + envelope + '\n```',
                       'Here is my output: ' + envelope,
                       'Working on ((nop))', 'nop'):
            with self.subTest(source=source):
                self.assertEqual(commentary.split(source), (None, '()'))

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
            self.assertEqual(commentary.finish(plan, source, reason), '()')
        self.assertEqual(self.entries(), [])

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

"""Native instruction discovery against the pinned Codex discovery contract.

Reference: openai/codex 6aaac69480, core/src/agents_md.rs and its root/cwd,
override, worktree-marker and absent-document test cases. These fixtures are
independent; no reference implementation code is copied. Complete reads and
visible failures intentionally replace the reference's byte clipping.
"""
import hashlib
import itertools
import json
import os
from pathlib import Path
import unittest

import test_coding_session as session


@unittest.skipUnless(os.environ.get('CETTA_BIN'), 'set CETTA_BIN to a native Python/HTTP engine')
class GuidanceTests(unittest.TestCase):
    setUp = session.CodingSessionTests.setUp
    tearDown = session.CodingSessionTests.tearDown
    run_session = session.CodingSessionTests.run_session
    run_probe = session.CodingSessionTests.run_probe
    fake_work_scope = session.CodingSessionTests.fake_work_scope
    session = session.CodingSessionTests.session

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')
        return path

    def repo(self, name='repo', nested='sub/deep', marker_file=False):
        root = self.directory / name
        workspace = root / nested
        workspace.mkdir(parents=True)
        if marker_file:
            (root / '.git').write_text('gitdir: external-worktree-marker\n')
        else:
            (root / '.git').mkdir()
        return root, workspace

    def snapshot(self, path):
        return json.loads((path / 'guidance.json').read_text())['payload']

    def guidance_message(self, request):
        return next(m for m in request['messages'] if m.get('content', '').startswith('# AGENTS.md instructions for '))

    def assert_sources(self, path, expected):
        sources = self.snapshot(path)['sources']
        self.assertEqual([(row['path'], row['text']) for row in sources], [(str(p), s) for p, s in expected])
        for row in sources:
            self.assertEqual(row['text_sha256'], hashlib.sha256(row['text'].encode()).hexdigest())
            self.assertEqual(row['text_bytes'], len(row['text'].encode()))

    def test_global_then_root_to_leaf_override_and_worktree_marker(self):
        root, workspace = self.repo(marker_file=True)
        global_dir = self.directory / 'global'
        self.env['METTACLAW_CODING_GUIDANCE_DIR'] = str(global_dir)
        self.write(global_dir / 'AGENTS.override.md', '  \n ')
        global_doc = self.write(global_dir / 'AGENTS.md', '  global rule  \n')
        above = self.write(root.parent / 'AGENTS.md', 'OUTSIDE_REPOSITORY')
        top = self.write(root / 'AGENTS.md', 'Root rule.\n\nKeep "quotes" and \\slashes. λ\x00\n')
        middle = self.write(root / 'sub/AGENTS.override.md', 'Mid override')
        self.write(root / 'sub/AGENTS.md', 'SHADOWED_STANDARD')
        leaf = self.write(workspace / 'AGENTS.md', 'Leaf rule')
        self.write(root / 'sibling/AGENTS.md', 'WRONG_SIBLING')
        state = self.session('Follow the applicable project rules.', workspace)
        self.run_session(state)
        self.assert_sources(state, [(global_doc, 'global rule'), (top, top.read_text()),
                                    (middle, 'Mid override'), (leaf, 'Leaf rule')])
        message = self.guidance_message(self.server.requests[0])['content']
        self.assertLess(message.index('global rule'), message.index('Root rule'))
        self.assertLess(message.index('Root rule'), message.index('Mid override'))
        self.assertLess(message.index('Mid override'), message.index('Leaf rule'))
        self.assertIn(top.read_text(), message)
        for excluded in ('OUTSIDE_REPOSITORY', 'SHADOWED_STANDARD', 'WRONG_SIBLING'):
            self.assertNotIn(excluded, message)
        self.assertNotIn('_coding_guidance', json.dumps(self.server.requests))
        self.assertTrue(above.is_file())
        self.run_session(state)
        self.assertEqual(self.guidance_message(self.server.requests[0]), self.guidance_message(self.server.requests[1]))

    def test_no_repository_uses_only_cwd_and_empty_override_masks_standard(self):
        workspace = self.directory / 'unversioned/leaf'; workspace.mkdir(parents=True)
        self.write(workspace.parent / 'AGENTS.md', 'OUTSIDE_UNVERSIONED')
        self.write(workspace / 'AGENTS.override.md', ' \n')
        self.write(workspace / 'AGENTS.md', 'SHADOWED_BY_EMPTY_OVERRIDE')
        state = self.session('No invented parent scope.', workspace)
        self.run_session(state)
        self.assertEqual(self.snapshot(state)['sources'], [])
        self.assertEqual(len(self.server.requests[0]['messages']), 2)
        self.assertEqual(self.snapshot(state)['project_root'], str(workspace))

    def test_nearest_nested_repository_and_symbolic_workspace(self):
        outer, _ = self.repo(nested='nested')
        nested = outer / 'nested'; (nested / '.git').mkdir()
        leaf = nested / 'work'; leaf.mkdir()
        self.write(outer / 'AGENTS.md', 'OUTER_REPOSITORY')
        doc = self.write(nested / 'AGENTS.md', 'INNER_REPOSITORY')
        alias = self.directory / 'workspace-alias'; alias.symlink_to(leaf, target_is_directory=True)
        state = self.session('Use physical workspace scope.', alias)
        self.run_session(state)
        self.assert_sources(state, [(doc, 'INNER_REPOSITORY')])
        self.assertEqual(self.snapshot(state)['workspace'], str(leaf))

    def test_custom_markers_and_fallback_names(self):
        root, workspace = self.repo()
        self.env.update(METTACLAW_CODING_ROOT_MARKERS='[".project-root"]',
                        METTACLAW_CODING_DOC_FALLBACKS='["TEAM_GUIDE.txt"]')
        self.write(root / '.project-root', '')
        doc = self.write(workspace / 'TEAM_GUIDE.txt', 'fallback rules')
        state = self.session('Configured discovery.', workspace)
        self.run_session(state)
        self.assert_sources(state, [(doc, 'fallback rules')])
        self.assertEqual(self.snapshot(state)['project_root'], str(root))

    def test_empty_markers_disable_ancestor_search(self):
        root, workspace = self.repo()
        self.env['METTACLAW_CODING_ROOT_MARKERS'] = '[]'
        self.write(root / 'AGENTS.md', 'ANCESTOR_NOT_SELECTED')
        doc = self.write(workspace / 'AGENTS.md', 'only cwd')
        state = self.session('No root walk.', workspace)
        self.run_session(state)
        self.assert_sources(state, [(doc, 'only cwd')])

    def test_complete_large_document_and_invalid_utf8_replacement(self):
        root, workspace = self.repo()
        large = 'λ rule\n' * 7000 + 'END_OF_COMPLETE_GUIDANCE'
        doc = self.write(root / 'AGENTS.md', large)
        invalid = self.write(workspace / 'AGENTS.md', b'bad byte \xff retained with replacement\n')
        state = self.session('Retain complete guidance.', workspace)
        self.run_session(state)
        self.assert_sources(state, [(doc, large), (invalid, invalid.read_bytes().decode('utf-8', 'replace'))])
        self.assertIn(large, self.guidance_message(self.server.requests[0])['content'])

    def test_resume_uses_snapshot_after_source_changes_without_duplicate_messages(self):
        root, workspace = self.repo()
        doc = self.write(root / 'AGENTS.md', 'PINNED_SOURCE_RULE')
        state = self.session('Resume the same task.', workspace)
        self.run_session(state)
        original = (state / 'guidance.json').read_bytes()
        doc.write_text('CHANGED_SOURCE_RULE')
        self.run_session(state)
        doc.unlink()
        self.run_session(state)
        self.assertEqual((state / 'guidance.json').read_bytes(), original)
        for request in self.server.requests:
            self.assertEqual(sum('PINNED_SOURCE_RULE' in m.get('content', '') for m in request['messages']), 1)
            self.assertNotIn('CHANGED_SOURCE_RULE', json.dumps(request))
        history = [json.loads(line) for line in (state / 'messages.jsonl').read_text().splitlines()]
        self.assertEqual(sum('_coding_guidance' in row for row in history), 1)

    def test_compaction_retains_guidance_and_local_markers_stay_off_wire(self):
        root, workspace = self.repo()
        text = 'GUIDANCE_MUST_SURVIVE\nKeep the exact source values. λ'
        self.write(root / 'AGENTS.md', text)
        state = self.session('Continue through compaction.', workspace)
        self.env['METTACLAW_CONTEXT_WINDOW_TOKENS'] = '40000'
        self.server.replies = [
            session.reply('Read evidence.', [session.call('c1', 'exec_command', {'cmd': 'seq 1 30000'})]),
            session.reply('HANDOFF: evidence was read; continue respecting the instructions.'),
            session.reply('Done after compaction.')]
        self.run_session(state)
        self.assertEqual(len(self.server.requests), 3)
        for request in self.server.requests:
            self.assertIn(text, self.guidance_message(request)['content'])
            self.assertNotIn('_coding_guidance', json.dumps(request))
        self.assertEqual(len(list(state.glob('messages.jsonl.*.archived'))), 1)

    def test_unreadable_guidance_fails_before_provider_or_workspace_effect(self):
        root, workspace = self.repo()
        doc = self.write(root / 'AGENTS.md', 'cannot silently omit this rule')
        state = self.session('Do not run without guidance.', workspace)
        doc.chmod(0)
        try:
            self.run_session(state)
        finally:
            doc.chmod(0o600)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(json.loads((state / 'state.json').read_text())['status'], 'failed')
        self.assertIn('Cannot read coding guidance', (state / 'report.txt').read_text())

    def test_invalid_configuration_and_snapshot_corruption_are_visible(self):
        root, workspace = self.repo()
        self.env['METTACLAW_CODING_DOC_FALLBACKS'] = '["../outside.md"]'
        state = self.session('Reject invalid configuration.', workspace)
        self.run_session(state)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(json.loads((state / 'state.json').read_text())['status'], 'failed')
        self.env['METTACLAW_CODING_DOC_FALLBACKS'] = '[]'
        other = self.session('Reject a corrupted snapshot.', workspace, 'other')
        self.run_session(other)
        snapshot = json.loads((other / 'guidance.json').read_text())
        snapshot['payload']['project_root'] = 'tampered'
        (other / 'guidance.json').write_text(json.dumps(snapshot))
        self.server.requests.clear()
        self.run_session(other)
        self.assertEqual(self.server.requests, [])
        self.assertIn('digest', (other / 'report.txt').read_text())

    def test_legacy_conversation_acquires_guidance_once_after_uncertain_tools(self):
        root, workspace = self.repo()
        self.write(root / 'AGENTS.md', 'LEGACY_GUIDANCE')
        state = self.session('Resume existing evidence.', workspace)
        original = [dict(role='system', content='old instructions'), dict(role='user', content='original task'),
                    dict(role='assistant', content='', tool_calls=[session.call('old', 'exec_command', {'cmd': 'must-not-run'})])]
        (state / 'messages.jsonl').write_text(''.join(json.dumps(m)+'\n' for m in original))
        self.run_session(state)
        self.run_session(state)
        for request in self.server.requests:
            self.assertEqual(request['messages'][:3], original)
            self.assertEqual(request['messages'][3]['role'], 'tool')
            self.assertIn('outcome unknown', request['messages'][3]['content'])
            self.assertEqual(sum('LEGACY_GUIDANCE' in m.get('content', '') for m in request['messages']), 1)

    def test_snapshot_without_published_transcript_recovers_exact_guidance(self):
        root, workspace = self.repo()
        doc = self.write(root / 'AGENTS.md', 'PREPARED_GUIDANCE')
        state = self.session('Recover initialization.', workspace)
        self.run_probe('!(println! (PREPARED (guidance:prepare '+json.dumps(str(state))+' '+
                       json.dumps(str(workspace))+' JsonNull)))\n')
        self.assertTrue((state / 'guidance.json').exists())
        self.assertFalse((state / 'messages.jsonl').exists())
        doc.write_text('LATER_REPLACEMENT')
        self.run_session(state)
        self.assertIn('PREPARED_GUIDANCE', self.guidance_message(self.server.requests[0])['content'])
        self.assertNotIn('LATER_REPLACEMENT', json.dumps(self.server.requests[0]))

    def test_guidance_cannot_silently_change_when_the_snapshot_or_message_is_lost(self):
        root, workspace = self.repo()
        doc = self.write(root / 'AGENTS.md', 'ORIGINAL_RULE')
        state = self.session('Preserve the instruction receipt.', workspace)
        self.run_session(state)
        snapshot = (state / 'guidance.json').read_bytes()
        (state / 'guidance.json').unlink()
        doc.write_text('DIFFERENT_RULE')
        self.server.requests.clear()
        self.run_session(state)
        self.assertEqual(self.server.requests, [])
        self.assertFalse((state / 'guidance.json').exists())
        (state / 'guidance.json').write_bytes(snapshot)
        rows = [json.loads(line) for line in (state / 'messages.jsonl').read_text().splitlines()]
        next(row for row in rows if '_coding_guidance' in row)['content'] = 'corrupted guidance'
        (state / 'messages.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.run_session(state)
        self.assertEqual(self.server.requests, [])
        self.assertIn('disagree', (state / 'report.txt').read_text())

    def test_launcher_pins_guidance_options_before_freezing_the_runtime(self):
        self.fake_work_scope()
        runner = self.directory / 'runner'; runner.mkdir()
        workspace = runner / 'repo'; workspace.mkdir()
        global_dir = runner / 'identity'
        global_doc = self.write(global_dir / 'AGENTS.md', 'PRIVATE_GLOBAL_RULE')
        self.env['METTACLAW_CODING_DOC_FALLBACKS'] = '["TEAM_GUIDE.txt"]'
        result = self.run_probe('!(println! (work "Read the rules." "repo"))\n',
                                module='coding_work.metta', cwd=runner)
        self.assertIn('WORK_STARTED', result.stdout)
        state, = (runner / 'memory/coding').iterdir()
        task = json.loads((state / 'task.json').read_text())
        self.assertEqual(task['workdir'], str(workspace))
        self.assertEqual(task['guidance'], dict(home=str(global_dir), root_markers=['.git'],
                                                fallback_filenames=['TEAM_GUIDE.txt']))
        # The actual worker starts in a release copy, whose identity folder is
        # different. Its saved task must keep the originating agent's layer.
        self.env['METTACLAW_CODING_GUIDANCE_DIR'] = str(self.directory / 'different-identity')
        self.run_session(state)
        self.assert_sources(state, [(global_doc, 'PRIVATE_GLOBAL_RULE')])

    def test_empty_snapshot_also_remains_required_after_a_task_has_started(self):
        root, workspace = self.repo()
        state = self.session('Retain an empty instruction snapshot.', workspace)
        task = json.loads((state / 'task.json').read_text())
        task['guidance'] = dict(home='', root_markers=['.git'], fallback_filenames=[])
        (state / 'task.json').write_text(json.dumps(task))
        self.run_session(state)
        self.assertEqual(self.snapshot(state)['sources'], [])
        (state / 'guidance.json').unlink()
        self.write(root / 'AGENTS.md', 'NEW_INSTRUCTIONS')
        self.server.requests.clear()
        self.run_session(state)
        self.assertEqual(self.server.requests, [])
        self.assertFalse((state / 'guidance.json').exists())
        self.assertIn('Saved coding guidance', (state / 'report.txt').read_text())

    def test_generated_per_directory_selection_matches_the_reference_rule(self):
        cases = []
        forms = []
        for number, states in enumerate(itertools.product(('missing', 'empty', 'text', 'directory'), repeat=3)):
            root, workspace = self.repo('case-' + str(number), nested='work')
            names = ('AGENTS.override.md', 'AGENTS.md', 'TEAM_GUIDE.txt')
            expected = []
            for name, kind in zip(names, states):
                path = workspace / name
                if kind == 'directory': path.mkdir()
                elif kind in ('empty', 'text'): path.write_text('' if kind == 'empty' else name + ' λ\n')
            for name in names:
                path = workspace / name
                if path.is_file():
                    value = path.read_text()
                    if value.strip(): expected.append((str(path), value))
                    break
            target = self.directory / ('selected-' + str(number) + '.json')
            options = '(JsonObject ((JsonPair "home" (JsonString "")) (JsonPair "root_markers" (JsonArray ((JsonString ".git")))) (JsonPair "fallback_filenames" (JsonArray ((JsonString "TEAM_GUIDE.txt"))))))'
            forms.append('!(let (GuidanceCollected $root $sources) (guidance:collect '+json.dumps(str(workspace))+' '+options+') '+
                '(fs:update-text '+json.dumps(str(target))+' "replace" (json-stringify (JsonArray $sources)) "" ""))\n')
            cases.append((target, expected, states))
        self.run_probe(''.join(forms))
        trace = []
        for target, expected, states in cases:
            with self.subTest(target=target.name):
                self.assertTrue(target.is_file())
                sources = json.loads(target.read_text())
                self.assertEqual([(row['path'], row['text']) for row in sources], expected)
                trace.append(dict(candidates=list(states),
                                  selected=None if not sources else names.index(Path(sources[0]['path']).name)))
        self.assertEqual(self.server.requests, [])
        if target := os.environ.get('METTACLAW_GUIDANCE_TRACE_PATH'):
            Path(target).write_text(json.dumps(trace) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()

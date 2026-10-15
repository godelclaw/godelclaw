"""Native workspace views over a synthetic store; Python owns fixtures only."""
import unittest
import test_semantic_memory_engine as fixture


class WorkspaceTests(unittest.TestCase):
    setUp = fixture.MemoryTests.setUp
    stop_store = fixture.MemoryTests.stop_store
    run_metta = fixture.MemoryTests.run_metta

    def test_composition_filters_before_top_k_and_preserves_originals(self):
        output = self.run_metta('''
!(println! (TAG (workspace-tag "b" ("task:proof" "group:lab"))))
!(println! (DEFINE (workspace "proof" ("task:proof") () "task:proof")))
!(println! (SELECT (workspace "proof")))
!(println! (MATCH (semantic:query "alpha" 1 "similarity")))
!(println! (PREVIEW (workspace-preview)))
!(println! (READ (memory-read "a")))
!(println! (CATALOGUE (api:help "workspace")))
''')
        self.assertIn('(WorkspaceTagged "b" "add"', output)
        self.assertIn('(MATCH (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(WorkspacePreview "proof" (Matching 1)', output)
        self.assertIn('(MemoryCurrent "a"', output)
        self.assertIn('CommandHelp "workspace" "workspace"', output)
        self.assertEqual(self.collection.get(ids=['a', 'b'], include=['documents'])['documents'],
                         ['old alpha', 'beta reference'])

    def test_overlapping_union_exclusions_nested_views_and_live_wildcards(self):
        output = self.run_metta('''
!(workspace-tag "a" ("task:input:123" "group:lab" "person:alex"))
!(workspace-tag "b" ("task:other" "group:lab"))
!(workspace "lab" ("group:lab" "person:alex") () "")
!(workspace "tasks" ("task:*") ("person:alex") "")
!(workspace "combined" ("@lab" "@tasks") ("person:alex") "")
!(println! (UNION (workspace-query "lab" "alpha" 0 "similarity")))
!(println! (EXCLUDE (workspace-query "combined" "alpha" 0 "hybrid")))
!(println! (FIRST (workspace-preview "tasks")))
!(workspace-tag "a" ("person:alex") "remove")
!(println! (LATER (workspace-preview "tasks")))
!(println! (CURRENT (workspace)))
''')
        union = output.split('(UNION ', 1)[1].split('\n', 1)[0]
        self.assertEqual(union.count('(MemoryHit "a"'), 1, output)
        self.assertEqual(union.count('(MemoryHit "b"'), 1, output)
        excluded = output.split('(EXCLUDE ', 1)[1].split('\n', 1)[0]
        self.assertIn('(MemoryHit "b"', excluded)
        self.assertNotIn('(MemoryHit "a"', excluded)
        self.assertIn('(FIRST (WorkspacePreview "tasks" (Matching 1)', output)
        self.assertIn('(LATER (WorkspacePreview "tasks" (Matching 2)', output)
        self.assertIn('(CURRENT (WorkspaceCurrent "all"', output)

    def test_idempotent_changes_undo_and_selection_survive_restart(self):
        self.run_metta('''
!(workspace "research" ("task:*") () "task:notes")
!(workspace "research")
!(workspace-include "research" "group:lab")
''')
        snapshot = self.home / 'workspaces.metta'
        before = snapshot.read_bytes()
        output = self.run_metta('''
!(println! (ACTIVE (workspace)))
!(workspace-include "research" "group:lab")
!(println! (UNCHANGED (workspaces)))
''')
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertIn('(ACTIVE (WorkspaceCurrent "research"', output)
        output = self.run_metta('!(workspace-undo)\n!(println! (UNDONE (workspace)))')
        undone = output.split('(UNDONE ', 1)[1].split('\n', 1)[0]
        self.assertIn('(Include ("task:*"))', undone)
        self.assertNotIn('group:lab', undone)
        self.run_metta('!(workspace-undo)')
        restored = self.run_metta('!(println! (RESTORED (workspace)))')
        self.assertIn('(RESTORED (WorkspaceCurrent "all"', restored)

    def test_preferences_are_per_workspace_and_temporary_queries_restore_them(self):
        output = self.run_metta('''
!(workspace-tag "b" ("person:alex"))
!(workspace "alex" ("person:alex") () "person:alex")
!(workspace "alex")
!(println! (SET (setting "workspace" "selector" "similarity")))
!(println! (MODE (ws:mode)))
!(println! (SCOPED (semantic:query "alpha" 1)))
!(println! (TEMP (workspace-query "all" "alpha" 1 "similarity")))
!(println! (STILL (workspace)))
!(workspace "all")
!(println! (DEFAULT (setting "workspace" "selector")))
''')
        self.assertIn('(MODE "similarity")', output)
        self.assertIn('(SCOPED (MemoryResults ((MemoryHit "b"', output)
        self.assertIn('(TEMP (MemoryResults ((MemoryHit "a"', output)
        self.assertIn('(STILL (WorkspaceCurrent "alex"', output)
        self.assertIn('(Current "default")', output)

    def test_bad_selectors_and_cycles_are_local_errors_without_changing_state(self):
        output = self.run_metta('''
!(workspace "a" ("task:*") () "")
!(workspace "b" ("@a") () "")
!(println! (CYCLE (workspace-include "a" "@b")))
!(println! (BAD (workspace "bad" ("nonsense") () "")))
!(println! (MISSING (workspace-query "missing" "alpha")))
!(println! (CURRENT (workspace)))
!(println! (NEXT (semantic:query "alpha" 1 "similarity")))
''')
        self.assertIn('(CYCLE (WorkspaceError cycle', output)
        self.assertIn('(BAD (WorkspaceError selector', output)
        self.assertIn('(MISSING (WorkspaceError missing-workspace', output)
        self.assertIn('(CURRENT (WorkspaceCurrent "all"', output)
        self.assertIn('(NEXT (MemoryResults ((MemoryHit "a"', output)

    def test_queued_remember_keeps_capture_and_home_across_outage_and_restart(self):
        self.failed = True
        output = self.run_metta('''
!(workspace "proof" ("task:proof") () "task:proof")
!(workspace "proof")
!(ws:begin "original" "operator-event" "123")
!(println! (QUEUED (semantic:remember "new alpha evidence")))
!(fixture:drain 2)
!(workspace "all")
!(ws:begin "later" "continuation-tick" "")
''')
        self.assertIn('(QUEUED (MemoryQueued', output)
        self.failed = False
        output = self.run_metta('''
!(println! (RECOVERED (fixture:drain 100)))
!(println! (HOME (workspace-query "proof" "alpha" 0 "similarity")))
!(println! (SCOPES (workspace-preview "proof")))
''')
        self.assertIn('(RECOVERED true)', output)
        self.assertIn('"new alpha evidence"', output)
        self.assertIn('("context:task" "original")', output)
        self.assertIn('("context:task" "proof")', output)
        self.assertIn('("context:channel" "123")', output)
        self.assertNotIn('("context:task" "later")', output)

    def test_attention_scope_applies_to_focus_links_and_similarity_fallback(self):
        output = self.run_metta('''
!(workspace-tag "b" ("task:proof"))
!(workspace "proof" ("task:proof") () "")
!(attention-policy "exposure" 0)
!(attention-use "a" 100)
!(attention-link "b" "a" 1)
!(workspace "proof")
!(println! (ATTENTION (semantic:query "alpha" 1 "attention")))
!(println! (EDGES (am:edges (am:bank) "b")))
!(setting "workspace" "cross-links" "linked")
!(ws:prepare)
!(println! (CROSS (am:edges (am:bank) "b")))
!(println! (LINKED (semantic:query "alpha" 1 "attention")))
!(change-state! &am-state (AttentionUnavailable fixture failure))
!(println! (FALLBACK (semantic:query "alpha" 1 "hybrid")))
''')
        for label in ('ATTENTION', 'LINKED', 'FALLBACK'):
            self.assertIn('(' + label + ' (MemoryResults ((MemoryHit "b"', output, output)
        self.assertIn('(EDGES ())', output)
        self.assertIn('(CROSS (("a" 1)))', output)

    def test_empty_view_and_preview_do_not_stimulate_or_rewrite_attention(self):
        self.run_metta('!(attention-use "a")')
        before = (self.home / 'attention.metta').read_bytes()
        output = self.run_metta('''
!(workspace "empty" () () "")
!(println! (PREVIEW (workspace-preview "empty")))
!(println! (HELP (api:help "workspace-tag")))
''')
        self.assertEqual((self.home / 'attention.metta').read_bytes(), before)
        self.assertIn('(Matching 0)', output)
        output = self.run_metta('!(println! (EMPTY (workspace-query "empty" "alpha" 0 "hybrid")))')
        self.assertIn('(EMPTY (MemoryResults ()', output)

    def test_attention_pool_is_selected_before_its_top_k(self):
        self.collection.add(['c'], [[-1., 0., 0.]], ['strong within task'], [{'time': 'fixture'}])
        output = self.run_metta('''
!(workspace-tag "b" ("task:proof"))
!(workspace-tag "c" ("task:proof"))
!(workspace "proof" ("task:proof") () "")
!(attention-policy "exposure" 0)
!(attention-policy "candidate-multiplier" 1)
!(attention-policy "spread-rounds" 0)
!(attention-policy "seed-similarity-weight" 0)
!(attention-use "a" 10000)
!(attention-use "c" 100)
!(workspace "proof")
!(println! (FOCUS (semantic:query "alpha" 1 "attention")))
''')
        self.assertIn('(FOCUS (MemoryResults ((MemoryHit "c"', output)

    def test_indexed_masks_match_exhaustive_predicates(self):
        self.collection.add([f'r{i}' for i in range(25)],
                            [[1., (i+1)/27., .1] for i in range(25)],
                            [f'fixture {i}' for i in range(25)],
                            [{'context:group': str(i % 3), 'context:task': str(i % 4)}
                             for i in range(25)])
        output = self.run_metta('''
!(case (sm:open) (((MStore $space $profile $dim)
 (let* (($_ (sm:refresh $space $profile $dim))
        ($filter (And ((Or ((Eq "context:group" "1") (Eq "context:task" "0")))
                       (Not (Eq "context:task" "2")) (Exists "context:group"))))
        ($vector (tensor:from f64 (3) (1 0 0)))
        ($indexed (view:query $profile $vector 30 $filter))
        ($reference (list:sort-by memory:precedes
          (collapse (let $row (memory:matches All)
            (let (Memory $id $rev $p $v $doc $meta) $row
              (if (view:matches $meta $filter) (memory:score $profile $vector $row) (empty))))))))
   (println! (EXACT (== $indexed $reference)))))))
''')
        self.assertIn('(EXACT true)', output)

    def test_public_catalogue_dispatch_and_context_capture(self):
        import subprocess
        from test_command_dispatch_engine import CommandDispatchEngineTest
        env = CommandDispatchEngineTest()._cetta_env()
        env.update(self.env)
        env['METTACLAW_ENGINE'] = env['METTACLAW_ACTIVE_ENGINE'] = 'cetta'
        probe = self.home / 'public-workspaces.metta'
        probe.write_text('''
!(println! (API (help "workspace")))
!(println! (CHECK (check "(workspace-include \\\"all\\\" \\\"task:*\\\")")))
!(println! (SETTINGS (settings "workspace")))
!(workspace "task" ("task:proof") () "task:proof")
!(workspace "task")
!(println! (QUEUED (remember "native public workspace alpha")))
!(fixture:drain 100)
!(println! (QUERY (query "alpha" 1 "similarity")))
!(println! (CONTEXT (ws:context)))
'''.replace('!(fixture:drain 100)', '!(semantic:maintain)'))
        result = subprocess.run([self.engine, '--lang', 'petta', '--import-mode',
                                 'ancestor-walk', str(fixture.ROOT / 'cetta_bootstrap.metta'), str(probe)],
                                cwd=fixture.ROOT, env=env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('CommandHelp "workspace" "workspace"', result.stdout)
        self.assertIn('Setting "workspace" "cross-links"', result.stdout)
        self.assertIn('(MemoryQueued ', result.stdout)
        self.assertIn('(CONTEXT (WorkspaceCurrent "task"', result.stdout)

    def test_corrupt_workspace_data_is_preserved_and_repair_can_be_reloaded(self):
        path = self.home / 'workspaces.metta'
        invalid = '(WorkspaceSnapshot 1 "all" ((Workspace "all" ("all") () "" "bogus" "inside")) ())'
        path.write_text(invalid)
        output = self.run_metta('!(println! (BAD (workspace)))\n!(println! (QUERY (semantic:query "alpha" 1)))')
        self.assertIn('(WorkspaceError snapshot', output)
        self.assertNotIn('(MemoryHit ', output)
        self.assertEqual(path.read_text(), invalid)
        path.write_text('(WorkspaceSnapshot 1 "all" ((Workspace "all" ("all") () "" "default" "inside")) ())')
        output = self.run_metta('!(println! (REPAIRED (workspace-reload)))\n!(println! (QUERY (semantic:query "alpha" 1)))')
        self.assertIn('(REPAIRED (WorkspaceCurrent "all"', output)
        self.assertIn('(MemoryHit "a"', output)

    def test_invalid_definition_arguments_do_not_stop_later_calls(self):
        output = self.run_metta('''
!(println! (BAD (workspace "bad" "task:proof" () "")))
!(println! (BAD_COUNT (workspace "bad" ("task:proof"))))
!(println! (NEXT (workspace)))
''')
        self.assertIn('(BAD (WorkspaceError definition', output)
        self.assertIn('(BAD_COUNT (WorkspaceError arguments', output)
        self.assertIn('(NEXT (WorkspaceCurrent "all"', output)


if __name__ == '__main__':
    unittest.main()

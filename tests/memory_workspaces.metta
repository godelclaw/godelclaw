; Workspaces over the native memory world (loaded by tests/cognitive_memory.metta
; after tests/memory_world.metta): views compose and filter before top-k,
; overlap and exclusions resolve once, changes are idempotent and undoable
; across evaluators, preferences belong to a workspace, bad definitions stay
; local, queued remembers keep their capture through an outage, attention
; honours the scope, empty views stimulate nothing, attention pools are scoped
; before top-k, indexed masks equal exhaustive predicates, the public catalogue
; works through the bootstrap, corrupt data is kept and repairs reload, and
; invalid arguments do not stop later calls.

(= (mws:recall $world $root $env)
   (mc:guest $world $root "
!(println! (READS (memory-read \"a\") (memory-read \"b\")))
!(nt:require originals-are-preserved
   (and (case (memory-read \"a\") (((MemoryCurrent \"a\" $r \"old alpha\" $m $p) True) ($bad False)))
        (case (memory-read \"b\") (((MemoryCurrent \"b\" $rb \"beta reference\" $mb $pb) True) ($badb False)))))
!(println! ORIGINALS_KEPT)" $env))

(= (mws:composition $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-compose"))
          ($out (mc:guest $world $root "
!(println! (TAG (workspace-tag \"b\" (\"task:proof\" \"group:lab\"))))
!(println! (DEFINE (workspace \"proof\" (\"task:proof\") () \"task:proof\")))
!(println! (SELECT (workspace \"proof\")))
!(println! (MATCH (semantic:query \"alpha\" 1 \"similarity\")))
!(println! (PREVIEW (workspace-preview)))
!(println! (READ (memory-read \"a\")))
!(println! (CATALOGUE (api:help \"workspace\")))" $env))
          ($_ (nt:require a-view-filters-before-top-k
                (mc:has-all $out ("(WorkspaceTagged \"b\" \"add\"" "(MATCH (MemoryResults ((MemoryHit \"b\""
                                  "(WorkspacePreview \"proof\" (Matching 1)" "(MemoryCurrent \"a\""
                                  "CommandHelp \"workspace\" \"workspace\""))))
          ($_ (nt:require tagging-keeps-the-original-documents (nt:contains (mws:recall $world $root $env) "ORIGINALS_KEPT"))))
     (mc:stop $server)))

(= (mws:overlap $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-overlap"))
          ($out (mc:guest $world $root "
!(workspace-tag \"a\" (\"task:input:123\" \"group:lab\" \"person:alex\"))
!(workspace-tag \"b\" (\"task:other\" \"group:lab\"))
!(workspace \"lab\" (\"group:lab\" \"person:alex\") () \"\")
!(workspace \"tasks\" (\"task:*\") (\"person:alex\") \"\")
!(workspace \"combined\" (\"@lab\" \"@tasks\") (\"person:alex\") \"\")
!(println! (UNION (workspace-query \"lab\" \"alpha\" 0 \"similarity\")))
!(println! (EXCLUDE (workspace-query \"combined\" \"alpha\" 0 \"hybrid\")))
!(println! (FIRST (workspace-preview \"tasks\")))
!(workspace-tag \"a\" (\"person:alex\") \"remove\")
!(println! (LATER (workspace-preview \"tasks\")))
!(println! (CURRENT (workspace)))" $env))
          ($union (mc:line $out "(UNION "))
          ($excluded (mc:line $out "(EXCLUDE "))
          ($_ (nt:require an-overlapping-union-holds-each-record-once
                (and (== (mc:count $union "(MemoryHit \"a\"") 1) (== (mc:count $union "(MemoryHit \"b\"") 1))))
          ($_ (nt:require nested-views-apply-their-exclusions
                (and (nt:contains $excluded "(MemoryHit \"b\"") (not (nt:contains $excluded "(MemoryHit \"a\"")))))
          ($_ (nt:require wildcards-follow-live-tags
                (mc:has-all $out ("(FIRST (WorkspacePreview \"tasks\" (Matching 1)" "(LATER (WorkspacePreview \"tasks\" (Matching 2)"
                                  "(CURRENT (WorkspaceCurrent \"all\"")))))
     (mc:stop $server)))

(= (mws:undo $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-undo"))
          ($snapshot (path:join (mc:case-dir $world "ws-undo") "workspaces.metta"))
          ($_ (mc:guest $world $root "
!(workspace \"research\" (\"task:*\") () \"task:notes\")
!(workspace \"research\")
!(workspace-include \"research\" \"group:lab\")" $env))
          ($_ (nt:require workspace-changes-are-saved (fs:exists $snapshot)))
          ($before (mapi:digest $snapshot))
          ($again (mc:guest $world $root "
!(println! (ACTIVE (workspace)))
!(workspace-include \"research\" \"group:lab\")
!(println! (UNCHANGED (workspaces)))" $env))
          ($_ (nt:require a-repeated-change-writes-nothing
                (and (== (mapi:digest $snapshot) $before) (nt:contains $again "(ACTIVE (WorkspaceCurrent \"research\""))))
          ($undone (mc:line (mc:guest $world $root "!(workspace-undo)\n!(println! (UNDONE (workspace)))" $env) "(UNDONE "))
          ($_ (nt:require undo-restores-the-previous-definition
                (and (nt:contains $undone "(Include (\"task:*\"))") (not (nt:contains $undone "group:lab")))))
          ($_ (mc:guest $world $root "!(workspace-undo)" $env))
          ($_ (nt:require the-selection-survives-a-fresh-evaluator
                (nt:contains (mc:guest $world $root "!(println! (RESTORED (workspace)))" $env) "(RESTORED (WorkspaceCurrent \"all\""))))
     (mc:stop $server)))

(= (mws:preferences $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-preferences"))
          ($out (mc:guest $world $root "
!(workspace-tag \"b\" (\"person:alex\"))
!(workspace \"alex\" (\"person:alex\") () \"person:alex\")
!(workspace \"alex\")
!(println! (SET (setting \"workspace\" \"selector\" \"similarity\")))
!(println! (MODE (ws:mode)))
!(println! (SCOPED (semantic:query \"alpha\" 1)))
!(println! (TEMP (workspace-query \"all\" \"alpha\" 1 \"similarity\")))
!(println! (STILL (workspace)))
!(workspace \"all\")
!(println! (DEFAULT (setting \"workspace\" \"selector\")))" $env))
          ($_ (nt:require preferences-belong-to-a-workspace-and-temporary-queries-restore-them
                (mc:has-all $out ("(MODE \"similarity\")" "(SCOPED (MemoryResults ((MemoryHit \"b\""
                                  "(TEMP (MemoryResults ((MemoryHit \"a\"" "(STILL (WorkspaceCurrent \"alex\""
                                  "(Current \"default\")")))))
     (mc:stop $server)))

(= (mws:bad-selectors $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-bad"))
          ($out (mc:guest $world $root "
!(workspace \"a\" (\"task:*\") () \"\")
!(workspace \"b\" (\"@a\") () \"\")
!(println! (CYCLE (workspace-include \"a\" \"@b\")))
!(println! (BAD (workspace \"bad\" (\"nonsense\") () \"\")))
!(println! (MISSING (workspace-query \"missing\" \"alpha\")))
!(println! (CURRENT (workspace)))
!(println! (NEXT (semantic:query \"alpha\" 1 \"similarity\")))" $env))
          ($_ (nt:require bad-selectors-and-cycles-are-local-errors
                (mc:has-all $out ("(CYCLE (WorkspaceError cycle" "(BAD (WorkspaceError selector"
                                  "(MISSING (WorkspaceError missing-workspace" "(CURRENT (WorkspaceCurrent \"all\""
                                  "(NEXT (MemoryResults ((MemoryHit \"a\"")))))
     (mc:stop $server)))

(= (mws:queued $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-queued"))
          ($_ (fs:write (nq:path $world "status") "503"))
          ($first (mc:guest $world $root "
!(workspace \"proof\" (\"task:proof\") () \"task:proof\")
!(workspace \"proof\")
!(ws:begin \"original\" \"operator-event\" \"123\")
!(println! (QUEUED (semantic:remember \"new alpha evidence\")))
!(fixture:drain 2)
!(workspace \"all\")
!(ws:begin \"later\" \"continuation-tick\" \"\")" $env))
          ($_ (fs:write (nq:path $world "status") "200"))
          ($_ (nt:require a-remember-in-an-outage-is-queued (nt:contains $first "(QUEUED (MemoryQueued")))
          ($later (mc:guest $world $root "
!(println! (RECOVERED (fixture:drain 100)))
!(println! (HOME (workspace-query \"proof\" \"alpha\" 0 \"similarity\")))
!(println! (SCOPES (workspace-preview \"proof\")))" $env))
          ($_ (nt:require a-queued-remember-keeps-its-capture-and-home
                (and (mc:has-all $later ("(RECOVERED true)" "\"new alpha evidence\"" "(\"context:task\" \"original\")"
                                         "(\"context:task\" \"proof\")" "(\"context:channel\" \"123\")"))
                     (not (nt:contains $later "(\"context:task\" \"later\")"))))))
     (mc:stop $server)))

(= (mws:attention-scope $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-attention"))
          ($out (mc:guest $world $root "
!(workspace-tag \"b\" (\"task:proof\"))
!(workspace \"proof\" (\"task:proof\") () \"\")
!(attention-policy \"exposure\" 0)
!(attention-use \"a\" 100)
!(attention-link \"b\" \"a\" 1)
!(workspace \"proof\")
!(println! (ATTENTION (semantic:query \"alpha\" 1 \"attention\")))
!(println! (EDGES (am:edges (am:bank) \"b\")))
!(setting \"workspace\" \"cross-links\" \"linked\")
!(ws:prepare)
!(println! (CROSS (am:edges (am:bank) \"b\")))
!(println! (LINKED (semantic:query \"alpha\" 1 \"attention\")))
!(change-state! &am-state (AttentionUnavailable fixture failure))
!(println! (FALLBACK (semantic:query \"alpha\" 1 \"hybrid\")))" $env))
          ($_ (nt:require attention-applies-the-scope-to-focus-links-and-fallback
                (mc:has-all $out ("(ATTENTION (MemoryResults ((MemoryHit \"b\"" "(LINKED (MemoryResults ((MemoryHit \"b\""
                                  "(FALLBACK (MemoryResults ((MemoryHit \"b\"" "(EDGES ())" "(CROSS ((\"a\" 1)))")))))
     (mc:stop $server)))

(= (mws:empty-view $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-empty"))
          ($snapshot (path:join (mc:case-dir $world "ws-empty") "attention.metta"))
          ($_ (mc:guest $world $root "!(attention-use \"a\")" $env))
          ($_ (nt:require attention-use-writes-the-empty-case-snapshot (fs:exists $snapshot)))
          ($before (mapi:digest $snapshot))
          ($out (mc:guest $world $root "
!(workspace \"empty\" () () \"\")
!(println! (PREVIEW (workspace-preview \"empty\")))
!(println! (HELP (api:help \"workspace-tag\")))" $env))
          ($_ (nt:require an-empty-view-and-its-preview-stimulate-nothing
                (and (== (mapi:digest $snapshot) $before) (nt:contains $out "(Matching 0)"))))
          ($_ (nt:require an-empty-view-finds-nothing
                (nt:contains (mc:guest $world $root "!(println! (EMPTY (workspace-query \"empty\" \"alpha\" 0 \"hybrid\")))" $env)
                             "(EMPTY (MemoryResults ()"))))
     (mc:stop $server)))

(= (mws:pool $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-pool"))
          ($_ (nt:require a-record-joins-the-store
                (mc:add $socket "ws-pool" (("c" (tensor:from f64 (3) (-1.0 0.0 0.0)) "strong within task" (("time" "fixture")))))))
          ($out (mc:guest $world $root "
!(workspace-tag \"b\" (\"task:proof\"))
!(workspace-tag \"c\" (\"task:proof\"))
!(workspace \"proof\" (\"task:proof\") () \"\")
!(attention-policy \"exposure\" 0)
!(attention-policy \"candidate-multiplier\" 1)
!(attention-policy \"spread-rounds\" 0)
!(attention-policy \"seed-similarity-weight\" 0)
!(attention-use \"a\" 10000)
!(attention-use \"c\" 100)
!(workspace \"proof\")
!(println! (FOCUS (semantic:query \"alpha\" 1 \"attention\")))" $env))
          ($_ (nt:require the-attention-pool-is-scoped-before-its-top-k
                (nt:contains $out "(FOCUS (MemoryResults ((MemoryHit \"c\""))))
     (mc:stop $server)))

(= (mws:row $i)
   (let* (($x (/ (+ $i 1) 27.0)) ($group (% $i 3)) ($task (% $i 4)))
     ((format-args "r{}" ($i)) (tensor:from f64 (3) (1.0 $x 0.1)) (format-args "fixture {}" ($i))
      (("context:group" (format-args "{}" ($group))) ("context:task" (format-args "{}" ($task)))))))
(= (mws:rows $i) (if (== $i 25) () (cons-atom (mws:row $i) (mws:rows (+ $i 1)))))
(= (mws:masks $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-masks"))
          ($_ (nt:require twenty-five-records-join-the-store (mc:add $socket "ws-masks" (mws:rows 0))))
          ($out (mc:guest $world $root "
!(case (sm:open) (((MStore $space $profile $dim)
 (let* (($_ (sm:refresh $space $profile $dim))
        ($filter (And ((Or ((Eq \"context:group\" \"1\") (Eq \"context:task\" \"0\")))
                       (Not (Eq \"context:task\" \"2\")) (Exists \"context:group\"))))
        ($vector (tensor:from f64 (3) (1 0 0)))
        ($indexed (view:query $profile $vector 30 $filter))
        ($reference (list:sort-by memory:precedes
          (collapse (let $row (memory:matches All)
            (let (Memory $id $rev $p $v $doc $meta) $row
              (if (view:matches $meta $filter) (memory:score $profile $vector $row) (empty))))))))
   (println! (EXACT (== $indexed $reference) (size-atom $indexed)))))))" $env))
          ($_ (nt:require indexed-masks-match-exhaustive-predicates
                (and (nt:contains $out "(EXACT true") (not (nt:contains $out "(EXACT true 0)"))))))
     (mc:stop $server)))

(= (mws:bootstrap $world $service $root $sdk)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-bootstrap"))
          ($probe (nq:path $world "public-workspaces.metta"))
          ($_ (fs:write $probe "
!(println! (API (help \"workspace\")))
!(println! (CHECK (check \"(workspace-include \\\"all\\\" \\\"task:*\\\")\")))
!(println! (SETTINGS (settings \"workspace\")))
!(workspace \"task\" (\"task:proof\") () \"task:proof\")
!(workspace \"task\")
!(println! (QUEUED (remember \"native public workspace alpha\")))
!(semantic:maintain)
!(println! (QUERY (query \"alpha\" 1 \"similarity\")))
!(println! (CONTEXT (ws:context)))
"))
          ($out (mc:bootstrap $world $root $sdk $probe $env))
          ($_ (nt:require the-public-workspace-catalogue-works-through-the-bootstrap
                (mc:has-all $out ("CommandHelp \"workspace\" \"workspace\"" "Setting \"workspace\" \"cross-links\""
                                  "(MemoryQueued " "(CONTEXT (WorkspaceCurrent \"task\"")))))
     (mc:stop $server)))

(= (mws:corrupt $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-corrupt"))
          ($path (path:join (mc:case-dir $world "ws-corrupt") "workspaces.metta"))
          ($invalid "(WorkspaceSnapshot 1 \"all\" ((Workspace \"all\" (\"all\") () \"\" \"bogus\" \"inside\")) ())")
          ($_ (fs:write $path $invalid))
          ($out (mc:guest $world $root "!(println! (BAD (workspace)))\n!(println! (QUERY (semantic:query \"alpha\" 1)))" $env))
          ($_ (nt:require corrupt-workspace-data-is-refused-and-kept
                (and (nt:contains $out "(WorkspaceError snapshot")
                     (and (not (nt:contains $out "(MemoryHit ")) (== (fs:read-text $path) $invalid)))))
          ($_ (fs:write $path "(WorkspaceSnapshot 1 \"all\" ((Workspace \"all\" (\"all\") () \"\" \"default\" \"inside\")) ())"))
          ($repaired (mc:guest $world $root "!(println! (REPAIRED (workspace-reload)))\n!(println! (QUERY (semantic:query \"alpha\" 1)))" $env))
          ($_ (nt:require a-repair-can-be-reloaded
                (mc:has-all $repaired ("(REPAIRED (WorkspaceCurrent \"all\"" "(MemoryHit \"a\"")))))
     (mc:stop $server)))

(= (mws:invalid-arguments $world $service $root)
   (let* (((OpenCase $server $socket $config $env) (mc:open-case $world $service "ws-arguments"))
          ($out (mc:guest $world $root "
!(println! (BAD (workspace \"bad\" \"task:proof\" () \"\")))
!(println! (BAD_COUNT (workspace \"bad\" (\"task:proof\"))))
!(println! (NEXT (workspace)))" $env))
          ($_ (nt:require invalid-definition-arguments-do-not-stop-later-calls
                (mc:has-all $out ("(BAD (WorkspaceError definition" "(BAD_COUNT (WorkspaceError arguments"
                                  "(NEXT (WorkspaceCurrent \"all\"")))))
     (mc:stop $server)))

(= (mws:run $world $service $root $sdk)
   (let* (($_ (mws:composition $world $service $root))
          ($_ (mws:overlap $world $service $root))
          ($_ (mws:undo $world $service $root))
          ($_ (mws:preferences $world $service $root))
          ($_ (mws:bad-selectors $world $service $root))
          ($_ (mws:queued $world $service $root))
          ($_ (mws:attention-scope $world $service $root))
          ($_ (mws:empty-view $world $service $root))
          ($_ (mws:pool $world $service $root))
          ($_ (mws:masks $world $service $root))
          ($_ (mws:bootstrap $world $service $root $sdk))
          ($_ (mws:corrupt $world $service $root))
          ($_ (mws:invalid-arguments $world $service $root)))
     True))

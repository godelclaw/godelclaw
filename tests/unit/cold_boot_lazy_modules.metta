; Pure-native unit test for #6: fast targeted command_arity scan + lazy optional
; skill/channel/context modules. Verifies exact arity equivalence and first-call
; reduction for every lazy-loaded module.
!(import! &self ../native_test_support.metta)
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ../../src/skills_help.metta)
!(import! &self ../../src/undelivered_prose.metta)
!(import! &self ../../src/coding_work_lazy.metta)
!(import! &self ../../src/channels.metta)
!(import! &self ../../src/skills.metta)
!(import! &self ../../src/context_sources_cetta.metta)

!(let* (
        ; 1. At import time, heavy optional modules and full 5,500+ equation map stay unloaded.
        ($e0 (nt:require full-equations-unloaded-at-boot (== (get-state &arity-equations) ArityUnloadedV1)))
        ($e1 (nt:require help-pages-unloaded-at-boot (== (get-state &help-pages) HelpPagesUnloadedV1)))
        ($e2 (nt:require prose-skills-unloaded-at-boot (== (get-state &prose-skills) ProseSkillsUnloadedV1)))
        ($e3 (nt:require mcp-unloaded-at-boot (== (get-state &skills-mcp-loaded) False)))
        ($e4 (nt:require iter-author-unloaded-at-boot (== (get-state &skills-iter-author-loaded) False)))
        ($e5 (nt:require claude-unloaded-at-boot (== (get-state &skills-claude-loaded) False)))
        ($e6 (nt:require sibling-unloaded-at-boot (== (get-state &skills-sibling-loaded) False)))
        ($e8 (nt:require websearch-unloaded-at-boot (== (get-state &channels-websearch-loaded) False)))
        ($e9 (nt:require chat-spool-unloaded-at-boot (== (get-state &channels-chat-spool-loaded) False)))
        ($e10 (nt:require work-core-unloaded-at-boot (== (get-state &work-core-loaded) False)))
        ($e11 (nt:require coding-estate-unloaded-at-boot (== (get-state &ctx-coding-estate-loaded) False)))

        ; 2. Compact command-only arity table matches full equation scan identically.
        ($fast-rows (arity:rows))
        ($full-eqs (arity:equation-arities))
        ($full-rows (arity:refined-rows $full-eqs))
        ($a0 (nt:require fast-and-full-arity-rows-match (nt:equal $fast-rows $full-rows)))
        ($a1 (nt:require arity-shell-bounds (nt:equal (arity:bounds "shell") (ArityBoundsV1 1 2))))
        ($a2 (nt:require arity-request-page-refined (nt:equal (arity:bounds "request-page") (ArityBoundsV1 1 2))))
        ($a3 (nt:require arity-recent-messages-refined (nt:equal (arity:bounds "recent-messages") (ArityBoundsV1 2 3))))
        ($a4 (nt:require arity-equation-commands-present
               (and (arity:known? "memory-health") (arity:known? "context-policy"))))
        ($a5 (nt:require arity-retired-excluded
               (and (not (arity:known? "rest-command")) (not (arity:known? "task-resolve")))))

        ; 3. Complex LHS (nested parens, quoted parens, multiline) scanned cleanly.
        ($probe-space (new-space))
        ($s-complex (arity:scan-text $probe-space
          "(= (nested-fn (Wrap $a) \"x)y\"\n $b)\n  1)\n(= (simple-fn $x) $x)\n"))
        ($probe-eqs (arity:group-equations (sort-atom (unique-atom (collapse (match $probe-space
          (ArityEquationCountV1 $nm $ct) ($nm $ct)))))))
        ($a6 (nt:require complex-lhs-exact-arity
               (nt:equal $probe-eqs ((ArityEquationV1 "nested-fn" (3)) (ArityEquationV1 "simple-fn" (1))))))

        ; 4. Lazy help pages and undelivered prose bare-command detection work on first call.
        ($h-idx (help:index))
        ($h-shell (help:lookup "shell"))
        ($h0 (nt:require help-pages-loaded-on-demand (not (== (get-state &help-pages) HelpPagesUnloadedV1))))
        ($h1 (nt:require help-shell-page-found (and (== (get-type $h-shell) String) (nt:contains $h-shell "usage: (shell"))))
        ($h2 (nt:require help-index-lists-shell (and (== (get-type $h-idx) String) (nt:contains $h-idx "shell"))))
        ($p0 (nt:require prose-bare-command-recognized (nt:equal (prose:undelivered "rest") "")))
        ($p1 (nt:require prose-skills-loaded-after-bare-check
               (not (== (get-state &prose-skills) ProseSkillsUnloadedV1))))
        ($p2 (nt:require prose-plain-text-kept (nt:equal (str:symbol->string (prose:undelivered "hello there")) "hello there")))

        ; 5. Work SettingSpec visible BEFORE loading coding_work_core; work:reap stays lazy when empty.
        ($ws-spec (settings:spec "work" "context"))
        ($w0 (nt:require work-setting-spec-visible-at-boot
               (case $ws-spec (((SettingSpec 1 "work" "context" "open" $_ $_ $_ $_ $_ $_) True) ($bad False)))))
        ($w1 (nt:require work-core-still-unloaded-after-settings (== (get-state &work-core-loaded) False)))
        ($reap-empty (work:reap))
        ($w2 (nt:require work-reap-zero-and-still-lazy
               (and (nt:equal $reap-empty 0) (== (get-state &work-core-loaded) False))))
        ($cs-missing (ctx:load-coding-sessions))
        ($cs-miss-ok (nt:require coding-sessions-missing-when-absent
                       (and (nt:equal $cs-missing ("missing"))
                            (== (get-state &ctx-coding-estate-loaded) False))))

        ; 6. First-call dispatch for lazy skill modules reduces immediately (not unreduced expressions).
        ($ms-res (mcp-servers))
        ($m0 (nt:require mcp-loaded-on-first-call (== (get-state &skills-mcp-loaded) True)))
        ($m0b (nt:require mcp-servers-first-call-reduced (and (== (get-type $ms-res) String) (nt:contains $ms-res "servers"))))
        ($m-res (mcp-tools "nonexistent-mcp-server-xyz"))
        ($m1 (nt:require mcp-first-call-reduced (and (== (get-type $m-res) String) (nt:contains $m-res "unknown MCP server"))))

        ($c-res (claude-code "ping"))
        ($c0 (nt:require claude-loaded-on-first-call (== (get-state &skills-claude-loaded) True)))
        ($c1 (nt:require claude-first-call-reduced (== (get-type $c-res) String)))

        ($sb-res (sibling-status))
        ($sb0 (nt:require sibling-loaded-on-first-call (== (get-state &skills-sibling-loaded) True)))
        ($sb1 (nt:require sibling-first-call-reduced (== (get-type $sb-res) String)))

        ($it-res (iter-transformations))
        ($it0 (nt:require iter-author-loaded-on-first-call (== (get-state &skills-iter-author-loaded) True)))
        ($it1 (nt:require iter-author-first-call-reduced (== (get-type $it-res) String)))

        ($wk-res (work-status "nonexistent-work-id-xyz"))
        ($wk0 (nt:require work-core-loaded-on-first-call (== (get-state &work-core-loaded) True)))
        ($wk1 (nt:require work-first-call-reduced
                (and (== (get-type $wk-res) String) (nt:contains $wk-res "no session nonexistent-work-id-xyz"))))

        ($ws-loaded (channel:ensure-websearch!))
        ($ws0 (nt:require websearch-loaded-on-ensure (== (get-state &channels-websearch-loaded) True)))
        ($cs-loaded (channel:ensure-chat-spool!))
        ($cs0 (nt:require chat-spool-loaded-on-ensure (== (get-state &channels-chat-spool-loaded) True)))
       )
  (println! UNIT_COLD_BOOT_LAZY_OK))

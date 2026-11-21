; Boot work deferred until needed: the command arity scan reads only command
; heads, and the full equation map, help pages and prose skill set are built
; on first use. The arity table equals the full scan's.
!(import! &self ../native_test_support.metta)
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ../../src/skills_help.metta)
!(import! &self ../../src/undelivered_prose.metta)

!(let* (
        ; 1. At import time the full equation map, help pages and prose skills stay unbuilt.
        ($e0 (nt:require full-equations-unloaded-at-boot (== (get-state &arity-equations) ArityUnloadedV1)))
        ($e1 (nt:require help-pages-unloaded-at-boot (== (get-state &help-pages) HelpPagesUnloadedV1)))
        ($e2 (nt:require prose-skills-unloaded-at-boot (== (get-state &prose-skills) ProseSkillsUnloadedV1)))

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

        ; 4. Help pages and undelivered prose bare-command detection work on first call.
        ($h-idx (help:index))
        ($h-shell (help:lookup "shell"))
        ($h0 (nt:require help-pages-loaded-on-demand (not (== (get-state &help-pages) HelpPagesUnloadedV1))))
        ($h1 (nt:require help-shell-page-found (and (== (get-type $h-shell) String) (nt:contains $h-shell "usage: (shell"))))
        ($h2 (nt:require help-index-lists-shell (and (== (get-type $h-idx) String) (nt:contains $h-idx "shell"))))
        ($p0 (nt:require prose-bare-command-recognized (nt:equal (prose:undelivered "rest") "")))
        ($p1 (nt:require prose-skills-loaded-after-bare-check
               (not (== (get-state &prose-skills) ProseSkillsUnloadedV1))))
        ($p2 (nt:require prose-plain-text-kept (nt:equal (str:symbol->string (prose:undelivered "hello there")) "hello there")))
       )
  (println! UNIT_COLD_BOOT_LAZY_OK))

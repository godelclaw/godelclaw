; The bare-command vocabulary has one owner, the arity table: a command's
; arity follows its equations, a command named only by its equations joins,
; and retired names do not; the balancer recognizes a bare command by the
; table, and the help checks a command's arguments against the same table.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/command_arity.metta)
!(import! &self ../../src/balance_parentheses.metta)
!(import! &self ../../src/skills_help.metta)

!(nt:require equations-refine-the-named-commands
   (and (== (arity:bounds "request-page") (ArityBoundsV1 1 2))
   (and (== (arity:bounds "recent-messages") (ArityBoundsV1 2 3))
        (== (arity:bounds "shell") (ArityBoundsV1 1 2)))))
!(nt:require commands-named-only-by-equations-join
   (== (filter-atom (arity:equation-commands) $name (not (arity:known? $name))) ()))
!(nt:require retired-names-are-not-commands
   (== (filter-atom ("rest-command" "background-model" "model-adopt" "task-resolve") $name (arity:known? $name)) ()))
!(nt:require the-names-are-listed-in-order
   (== (arity:names-text) (str:join "\n" (sort-strings (arity:names)))))
!(nt:require a-bare-command-is-recognized-by-the-table
   (and (== (repr (balance:parentheses "pins")) "((pins))")
   (and (== (repr (balance:parentheses "memory-health now")) "((memory-health now))")
        (== (repr (balance:parentheses "hello there")) "()"))))
!(nt:require the-help-checks-arguments-against-the-same-table
   (str:starts-with (help:command-error (pins extra)) "COMMAND_ERROR: wrong number of arguments"))

!(println! UNIT_COMMAND_ARITY_OK)

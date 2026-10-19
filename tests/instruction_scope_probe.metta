!(import! &self ./src/open_assemblage)
!(import! &self ./src/instruction_scope)

(= (instruction-scope-assert-equal $A $B) (assert (== $A $B)))

!(let* (($root-rules
          (coordinate may-edit allowed
            (coordinate reply-format concise coordinates-empty)))
         ($middle-rules
          (coordinate session-refresh-sec 259200 coordinates-empty))
         ($nearest-rules
          (coordinate may-edit forbidden coordinates-empty))
         ($outer
          (instruction-scope-chain
            (instruction-scope-layer root $root-rules)
            instruction-scope-chain-empty))
         ($middle
          (instruction-scope-chain
            (instruction-scope-layer middle $middle-rules) $outer))
         ($chain
          (instruction-scope-chain
            (instruction-scope-layer nearest $nearest-rules) $middle))
         ($_ (instruction-scope-assert-equal
               (instruction-resolve may-edit $chain)
               (resolved-instruction nearest may-edit forbidden)))
         ($_ (instruction-scope-assert-equal
               (instruction-resolve session-refresh-sec $chain)
               (resolved-instruction middle session-refresh-sec 259200)))
         ($_ (instruction-scope-assert-equal
               (instruction-resolve reply-format $chain)
               (resolved-instruction root reply-format concise)))
         ($_ (instruction-scope-assert-equal
               (instruction-resolve absent-key $chain)
               (no-instruction absent-key)))
         ;; Once the nearest layer defines a key, replacing all outer layers
         ;; cannot change that result.
         ($other-outer
          (instruction-scope-chain
            (instruction-scope-layer other
              (coordinate may-edit invented coordinates-empty))
            instruction-scope-chain-empty))
         ($other-chain
          (instruction-scope-chain
            (instruction-scope-layer nearest $nearest-rules) $other-outer))
         ($_ (instruction-scope-assert-equal
               (instruction-resolve may-edit $other-chain)
               (resolved-instruction nearest may-edit forbidden))))
     (println! INSTRUCTION_SCOPE_OK))

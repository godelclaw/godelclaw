;; Pure per-key instruction resolution over a validated ancestor chain.
;;
;; The caller supplies a validated ancestor chain, nearest-first.
;; This policy contains no filesystem access, parsing, caching, prompt rendering, or path heuristics.
;; Rule maps use the open coordinate representation, so keys remain data.

(= (instruction-resolve $key instruction-scope-chain-empty)
   (no-instruction $key))

(= (instruction-resolve
      $key
      (instruction-scope-chain
        (instruction-scope-layer $directory coordinates-empty) $outer))
   (instruction-resolve $key $outer))

(= (instruction-resolve
      $key
      (instruction-scope-chain
        (instruction-scope-layer $directory (coordinate $name $value $rest)) $outer))
   (if (== $key $name)
       (resolved-instruction $directory $key $value)
       (instruction-resolve $key
         (instruction-scope-chain
           (instruction-scope-layer $directory $rest) $outer))))

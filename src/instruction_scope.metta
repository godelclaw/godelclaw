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
        (instruction-scope-layer $directory $rules) $outer))
   (let $found (coordinate-get $key $rules)
     (case $found
       (((missing-coordinate $missing)
         (instruction-resolve $key $outer))
        ($value
         (resolved-instruction $directory $key $value))))))

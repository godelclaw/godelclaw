; Native context projection and assembly. Python owns only the retained
; loaders (including the ONE pins marker owner), never a bundle fallback.
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library json))
!(import! &self ./src/undelivered_prose_unicode)
!(import! &self ./src/context_source_loaders.py)

!(bind! &ctx-known (new-state ()))
!(bind! &ctx-blank (new-state
   (re:compile (str:join "" ("\\A" (prose:pattern space) "*\\z")))))

(= (ctx:revision $raw) (str:concat "sha256:" (str:sha256 $raw)))
(= (ctx:clip $raw $limit $tail)
   (if (<= (str:char-length $raw) $limit) $raw
     (if $tail
       (str:concat "…\n" (str:char-slice $raw (- (str:char-length $raw) (- $limit 2)) (str:char-length $raw)))
       (str:concat (str:char-slice $raw 0 (- $limit 1)) "…"))))
(= (ctx:previous $id $known)
   (if (== $known ()) Missing
     (let (($key $text $revision) $rest) (decons-atom $known)
       (if (== $id $key) (Previous $text $revision) (ctx:previous $id $rest)))))
(= (ctx:without $id $known)
   (if (== $known ()) ()
     (let* ((($item $rest) (decons-atom $known))
            (($key $text $revision) $item)
            ($remaining (ctx:without $id $rest)))
       (if (== $id $key) $remaining (cons-atom $item $remaining)))))
(= (ctx:absent $id)
   (let $_ (change-state! &ctx-known (ctx:without $id (get-state &ctx-known)))
     (CtxObservation $id "absent" "" (ctx:revision ""))))
; This state transition refines ContextProjection.admit: absent -> none,
; unavailable -> previous, known -> some (bounded text, raw revision).
(= (ctx:observe $id $limit $tail $outcome)
   (case $outcome
     ((("unavailable")
       (case (ctx:previous $id (get-state &ctx-known))
         ((Missing (CtxObservation $id "unavailable" "" ""))
          ((Previous $text $revision) (CtxObservation $id "stale-known" $text $revision)))))
      (("missing") (ctx:absent $id))
      (("value" $raw)
       (if (re:is-match (get-state &ctx-blank) $raw) (ctx:absent $id)
         (let* (($text (ctx:clip $raw $limit $tail))
                ($revision (ctx:revision $raw))
                ($_ (change-state! &ctx-known
                      (cons-atom ($id $text $revision) (ctx:without $id (get-state &ctx-known))))))
           (CtxObservation $id "known" $text $revision))))
      ($other (Error ctx:observe invalid-outcome)))))
(= (ctx:project $loaders $specs)
   (if (== $specs ()) ()
     (let* (((($key $limit $tail) $rest) (decons-atom $specs))
            ($id (str:symbol->string $key))
            ($outcome (ctx:decode (py-call (context_source_loaders.load $loaders $id))))
            ($observation (ctx:observe $id $limit $tail $outcome))
            ($others (ctx:project $loaders $rest)))
       (cons-atom $observation $others))))
(= (ctx:decode $wire)
   (case $wire
     (((0) ("unavailable")) ((1) ("missing"))
      ((2 $encoded) ("value" (json:parse (str:symbol->string $encoded))))
      ($other (Error ctx:decode invalid-outcome)))))
(= (ctx:render-one $observation)
   (let (CtxObservation $id $status $text $revision) $observation
     (str:join "" ("SOURCE[" $id "] status=" $status " revision="
       (if (== $revision "") "unavailable" $revision)
       (if (== $text "") "" (str:concat "\n" $text))))))
(= (ctx:group $observations $group)
   (if (== $observations ()) ()
     (let* ((($item $rest) (decons-atom $observations))
            ((CtxObservation $id $status $text $revision) $item)
            ($others (ctx:group $rest $group))
            ($belongs (if (== $group "turn")
                        (not (or (== $id "project-capabilities")
                          (or (== $id "development-state")
                            (or (== $id "project-evidence") (== $id "conversation")))))
                        (== $id $group))))
       (if $belongs (cons-atom (ctx:render-one $item) $others) $others))))
(= (ctx:layout $observations)
   (let $stable (union-atom (ctx:group $observations "project-capabilities")
                 (union-atom (ctx:group $observations "development-state")
                             (ctx:group $observations "project-evidence")))
     (str:join "\n\n_cache_boundary_\n\n"
       ((str:join "\n\n" $stable)
        (str:join "\n\n" (ctx:group $observations "conversation"))
        (str:join "\n\n" (ctx:group $observations "turn"))))))
(= (ctx:build $prepared)
   (let ($loaders $specs) $prepared (ctx:layout (ctx:project $loaders $specs))))
; Capture errors, empty/nondeterministic answers, and unreduced helpers. None
; of these can silently choose context_sources.bundle on the native route.
(= (ctx:safe $answers)
   (case $answers
     ((($text) (if (== (get-type $text) String) $text "SOURCE[context-bundle] status=unavailable"))
      ($other "SOURCE[context-bundle] status=unavailable"))))
(= (context-bundle:with-views $views)
   (ctx:safe (collapse (catch (ctx:build (py-call (context_source_loaders.prepare $views)))))))
(= (context-bundle:with-views $views $conversation)
   (ctx:safe (collapse (catch (ctx:build (py-call (context_source_loaders.prepare $views $conversation)))))))

; Native context projection and assembly. Python owns only the retained
; loaders (including the ONE pins marker owner), never a bundle fallback.
!(import! &self (library str))
!(import! &self (library re))
!(import! &self (library json))
!(import! &self ./src/undelivered_prose_unicode)
!(import! &self ./src/context_source_loaders.py)
!(import! &self ./src/small_views_cetta.metta)

!(bind! &ctx-known (new-state ()))
!(bind! &ctx-blank (new-state
   (re:compile (str:join "" ("\\A" (prose:pattern space) "*\\z")))))
!(bind! &ctx-first-space (new-state (re:compile (prose:pattern space))))

(= (ctx:blank? $raw)
   (if (== $raw "") True
     (if (re:is-match (get-state &ctx-first-space) (str:char-slice $raw 0 1))
       (re:is-match (get-state &ctx-blank) $raw) False)))

(= (ctx:revision $raw) (str:concat "sha256:" (str:sha256 $raw)))
(= (ctx:clip $raw $limit $tail)
   (let $length (str:char-length $raw)
     (if (<= $length $limit) $raw
       (if $tail
         (str:concat "…\n" (str:char-slice $raw (- $length (- $limit 2)) $length))
         (str:concat (str:char-slice $raw 0 (- $limit 1)) "…")))))
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
       (if (ctx:blank? $raw) (ctx:absent $id)
         (let* (($text (ctx:clip $raw $limit $tail))
                ($revision (ctx:revision $raw))
                ($_ (change-state! &ctx-known
                      (cons-atom ($id $text $revision) (ctx:without $id (get-state &ctx-known))))))
           (CtxObservation $id "known" $text $revision))))
      ($other (Error ctx:observe invalid-outcome)))))
; Assembly: prepare + seven retained loaders = eight Python crossings, down
; from fourteen. Capability engine lookup adds one allowed crossing (nine
; total here); this is NOT a full-turn count. Pins marker ownership is intact.
(= (ctx:load $loaders $id)
   (case $id
     (("active-query-declaration" (sv:load $id)) ("commitment-state" (sv:load $id))
      ("task-phase" (sv:load $id)) ("development-state" (sv:load $id))
      ("project-capabilities" (sv:load $id)) ("project-evidence" (sv:load $id))
      ($retained (ctx:decode (py-call (context_source_loaders.load $loaders $id)))))))
(= (ctx:project $loaders $specs)
   (if (== $specs ()) ()
     (let* (((($key $limit $tail) $rest) (decons-atom $specs))
            ($id (str:symbol->string $key))
            ; Keep loader/str(value) conversion outside the decode catch: a
            ; conversion exception still fails the bundle, exactly as Python.
            ($outcome (ctx:load $loaders $id))
            ($observation (ctx:observe $id $limit $tail $outcome))
            ($others (ctx:project $loaders $rest)))
       (cons-atom $observation $others))))
(= (ctx:decode $wire)
   (case $wire
     (((0) ("unavailable")) ((1) ("missing"))
      ((2 $encoded)
       (ctx:decoded (collapse (catch (json:parse (str:symbol->string $encoded))))))
      ($other ("unavailable")))))
; Residual transport failures cost only their source. Never invent absence or
; install non-text data: admission retains the prior value as stale-known.
(= (ctx:decoded $answers)
   (case $answers
     ((($text) (if (== (get-type $text) String) ("value" $text) ("unavailable")))
      ($other ("unavailable")))))
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

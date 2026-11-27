; One case of tests/unit/small_views.metta in its own engine, so each case
; starts with empty caches and keeps them across its steps: small_views_case
; CASE DIRECTORY. Each step runs its file operations, sets the environment
; and engine command the view sees, and records the view's answer and the
; fake engine's probe log as answer-N and probes-N.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/small_views_cetta.metta)
!(import! &self (library json))
!(import! &self (library list))

(= (svc:root) (nt:required-env "TEST_REPOSITORY"))
(= (svc:directory) (str:symbol->string (system:arg 1)))
(= (svc:fill $text) (str:join (svc:directory) (str:split "{dir}" $text)))
; The step's own environment; the four optional paths are unset unless it sets them.
!(bind! &svc-env (new-state ()))
!(bind! &svc-command (new-state ()))
(= (svc:optional) ("METTACLAW_ACTIVE_QUERIES_PATH" "METTACLAW_COMMITMENT_STATE_PATH"
                   "METTACLAW_TASK_PHASE_PATH" "METTACLAW_REPORTS_ROOT"))
(= (svc:member? $x $xs) (if (== $xs ()) False (if (== (car-atom $xs) $x) True (svc:member? $x (cdr-atom $xs)))))
(= (svc:lookup $pairs $name)
   (if (== $pairs ()) SvcAbsent
     (case (car-atom $pairs)
       (((JsonPair $key $value) (if (== $key $name) (SvcSet $value) (svc:lookup (cdr-atom $pairs) $name)))))))
(= (svc:getenv $name $default)
   (case (svc:lookup (get-state &svc-env) $name)
     (((SvcSet $value) $value)
      (SvcAbsent (if (svc:member? $name (svc:optional)) $default
                   (__cetta_lib_system_getenv_or_default $name $default))))))
!(match &self (= (system:getenv-or-default $name $default) $body)
   (remove-atom &self (= (system:getenv-or-default $name $default) $body)))
(= (system:getenv-or-default $name $default) (svc:getenv $name $default))
!(match &self (= (engine-native:command $value) $body)
   (remove-atom &self (= (engine-native:command $value) $body)))
(= (engine-native:command $value) (get-state &svc-command))

; Shell helpers the recorded scripts name: write the fake engine, and rewrite
; it in place as its original bytes plus a suffix.
(= (svc:prelude)
   (str:join "" ("fake_source=" (repr (path:join (svc:root) "tests/fixtures/small_views_fake_engine.sh")) "\n"
                 "native-fake() { cat \"$fake_source\" > \"$1\" && cp \"$1\" \"$1.orig\" && chmod 700 \"$1\" && touch -h -d @1700000000.000000000 \"$1\"; }\n"
                 "native-fake-suffix() { cat \"$1.orig\" > \"$1\" && printf '%b' \"$2\" >> \"$1\"; }\n"
                 "set -e\n")))
(= (svc:shell $script)
   (if (== $script "") True
     (case (proc:run ("bash" "-c" (str:concat (svc:prelude) (svc:fill $script))) (svc:directory)
                     (nt:environment (svc:directory)) 30000 65536)
       (((proc:exited 0 $out $err) True)
        ($other (let $_ (println! (SVC_SHELL_FAILED $script $other)) False))))))
(= (svc:write $name $text) (fs:write (path:join (svc:directory) $name) $text))
(= (svc:step $source $index $step)
   (let* (($_ (nt:require step-files-applied (svc:shell (json:get $step "script"))))
          ($_ (change-state! &svc-env (case (json:get $step "env") (((JsonObject $pairs)
                (map-atom $pairs $pair (case $pair (((JsonPair $k $v) (JsonPair $k (svc:fill $v)))))))))))
          ($command (json:get $step "command"))
          ($_ (if (== $command JsonNull) True
                (change-state! &svc-command (map-atom (json:array-items $command) $part (svc:fill $part)))))
          ($sleep (json:get $step "sleep"))
          ($_ (if (> $sleep 0) (proc:run ("sleep" (repr $sleep)) (svc:directory) (nt:environment (svc:directory)) 10000 4096) True))
          ($answer (json:stringify (JsonArray (sv:load $source))))
          ($count (path:join (svc:directory) "count"))
          ($_ (svc:write (str:concat "answer-" (repr $index)) $answer)))
     (svc:write (str:concat "probes-" (repr $index)) (if (fs:exists $count) (fs:read-text $count) ""))))
(= (svc:run $name)
   (let* (($reference (json:parse (fs:read-text (path:join (svc:root) "tests/fixtures/small_views_reference.json"))))
          ($found (filter-atom (json:array-items (json:get $reference "cases")) $c (== (json:get $c "name") $name)))
          ($_ (nt:require case-is-recorded (== (size-atom $found) 1)))
          ($case (car-atom $found))
          ($_ (map-atom (json:array-items (json:get $case "setup")) $line (nt:require case-setup (svc:shell $line))))
          ($steps (json:array-items (json:get $case "steps"))))
     (map-atom (list:range 0 (size-atom $steps)) $i (svc:step (json:get $case "source") $i (list:at $steps $i)))))

;; Checks the Python test made with stubs of its own, natively.
(= (svc:extra "inner-error" $reference)
   ; A failed listing of the transformations is a value naming the failure.
   (let* (($_ (match &self (= (sv:iter) $body) (remove-atom &self (= (sv:iter) $body))))
          ($_ (add-atom &self (= (sv:iter) (Error sv:iter ValueError)))))
     (nt:require a-failed-listing-is-named-not-hidden
       (== (json:parse (json:stringify (JsonArray (sv:load "development-state"))))
           (JsonArray ("value" (json:get $reference "development_inner_error")))))))
(= (svc:extra "legacy-metadata" $reference)
   ; Metadata without a modification time, or none at all, makes the
   ; capability view unavailable without launching the engine.
   (let* (($_ (svc:shell "native-fake {dir}/fake-cetta && : > {dir}/mode"))
          ($_ (change-state! &svc-command ((svc:fill "{dir}/fake-cetta") (svc:fill "{dir}/mode") (svc:fill "{dir}/count"))))
          ($_ (match &self (= (fs:metadata $path $links) $body) (remove-atom &self (= (fs:metadata $path $links) $body))))
          ($_ (add-atom &self (= (fs:metadata $path $links) (fs:failed fs:denied metadata $path 13 (fs:progress 0 known-position) "fixture"))))
          ($denied (sv:load "project-capabilities"))
          ($_ (match &self (= (fs:metadata $path $links) $body) (remove-atom &self (= (fs:metadata $path $links) $body))))
          ($_ (add-atom &self (= (fs:metadata $path $links) (fs:info regular 1 (fs:identity "1" "2")))))
          ($legacy (sv:load "project-capabilities")))
     (nt:require unusable-metadata-is-unavailable-and-launches-nothing
       (and (== $denied ("unavailable"))
       (and (== $legacy ("unavailable")) (not (fs:exists (svc:fill "{dir}/count"))))))))
(= (svc:extra "artifact-key" $reference)
   ; The cache key is the command, the modification time in nanoseconds,
   ; negative ones included, and the size.
   (let* (($_ (svc:shell "native-fake {dir}/fake-cetta"))
          ($command ((svc:fill "{dir}/fake-cetta") (svc:fill "{dir}/mode") (svc:fill "{dir}/count")))
          ($size (fs:size (svc:fill "{dir}/fake-cetta")))
          ($keys (map-atom (("@1700000000.000000000" 1700000000000000000) ("@0.000000001" 1) ("@-0.000000001" -1)) $pair
                   (let ($stamp $nanoseconds) $pair
                     (let $_ (svc:shell (str:join "" ("touch -h -d " $stamp " {dir}/fake-cetta")))
                       (== (sv:artifact-key $command) ($command $nanoseconds $size)))))))
     (nt:require the-artifact-key-is-nanoseconds-and-size (== $keys (True True True)))))
(= (svc:extra "model-projection" $reference)
   ; The implemented rows of an inventory are what the capability model projects.
   (nt:require the-inventory-matches-the-capability-model
     (== (map-atom (filter-atom (sv:inventory ("  petta implemented Native 日 🙂" "  future planned Not yet" "  unicode implemented λ"))
                     $row (== (index-atom $row 1) "implemented"))
                   $row (str:join " " ((index-atom $row 0) (index-atom $row 2))))
         (json:array-items (json:get $reference "capability_model_projection")))))
(= (svc:extra "json-gaps" $reference)
   ; JSON outside the standard (a lone surrogate, NaN) is refused, not
   ; guessed at; Python's reader accepted both.
   (let* (($_ (svc:shell "printf '%s' '[\"\\ud800\"]' > {dir}/queries && printf '%s' '{\"schema\":1,\"revision\":NaN}' > {dir}/task"))
          ($_ (change-state! &svc-env ((JsonPair "METTACLAW_ACTIVE_QUERIES_PATH" (svc:fill "{dir}/queries"))
                                       (JsonPair "METTACLAW_TASK_PHASE_PATH" (svc:fill "{dir}/task"))))))
     (nt:require nonstandard-json-is-refused
       (and (== (sv:load "active-query-declaration") ("unavailable"))
            (== (sv:load "task-phase") ("unavailable"))))))
(= (svc:main $name)
   (if (str:starts-with $name "extra:")
     (svc:extra (str:byte-slice $name 6 (str:byte-length $name))
                (json:parse (fs:read-text (path:join (svc:root) "tests/fixtures/small_views_reference.json"))))
     (svc:run $name)))
!(svc:main (str:symbol->string (system:arg 0)))
!(println! SMALL_VIEWS_CASE_DONE)

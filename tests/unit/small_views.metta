; The six small context views (active queries, commitment, task phase,
; development state, project capabilities, project evidence) answer, step for
; step, as their Python loaders did on the same files
; (tests/fixtures/small_views_reference.json): present, absent, malformed,
; Unicode and changing inputs; the capability probe's cache, keyed by the
; engine's command, modification time and size, and the probes it launches;
; failures and timeouts that are never cached; evidence from a git checkout
; and reports, cached briefly. Each case runs in its own engine
; (tests/unit/small_views_case.metta).
!(import! &self ../native_test_support.metta)
!(import! &self (library json))
!(import! &self (library list))

(= (sv-test:root) (nt:required-env "TEST_REPOSITORY"))
(= (sv-test:reference) (json:parse (fs:read-text (path:join (sv-test:root) "tests/fixtures/small_views_reference.json"))))
(= (sv-test:fill $directory $text) (str:join $directory (str:split "{dir}" $text)))
(= (sv-test:lines $text)
   (filter-atom (str:split "\n" $text) $line (not (== $line ""))))
(= (sv-test:guest $name $directory)
   (proc:run ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet"
              "tests/unit/small_views_case.metta" $name $directory)
             (sv-test:root)
             (list:append (nt:environment $directory)
               (("TEST_REPOSITORY" (sv-test:root))
                ("METTACLAW_ITER_PROCESS_DIR" (path:join $directory "iter"))
                ("METTACLAW_PROJECT_REGISTRY_PATH" (path:join $directory "registry.json"))
                ("GIT_CONFIG_GLOBAL" "/dev/null") ("GIT_CONFIG_NOSYSTEM" "1")))
             180000 1048576))
(= (sv-test:prepare $name)
   (let* (($directory (path:join (nt:required-env "HOME") (str:concat "sv-" $name)))
          ($_ (fs:make-directories (path:join $directory "iter") 448))
          ($_ (fs:write (path:join $directory "registry.json") "{\"schema\":1,\"projects\":[]}")))
     $directory))
; One recorded case: every step's answer and probe log as recorded.
(= (sv-test:case $case)
   (let* (($name (json:get $case "name")) ($directory (sv-test:prepare $name))
          ($run (sv-test:guest $name $directory))
          ($ran (case $run (((proc:exited 0 $out $err) (nt:contains $out "SMALL_VIEWS_CASE_DONE")) ($other False))))
          ($_ (if $ran True (println! (SMALL_VIEWS_GUEST_FAILED $name $run))))
          ($expected (json:array-items (json:get $case "expected")))
          ($probes (json:array-items (json:get $case "probes")))
          ($wrong (filter-atom (list:range 0 (size-atom $expected)) $i
                    (let* (($answer-path (path:join $directory (str:concat "answer-" (repr $i))))
                           ($answer (if (fs:exists $answer-path) (json:parse (fs:read-text $answer-path)) Unanswered))
                           ($want (JsonArray (map-atom (json:array-items (list:at $expected $i)) $part (sv-test:fill $directory $part))))
                           ($probe-path (path:join $directory (str:concat "probes-" (repr $i))))
                           ($seen (if (fs:exists $probe-path) (sv-test:lines (fs:read-text $probe-path)) Unrecorded))
                           ($same (and (== $answer $want) (== $seen (json:array-items (list:at $probes $i))))))
                      (if $same False
                        (let $_ (println! (SMALL_VIEW_DIFFERS $name $i (ANSWER $answer) (EXPECTED $want) (PROBES $seen))) True))))))
     (and $ran (== $wrong ()))))

!(let* (($reference (sv-test:reference))
        ($failed (filter-atom (json:array-items (json:get $reference "cases")) $case (not (sv-test:case $case)))))
   (nt:require every-view-answers-as-recorded (== (map-atom $failed $c (json:get $c "name")) ())))
!(nt:require the-native-only-checks-pass
   (== (filter-atom ("inner-error" "legacy-metadata" "artifact-key" "model-projection" "json-gaps") $extra
         (let* (($name (str:concat "extra:" $extra)) ($directory (sv-test:prepare $extra))
                ($run (sv-test:guest $name $directory)))
           (case $run
             (((proc:exited 0 $out $err) (not (nt:contains $out "SMALL_VIEWS_CASE_DONE")))
              ($other (let $_ (println! (SMALL_VIEWS_EXTRA_FAILED $extra $other)) True))))))
       ()))

!(println! UNIT_SMALL_VIEWS_OK)

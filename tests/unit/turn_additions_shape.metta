; The turn's shape in source: the weak core names no runtime additions, the
; live turn delegates to named outer additions, an envelope without commands
; never reaches the effect broker, and a protocol failure paces without
; spending energy.
!(import! &self ../native_test_support.metta)
!(import! &self (library list))

(= (ta:read $repo $file) (fs:read-text (path:join $repo $file)))
(= (ta:all $values) (== (filter-atom $values $value (not (== $value True))) ()))
(= (ta:has-all $text $parts) (ta:all (map-atom $parts $part (nt:contains $text $part))))
(= (ta:has-none $text $parts) (ta:all (map-atom $parts $part (not (nt:contains $text $part)))))

!(let* (($repo (nt:required-env "TEST_REPOSITORY"))
         ($core (ta:read $repo "src/weak_process_core.metta"))
         ($loop (ta:read $repo "src/loop.metta"))
         ($_ (nt:require weak-core-defines-two-process-steps
               (== (- (size-atom (str:split "(= (process-step" $core)) 1) 2)))
         ($_ (nt:require weak-core-names-no-runtime-additions
               (ta:has-none $core ("addition-" "context_sources" "synthetic_llm" "run-command-batch-once"))))
         ($_ (nt:require live-turn-delegates-to-named-outer-additions
               (ta:has-all $loop ("addition-context-sources" "addition-effect-turn-begin" "addition-prepare-request"
                                  "addition-model-call" "addition-balance-response" "addition-envelope-response"
                                  "addition-read-response" "addition-command-sequence" "addition-effect-broker"))))
         ($_ (nt:require live-turn-calls-no-host-language
               (ta:has-none $loop ("(py-call (context_sources.bundle))" "(py-call (telegram.begin_effect_turn"
                                   "(py-call (synthetic_llm.chat" "(run-command-batch-once\n"))))
         ($_ (nt:require an-envelope-without-commands-never-reaches-the-broker
               (nt:contains (ta:read $repo "src/command_pipeline.metta") "((no_commands_parsed) ())")))
         ($_ (nt:require protocol-failure-paces-without-spending-energy
               (and (ta:has-all (ta:read $repo "src/loop_policy.metta") ("(applyProtocolPacing" "(max 1 $retry)"))
                    (nt:contains $loop "(applyProtocolPacing (sleepInterval) 0")))))
   (println! UNIT_TURN_ADDITIONS_SHAPE_OK))

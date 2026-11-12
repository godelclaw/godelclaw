; Python-entry census over the smoke scenarios. Each scenario runs in its own
; disposable world with tests/python_entry_census.py installed as a profiler;
; every total must stay at or below its ceiling in tests/python_entry_census.json.
; Lower a ceiling in the same commit as the port that lowers the count.
; Run from the checkout root with NATIVE_TEST_SDK, NATIVE_TEST_CORE and
; NATIVE_TEST_ARTIFACTS, as for scripts/test_runtime_probes_cetta.metta.
!(import! &self ../tests/runtime_probes_native.metta)

(: census:absolute-imports (-> %Undefined% %Undefined% %Undefined%))
(= (census:absolute-imports $root $text)
   (str:join (str:concat (str:concat "(import! &self " $root) "/")
             (str:split "(import! &self ./" $text)))

(: census:wrapper (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (census:wrapper $root $probe $result)
   (let* (($profiler (preq:repr (nt:join $root "tests/python_entry_census.py")))
          ($head (format-args "!(import! &self (library lib_import))\n!(import! &self {})\n!(py-call (python_entry_census.start {}))\n"
                              ($profiler (preq:repr $result))))
          ($body (census:absolute-imports $root (fs:read-text (nt:join $root $probe)))))
     (str:concat (str:concat $head $body) "\n!(py-call (python_entry_census.finish))\n")))

(: census:total (-> %Undefined% %Undefined% %Undefined%))
(= (census:total $name $result)
   (let* (($census (preq:parse (fs:read-text $result)))
          ($total (pcall:fact $census "total"))
          ($_ (println! (PYTHON_ENTRY_CENSUS $name $total (preq:render (preq:field $census "modules"))))))
     $total))

(: census:probe (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (census:probe $root $sdk $core $artifacts $fixture $bin $name $probe $marker)
   (let* (($world (nq:world $root $sdk $core $artifacts $fixture (str:concat "census-" $name)))
          ($result (nq:path $world "census.json"))
          ($wrapper (nq:path $world "census-probe.metta"))
          ($_ (fs:write $wrapper (census:wrapper $root $probe $result)))
          ($env (nr:probe-env $world $probe $bin $probe))
          ($output (nt:exit-ok (proc:run ("./run.sh" $wrapper) $root $env 120000 4194304)))
          ($_ (nt:require census-scenario-witness (preq:member? $marker (str:split "\n" $output))))
          ($_ (nq:close $world)))
     (census:total $name $result)))

; One production-shaped command turn: native transport and ledger, model
; commentary and a display stream, against the owned loopback provider.
(: census:provider (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (census:provider $root $sdk $core $artifacts $fixture $name)
   (let* (($world (nq:world $root $sdk $core $artifacts $fixture (str:concat "census-" $name)))
          ($_ (fs:write (nq:path $world "response.body")
               "{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":null,\"tool_calls\":[{\"id\":\"t1\",\"type\":\"function\",\"function\":{\"name\":\"metta\",\"arguments\":\"{\\\"program\\\":\\\"((nop))\\\"}\"}}]},\"finish_reason\":\"tool_calls\"}],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":2}}"))
          ($result (nq:path $world "census.json"))
          ($entry (nq:path $world "census-guest.metta"))
          ($forms "!(let $answer (llm:command \"fixture-model\" 42 \"low\" \"S_cache_boundary_U\") (let $_ (nt:require census-provider-answer (nt:equal $answer \"((nop))\")) (println! CENSUS_PROVIDER_OK)))")
          ($_ (fs:write $entry
               (format-args "!(import! &self {})\n!(py-call (python_entry_census.start {}))\n!(import! &self {})\n!(import! &self {})\n{}\n!(py-call (python_entry_census.finish))\n"
                 ((preq:repr (nt:join $root "tests/python_entry_census.py")) (preq:repr $result)
                  (preq:repr (nt:join $root "src/native_llm.metta"))
                  (preq:repr (nt:join $root "tests/native_test_support.metta")) $forms))))
          ($env (nq:guest-env $world "fixture-model"
                  (("METTACLAW_MODEL_COMMENTARY" "1") ("METTACLAW_STREAM_DIR" (nq:path $world "display")))))
          ($output (nt:exit-ok (proc:run ((nt:join $sdk "cetta") "--lang" "petta" "--profile" "extended"
                                          "--import-mode" "ancestor-walk" "--quiet" $entry)
                                         $root $env 120000 4194304)))
          ($_ (nt:require census-scenario-witness (nt:contains $output "CENSUS_PROVIDER_OK")))
          ($_ (nt:require census-provider-single-post (nt:equal (nq:posts $world) 1)))
          ($_ (nq:close $world)))
     (census:total $name $result)))

(: census:check (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (census:check $ceilings $name $total)
   (let $ceiling (pcall:fact $ceilings $name)
     (let $_ (println! (PYTHON_ENTRY_CEILING $name $total $ceiling))
       (nt:require python-entries-at-or-below-ceiling (<= $total $ceiling)))))

(= (census:run)
   (let* (($root (system:cwd)) ($sdk (nt:required-env "NATIVE_TEST_SDK"))
          ($core (nt:required-env "NATIVE_TEST_CORE")) ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($fixture (nt:join $artifacts "native-http-peer")) ($bin (nt:join $artifacts "bin"))
          ($_ (fs:make-directories $bin 448)) ($env (nq:base-env $root $artifacts))
          ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror"
             "tests/fixtures/native_http_peer.c" "-o" $fixture) $root $env 30000 65536)))
          ($_ (nt:exit-ok (proc:run ("ln" "-sfn" $fixture (nt:join $bin "systemctl")) $root $env 5000 65536)))
          ($ceilings (preq:parse (fs:read-text "tests/python_entry_census.json")))
          ($coding (census:probe $root $sdk $core $artifacts $fixture $bin "coding-two-turn"
                     "tests/integrated_coding_turn_probe.metta" "INTEGRATED_CODING_TURN_OK"))
          ($delivery (census:probe $root $sdk $core $artifacts $fixture $bin "activity-delivery"
                       "tests/activity_delivery_full_turn_probe.metta" "ACTIVITY_DELIVERY_FULL_TURN_OK"))
          ($provider (census:provider $root $sdk $core $artifacts $fixture "native-provider-command"))
          ($_ (census:check $ceilings "coding-two-turn" $coding))
          ($_ (census:check $ceilings "activity-delivery" $delivery))
          ($_ (census:check $ceilings "native-provider-command" $provider)))
     PythonEntryCensusPassedV1))
!(nt:require census-completed (nt:equal (collapse (census:run)) (PythonEntryCensusPassedV1)))
!(println! PYTHON_ENTRY_CENSUS_PASSED)

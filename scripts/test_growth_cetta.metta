; Growth checks of ported paths. Run from the checkout root with
; NATIVE_TEST_CORE, NATIVE_TEST_SDK and NATIVE_TEST_ARTIFACTS.
!(import! &self ../tests/growth_support.metta)

(= (growth-suite:import $root $module) (format-args "!(import! &self {})" ((repr (nt:join $root $module)))))
(= (growth-suite:run)
   (let* (($root (system:cwd)) ($core (nt:required-env "NATIVE_TEST_CORE"))
          ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS"))
          ($directory (nt:join $artifacts (str:concat "growth-" (system:uuid4-hex))))
          ($fixture (nt:join $artifacts "native-http-peer"))
          ($env (nt:environment $artifacts))
          ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror"
             "tests/fixtures/native_http_peer.c" "-o" $fixture) $root $env 30000 65536)))
          ($core-engine (nt:join $core "cetta"))
          ($controls (growth-suite:import $root "tests/growth/controls.metta"))
          ($_ (nt:require growth-linear-control-passes
                (growth:linear? $root $core-engine $env $fixture $directory $controls "growth-control:linear" 20000 4096)))
          ($_ (nt:require growth-model-selection-linear
                (growth:linear? $root $core-engine $env $fixture $directory
                  (growth-suite:import $root "tests/growth/model_selection.metta") "model-growth:workload" 20000 4096)))
          ($_ (nt:require growth-journal-append-linear
                (growth:linear? $root $core-engine $env $fixture $directory
                  (growth-suite:import $root "tests/growth/loop_host.metta") "loop-growth:workload" 20000 4096)))
          ($_ (nt:require growth-commentary-rewrite-linear
                (growth:linear? $root $core-engine (cons-atom ("METTACLAW_MODEL_COMMENTARY" "1") $env) $fixture $directory
                  (growth-suite:import $root "tests/growth/commentary_contract.metta") "commentary-growth:workload" 20000 4096)))
          ($_ (nt:require growth-quadratic-mutant-fails
                (not (growth:linear? $root $core-engine $env $fixture $directory $controls "growth-control:quadratic" 500 4096)))))
     GrowthSuitePassedV1))
!(nt:require growth-suite-completed (nt:equal (collapse (growth-suite:run)) (GrowthSuitePassedV1)))
!(println! GROWTH_SUITE_PASSED)

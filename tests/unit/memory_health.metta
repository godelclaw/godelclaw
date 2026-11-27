; Memory health states the configured backend and whether its native service
; is reachable, as the Python check answered on the same configurations: a
; missing, malformed, non-object or non-UTF-8 file is no configuration; a
; native backend names itself and its service's state, unreachable when the
; socket is absent or unset. The probe only connects: a listening service
; receives no bytes, once per check. The boot line is the report.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/memory_health_cetta.metta)
!(import! &self (library json))

(= (mh:config) (nt:required-env "METTACLAW_MEMORY_BACKEND_CONFIG"))
(= (mh:home) (nt:required-env "HOME"))
(= (mh:root) (nt:required-env "TEST_REPOSITORY"))
(= (mh:fill $text) (str:join (mh:home) (str:split "{dir}" $text)))
; Write the configuration's exact bytes (printf %b), or remove it.
(= (mh:write $bytes)
   (if (== $bytes Missing) (if (fs:exists (mh:config)) (fs:remove (mh:config)) True)
     (case (proc:run ("sh" "-c" "printf '%b' \"$1\" > \"$2\"" "sh" (mh:fill $bytes) (mh:config)) (mh:home)
                     (nt:environment (mh:home)) 10000 4096)
       (((proc:exited 0 $o $e) True) ($x False)))))
(= (mh:agrees? $bytes $backend $report)
   (let $_ (mh:write $bytes)
     (and (== (json:parse (json:stringify-python (memory-health:backend (mh:root)))) (json:parse (mh:fill $backend)))
          (== (memory-health:report (mh:root)) $report))))

; (bytes as a printf %b argument, Python's backend, Python's report)
!(nt:require each-configuration-answers-as-recorded
   (== (filter-atom (
      (Missing "{\"mode\": \"unconfigured\"}" "memory-health: no native memory is configured (backend unconfigured)")
      ("{not JSON" "{\"mode\": \"unconfigured\"}" "memory-health: no native memory is configured (backend unconfigured)")
      ("[]" "{\"mode\": \"unconfigured\"}" "memory-health: no native memory is configured (backend unconfigured)")
      ("null" "{\"mode\": \"unconfigured\"}" "memory-health: no native memory is configured (backend unconfigured)")
      ("{\"mode\":\"\\0377\"}" "{\"mode\": \"unconfigured\"}" "memory-health: no native memory is configured (backend unconfigured)")
      ("{\"mode\": \"native\", \"name\": \"fixture\"}" "{\"mode\": \"native\", \"name\": \"fixture\", \"socket\": null}"
       "memory-health: native backend fixture — service UNREACHABLE")
      ("{\"mode\": \"native\", \"socket\": \"{dir}/absent\"}" "{\"mode\": \"native\", \"name\": null, \"socket\": \"{dir}/absent\"}"
       "memory-health: native backend None — service UNREACHABLE")
      ("{\"mode\": \"other\"}" "{\"mode\": \"other\", \"name\": null, \"socket\": null}" "memory-health: no native memory is configured (backend other)")
      ("{\"mode\": null}" "{\"mode\": null, \"name\": null, \"socket\": null}" "memory-health: no native memory is configured (backend None)")
      ("{\"mode\": [\"other\"]}" "{\"mode\": [\"other\"], \"name\": null, \"socket\": null}" "memory-health: no native memory is configured (backend ['other'])")
      ("{}" "{\"mode\": \"unconfigured\", \"name\": null, \"socket\": null}" "memory-health: no native memory is configured (backend unconfigured)"))
      $case (let ($bytes $backend $report) $case (not (mh:agrees? $bytes $backend $report))))
       ()))

!(let* (($_ (mh:write "{\"mode\":\"native\",\"name\":\"fixture\"}"))
        ($run (proc:run ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--no-results" "--lang" "petta" "--profile" "extended"
                         "--import-mode" "ancestor-walk" (path:join (mh:root) "scripts/memory_health_cetta.metta"))
                        (mh:root) (cons-atom ("METTACLAW_MEMORY_BACKEND_CONFIG" (mh:config))
                                    (cons-atom ("METTACLAW_HEALTH_RUNTIME_ROOT" (mh:home)) (nt:environment (mh:home))))
                        30000 65536)))
   (nt:require the-boot-line-is-the-report
     (case $run (((proc:exited 0 $out $err)
                  (== $out "[memory_health] memory-health: native backend fixture — service UNREACHABLE\n"))
                 ($x False)))))

; A listening service: the counting peer records what each connection sends.
!(bind! &mh-peer (new-state none))
(= (mh:wait $path $peer $left)
   (if (fs:exists $path) True (if (<= $left 0) False (let $_ (proc:wait $peer 20) (mh:wait $path $peer (- $left 1))))))
!(let* (($directory (path:join (mh:home) "peer")) ($_ (fs:make-directories $directory 448))
        ($peer (proc:spawn ((nt:required-env "NATIVE_CORE_PEER") "unix" $directory)
                           ((env (nt:environment $directory)) (max-bytes 65536) (lifetime owner))))
        ($_ (change-state! &mh-peer $peer))
        ($socket (path:join $directory "socket"))
        ($_ (nt:require the-listener-is-ready (mh:wait $socket $peer 250)))
        ($_ (mh:write (str:join "" ("{\"mode\":\"native\",\"name\":\"fixture\",\"socket\":\"" $socket "\"}"))))
        ($reports (map-atom (1 2 3 4 5 6 7 8) $n (memory-health:report (mh:root))))
        ($_ (proc:wait $peer 200))
        ($count (str:trim (fs:read-text (path:join $directory "count"))))
        ($requests (filter-atom (fs:entries $directory) $name (str:starts-with $name "request-")))
        ($sent (filter-atom $requests $name (not (== (fs:read-text (path:join $directory $name)) "")))))
   (nt:require a-listening-service-is-reachable-and-receives-nothing
     (and (== (unique-atom $reports) ("memory-health: native backend fixture — service reachable"))
     (and (== $count "8")
     (and (== (size-atom $requests) 8)
          (== $sent ()))))))

!(println! UNIT_MEMORY_HEALTH_OK)

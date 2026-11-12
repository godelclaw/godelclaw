; Growth workload: the work-status listing's observation of n pinned sessions:
; one snapshot of them all, each session's pin and label and its detail, and
; the context view.
!(import! &self ../growth_support.metta)
!(import! &self ../../src/coding_io_cetta.metta)
!(import! &self ../../src/coding_estate_cetta.metta)
!(let* (($base (nt:join (growth:directory) (format-args "estate-{}" ((system:uuid4-hex)))))
        ($runtime (path:join $base "memory/coding-runtimes/aaaa1111"))
        ($_ (fs:make-directories (path:join $runtime "src") 504))
        ($_ (fs:write (path:join $runtime "src/llm_ledger.metta") "; native\n"))
        ($engine (path:join $base "engine"))
        ($_ (fs:write $engine "engine"))
        ($pin (json-stringify (JsonObject ((JsonPair "root" (JsonString $runtime)) (JsonPair "digest" (JsonString "aaaa1111"))
                                           (JsonPair "binary" (JsonString $engine)) (JsonPair "phase" (JsonString "ended"))))))
        ($_ (nt:exit-ok (proc:run ("sh" "-c" "i=0; while [ $i -lt $1 ]; do d=\"$2/memory/coding/work-$i\"; mkdir -p \"$d\"; printf '{\"task\":\"t\"}' > \"$d/task.json\"; printf '{\"status\":\"stopped\",\"steps\":2,\"updated\":1794500000}' > \"$d/state.json\"; printf '%s' \"$3\" > \"$d/runtime.json\"; i=$((i+1)); done"
                                   "sh" (repr (growth:size)) $base $pin)
                                  $base (nt:environment $base) 600000 4096))))
   (growth:measure
     (let (EstateSessions $facts $sessions) (estate:sessions-view $base)
       (str:concat (estate:context-view-root $base "2026-11-12")
                   (repr (size-atom (map-atom $sessions $s (str:concat (estate:status-suffix $facts $s)
                                                                       (estate:status-detail $facts $s)))))))))

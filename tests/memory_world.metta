; A native memory world for suites: a seeded space-service store per case,
; the world's loopback embedding peer (fixture-v1), guests that load the memory
; module as the agent does, and runs through the agent's own bootstrap.
!(import! &self ./provider_native_qualification.metta)
!(import! &self (library lib_tensor))
!(import! &self (library re))

(= (mc:prefix $root)
   (str:join "" ("!(import! &self " (repr (path:join $root "src/semantic_memory_cetta.metta")) ")
(= (fixture:drain $n)
   (let $_ (semantic:maintain)
     (if (== (memory-pending) ()) True
       (if (== $n 0) (FixturePending (memory-pending))
         (let (io:pending $h) (io:submit (timer:after 10)) (let $_ (io:wait $h 1000) (fixture:drain (- $n 1))))))))
")))
(= (mc:guest $world $root $forms $env)
   (nq:output (nq:guest $world "fixture-model" "petta" (str:concat (mc:prefix $root) $forms) $env)))
(= (mc:env $world $config $retry)
   (let (NativeProviderTestWorldV1 $_ $_ $_ $_ $_ $endpoint $_ $_ $_ $_) $world
     (("METTACLAW_MEMORY_BACKEND_CONFIG" $config) ("METTACLAW_EMBED_ENDPOINT" $endpoint)
      ("METTACLAW_EMBED_PROTOCOL" "fixture-v1") ("METTACLAW_MEMORY_RETRY_SECONDS" $retry))))
(= (mc:serve $service $socket $data $directory)
   (let* (($_ (if (fs:exists $socket) (fs:remove $socket) True))
          ($server (proc:spawn ($service "--socket" $socket "--data" $data)
                    ((env (nt:environment $directory)) (max-bytes 65536))))
          ($_ (nq:wait-file $server $socket 100)))
     $server))
(= (mc:stop $server) (let* (($_ (proc:cancel $server)) ($_ (proc:await $server)) ($_ (proc:close $server))) True))
; A store named $case holding the two original records. A reply that is not
; ok stops the suite with what the store said.
(= (mc:store $world $service $case)
   (let* (($socket (nq:path $world (str:concat $case ".sock"))) ($data (nq:path $world (str:concat $case "-data")))
          ($server (mc:serve $service $socket $data (nq:path $world ""))))
     (case (space:open $socket $case native)
       (((reply $opened-id ok $view) (mc:seed $world $server $socket $data $case $view))
        ($refused (mc:store-failed $case open $refused))))))
(= (mc:seed $world $server $socket $data $case $view)
   (let* (($a (tensor:from f64 (3) (1.0 0.0 0.0))) ($b (tensor:from f64 (3) (0.0 1.0 0.0))))
     (case (space:batch $socket $view
             (ops (add (MemoryProfile "unit-profile" "{\"embedding_output_dim\":3}"))
                  (add (Memory "a" 1 "unit-profile" $a "old alpha" (("time" "original"))))
                  (add (Memory "b" 1 "unit-profile" $b "beta reference" (("time" "original"))))))
       (((reply $seed-id ok $seeded)
         (let $config (nq:path $world (str:concat $case ".json"))
           (let $_ (fs:write $config (str:join "" ("{\"mode\":\"native\",\"name\":" (repr $case) ",\"socket\":" (repr $socket) "}")))
             (MemoryCase $server $socket $data $config))))
        ($refused (mc:store-failed $case seed $refused))))))
(= (mc:store-failed $case $step $reply)
   (let $_ (println! (MEMORY_STORE_FAILED $case $step $reply)) (system:exit 3)))

; A probe file run after the agent's bootstrap, as the launcher runs a program,
; with the world's guest environment and $extra over it.
(= (mc:bootstrap $world $root $sdk $probe $extra)
   (nt:exit-ok (proc:run ((path:join $sdk "cetta") "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk"
                          "--quiet" (path:join $root "cetta_bootstrap.metta") $probe)
                         $root (nq:guest-env $world "fixture-model" $extra) 30000 16777216)))
; The memory settings with an engine state directory of the case's own, so
; its attention snapshot and context policy stay with that case.
(= (mc:case-dir $world $case) (nq:path $world (str:concat $case "-state")))
(= (mc:case-env $world $config $case $retry)
   (let $_ (fs:make-directories (mc:case-dir $world $case) 448)
     (cons-atom ("METTACLAW_ENGINE_STATE_PATH" (path:join (mc:case-dir $world $case) "engine"))
                (mc:env $world $config $retry))))
; A seeded case with its own engine state directory: (OpenCase server socket config env).
(= (mc:open-case $world $service $case)
   (let (MemoryCase $server $socket $data $config) (mc:store $world $service $case)
     (OpenCase $server $socket $config (mc:case-env $world $config $case ".05"))))
; More records in the store named $case, each row (id vector document metadata).
(= (mc:add-op ($id $vector $doc $meta)) (add (Memory $id 1 "unit-profile" $vector $doc $meta)))
(= (mc:add $socket $case $rows)
   (let (reply $opened-id ok $view) (space:open $socket $case native)
     (case (space:batch $socket $view (cons-atom ops (map-atom $rows $row (mc:add-op $row))))
       (((reply $id ok $result) True) ($bad (let $_ (println! (MEMORY_ADD_REFUSED $bad)) False))))))
; Whether $text contains every one of $parts.
(= (mc:has-all $text $parts)
   (if (== $parts ()) True (if (nt:contains $text (car-atom $parts)) (mc:has-all $text (cdr-atom $parts)) False)))
; Everything after the first $marker, or "".
(= (mc:after $text $marker)
   (case (str:find $text $marker)
     (((TextNotFound $_) "")
      ($at (str:byte-slice $text (+ $at (str:byte-length $marker)) (str:byte-length $text))))))
; The rest of the line after the first $marker, or "".
(= (mc:line $text $marker) (car-atom (str:split "\n" (mc:after $text $marker))))
; Whether the regular expression $pattern matches somewhere in $text.
(= (mc:matches? $text $pattern)
   (case (re:find-all (re:compile $pattern) $text) (((re:matches LNil) False) ((re:matches (LCons $m $rest)) True) ($other False))))
; The ids of the memory hits in $text, in order.
(= (mc:ids $matches $text)
   (case $matches ((LNil ()) ((LCons $m $rest) (cons-atom (re:group-text $m $text 1) (mc:ids $rest $text))))))
(= (mc:hit-ids $text)
   (case (re:find-all (re:compile "MemoryHit \"([^\"]+)\"") $text) (((re:matches $matches) (mc:ids $matches $text)) ($other ()))))
(= (mc:count $text $needle) (- (size-atom (str:split $needle $text)) 1))

; Concurrent publication fixture for observatory_qualification.metta.
; Publishes N batches under exclusive .collector.lock where each batch writes
; +1 turn, +1 effect, +1 sample (cross-file invariant: turns == effects == samples).
!(import! &self ../../src/observatory_cetta.metta)

(= (ocp:publish-one $root $t $n)
   (let* (($lock (fs:open-write (path:join $root ".collector.lock") ("create" "append") 384))
          ($held (fs:lock $lock exclusive)))
     (case $held
       ((fs:locked
         (let* (($o1 (change-state! &obs-options (JsonObject ((JsonPair "--data" $root)))))
                ($rec (op:recover $root))
                ($w1 (change-state! &obs-write-failed False))
                ($c1 (change-state! &obs-counts (JsonObject ())))
                ($cur (sf:native-object (path:join $root "cursor.json")))
                ($c2 (change-state! &obs-cursor $cur))
                ($b1 (op:begin $root))
                ($a1 (obs:append "turns.jsonl" (JsonObject ((JsonPair "t" $t) (JsonPair "engine" "cetta")))))
                ($d1 (host:wait () 15))
                ($a2 (obs:append "effects.jsonl" (JsonObject ((JsonPair "t" $t) (JsonPair "turn" $n)
                       (JsonPair "kind" "send") (JsonPair "disposition" "returned") (JsonPair "reason" "ok")))))
                ($d2 (host:wait () 15))
                ($a3 (obs:append "samples.jsonl" (JsonObject ((JsonPair "t" $t) (JsonPair "engine" "cetta")
                       (JsonPair "model" "test-model") (JsonPair "rss_mb" 50) (JsonPair "swap_mb" 0) (JsonPair "uptime_s" 3600)))))
                ($sc (obs:set-cursor "journal_cursor" (str:concat "batch-" (repr $n))))
                ($fin (obs:finish-collection True True True))
                ($ul (fs:unlock $lock))
                ($cl (fs:close $lock)))
           $fin))
        ($bad (progn (fs:close $lock) 1))))))

(= (ocp:loop $root $t $i $total)
   (if (> $i $total) 0
     (let $code (ocp:publish-one $root (+ $t $i) $i)
       (if (== $code 0) (ocp:loop $root $t (+ $i 1) $total) $code))))

!(let* (($args (cli:strings (system:args)))
        ($root (index-atom $args 0))
        ($t (cli:number (index-atom $args 1) 1795155000))
        ($total (cli:number (index-atom $args 2) 6))
        ($ready (fs:write (path:join $root "publisher-started") "1"))
        ($code (ocp:loop $root $t 1 $total)))
   (system:exit $code))

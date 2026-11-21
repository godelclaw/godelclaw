
; Real launchers and native provider/ledger/channel peers, with restart evidence.
!(import! &self ./provider_native_qualification.metta)
!(import! &self ../src/state_file_cetta.metta)
!(import! &self ../src/coding_io_cetta.metta)
; A settled checkpoint whose payload holds $needle; an input is checkpointed
; before the provider call, so the first settled record need not be the turn's.
(= (core-boot:settled-with? $path $needle)
   (let $record (sf:native-object $path)
     (and (== (sf:get $record "status" "") "settled") (nt:contains (sf:get $record "payload" "") $needle))))
(= (core-boot:wait-checkpoint $owner $path $remaining $needle)
   (if (<= $remaining 0) (progn (println! (BOOT_DID_NOT_CHECKPOINT $path (proc:poll $owner))) False)
     (if (and (fs:is-file $path) (core-boot:settled-with? $path $needle)) True
       (core-boot:wait-next $owner $path $remaining $needle))))
(= (core-boot:wait-next $owner $path $remaining $needle)
   (case (proc:wait $owner 100)
     (((proc:exited $code $stdout $stderr)
       (progn (println! (EARLY_MODE_EXIT $code $stdout $stderr)) False))
      ((proc:result $status $kind $stdout $stderr $capture)
       (let $_ (fs:write (path:join (path:dirname $path) "mode-native-exit.log")
                  (repr (proc:result $status $kind $stdout $stderr $capture)))
         (if (and (== $status (proc:exited 0)) (core-boot:settled-with? $path $needle)) True
           (progn (println! (EARLY_MODE_EXIT $status $kind $stdout $stderr $capture)) False))))
      ($running (core-boot:wait-checkpoint $owner $path (- $remaining 1) $needle)))))
; The service's environment names none of these; drop the world's defaults.
(= (core-boot:without $environment $names)
   (filter-atom $environment $pair (not (erv:contains $names (car-atom $pair)))))
(= (core-boot:wait-exit $owner $remaining)
   (if (<= $remaining 0) (progn (println! (MODE_DID_NOT_EXIT_ON_SWITCH (proc:poll $owner))) False)
     (case (proc:wait $owner 100)
       (((proc:exited $code $_ $_) (== $code 0))
        ((proc:result $status $_ $_ $_ $_) (== $status (proc:exited 0)))
        ($running (core-boot:wait-exit $owner (- $remaining 1)))))))
(= (core-boot:wait-posts $world $owner $count $remaining)
   (if (or (<= $remaining 0) (>= (core-boot:posts $world) $count)) True
     (let $_ (proc:wait $owner 20) (core-boot:wait-posts $world $owner $count (- $remaining 1)))))
(= (core-boot:posts $world)
   (if (fs:is-file (nq:path $world "count")) (nq:posts $world) 0))
; A setting the test provider reads at each request, replaced whole: a
; running mode's request never finds it half written.
(= (core-boot:set $path $text) (nt:require setting-replaced (== (sf:replace $path $text 384 True) True)))
(= (core-boot:chmod $path $mode)
   (proc:run ("/bin/chmod" $mode $path) (system:cwd) (("PATH" "/usr/bin:/bin")) 5000 1024))
(= (core-boot:count $text $needle) (- (size-atom (str:split $needle $text)) 1))
(= (core-boot:intent-path $runtime) (path:join $runtime "agent-history-intent.json"))
(= (core-boot:intent $runtime) (sf:native-object (core-boot:intent-path $runtime)))
(= (core-boot:intent-status $runtime) (sf:get (core-boot:intent $runtime) "status" "none"))
; Another mode's valid append, torn after its first 12 bytes, whose record
; cannot be read for now (mode 0000): Omega must take no input, ask no model
; and run no tool until it can be read; then it completes that append from its
; record and its own record follows.
(= (core-boot:older-payload) "(\"2026-11-18 21:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"OLDER_ITER_RECORD\" \"\n\")\n")
(= (core-boot:held-intent $shared) (path:join (path:join $shared "iter") "agent-history-intent.json"))
(= (core-boot:held-record $shared $history)
   (case (fs:metadata $history follow-observed)
     (((fs:info-v2 $_ $size (fs:identity $device $inode) $_ $_ $_ $_)
       (let* (($payload (core-boot:older-payload)) ($intent (core-boot:held-intent $shared))
              ($_ (fs:make-directories (path:dirname $intent) 448))
              ($_ (fs:write $intent (json:stringify-python (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "pending")
                     (JsonPair "path" $history) (JsonPair "payload" $payload) (JsonPair "sha256" (str:sha256 $payload))
                     (JsonPair "offset" $size) (JsonPair "device" $device) (JsonPair "inode" $inode))))))
              ($owner (fs:open-write $history ("append") 384))
              ($_ (fs:write-all $owner (str:byte-slice $payload 0 12))) ($_ (fs:close $owner)))
         (core-boot:chmod $intent "0000"))))))
(= (core-boot:omega-held $world $owner $shared $history $size)
   (let* (($_ (proc:wait $owner 3000))
          ($_ (nt:require omega-held-before-dispatch (== (nq:posts $world) 0)))
          ($_ (nt:require omega-held-wrote-nothing
                (== (str:byte-length (fs:read-text $history)) (+ $size 12)))))
     (core-boot:chmod (core-boot:held-intent $shared) "0600")))
(= (core-boot:older-in-order $history $size)
   (let* (($text (fs:read-text $history)) ($older (core-boot:older-payload))
          ($after (+ $size (str:byte-length $older))))
     (and (== (str:byte-slice $text $size $after) $older)
          (and (== (core-boot:count $text "OLDER_ITER_RECORD") 1)
               (case (str:find $text "boot-test operator message")
                 (((TextNotFound $_) False) ($at (>= $at $after))))))))
(= (core-boot:omega-results $history $payload)
   (let* (($lines (filter-atom (str:split "\n" (fs:read-text $history)) $line
                    (str:starts-with $line "(HistoryResultV1 omega ")))
          ($at (str:find $payload "\n(HistoryResultV1 "))
          ($record (case $at (((TextNotFound $_) "") ($found (str:byte-slice $payload 0 $found)))))
          ($of (str:join "" ("(of \"" (str:byte-slice (str:sha256 $record) 0 16) "\")")))
          ($_ (nt:require omega-result-per-call (== (size-atom $lines) 3)))
          ($_ (nt:require omega-results-name-their-record
                (and (!= $record "") (== (filter-atom $lines $line (not (nt:contains $line $of))) ()))))
          ($_ (nt:require omega-results-in-call-order
                (and (nt:contains (index-atom $lines 0) " 0 returned full ")
                     (and (nt:contains (index-atom $lines 1) " 1 returned full ")
                          (nt:contains (index-atom $lines 2) " 2 returned full "))))))
     (nt:require omega-equal-calls-own-results
       (and (nt:contains (index-atom $lines 1) ": 1 bytes")
            (and (nt:contains (index-atom $lines 2) ": 2 bytes") (not (nt:contains (index-atom $lines 1) ": 2 bytes")))))))
; Until the history holds $needle and the mode's own record is settled.
(= (core-boot:wait-recorded $owner $path $needle $runtime $remaining)
   (if (or (<= $remaining 0)
           (and (nt:contains (fs:read-text $path) $needle) (== (core-boot:intent-status $runtime) "settled"))) True
     (let $_ (proc:wait $owner 100) (core-boot:wait-recorded $owner $path $needle $runtime (- $remaining 1)))))
; The agents' models speak the Responses API, which also checks each request
; against the native request contract: the fixture answers as it does.
(= (core-boot:call $id $name $arguments)
   (JsonObject ((JsonPair "type" "function_call") (JsonPair "id" (str:concat "f" $id)) (JsonPair "call_id" $id)
                (JsonPair "name" $name) (JsonPair "arguments" $arguments) (JsonPair "status" "completed"))))
(= (core-boot:reply $calls)
   (json:stringify-python (JsonObject ((JsonPair "id" "fixture") (JsonPair "status" "completed") (JsonPair "output" (JsonArray $calls))
     (JsonPair "usage" (JsonObject ((JsonPair "input_tokens" 10) (JsonPair "output_tokens" 2))))))))
; Two of Iter's own tools that fail: one returns an Error, one's worker dies.
(= (core-boot:test-tools $runtime)
   (let* (($tools (path:join $runtime "tools")) ($_ (fs:make-directories $tools 448))
          ($_ (fs:write (path:join $tools "fail_returned.metta")
                "(ComponentDeclarationV1 \"Test tool: always fails.\" ())\n(= (component:run $input) (Error test-tool (Fault deliberate-failure)))\n"))
          ($_ (fs:write (path:join $tools "crash_tool.metta")
                "(ComponentDeclarationV1 \"Test tool: its worker dies.\" ())\n(= (component:run $input) (system:exit 3))\n")))
     True))
; Until the mode's checkpoint is settled: a running mode publishes a record
; before each write and settles it after, so a read can land between them.
(= (core-boot:wait-settled $owner $path $remaining)
   (if (or (<= $remaining 0) (== (sf:get (sf:native-object $path) "status" "") "settled")) True
     (let $_ (proc:wait $owner 100) (core-boot:wait-settled $owner $path (- $remaining 1)))))
; Offline controls of what Iter sends after a refusal, each a run of the mode
; through several failed steps: the provider rejects every request; the
; configuration refuses every request before it is sent; an operator limits
; submissions to one. Each run starts without the previous run's pacing.
(= (core-boot:iter-controls $root $world $command $environment $mode-path $recycle)
   (let* (($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "iter"))))))
          ($health (nq:path $world "health.json"))
          ; The provider rejects every request: the unchanged request is sent once.
          ($_ (core-boot:fresh $health $recycle))
          ($_ (core-boot:set (nq:path $world "status") "400"))
          ($posts (core-boot:posts $world))
          ($rejected (core-boot:run-until-posts $root $world $command $environment (+ $posts 1) 4000))
          ($_ (fs:write (nq:path $world "control-rejected.log") $rejected))
          ($_ (nt:require rejected-request-sent-once (== (core-boot:posts $world) (+ $posts 1))))
          ($_ (nt:require rejected-request-not-resent (>= (core-boot:count $rejected "it is not sent again") 2)))
          ; The configuration refuses every request before it is sent: tried once, never sent.
          ($_ (core-boot:fresh $health $recycle))
          ($tight (cons-atom ("METTACLAW_LLM_RESPONSE_BYTES" "10")
                    (core-boot:without $environment ("METTACLAW_LLM_RESPONSE_BYTES"))))
          ($refused (core-boot:run-until-text $root $command $tight $health "\"valueerror\"" 4000))
          ($_ (fs:write (nq:path $world "control-refused.log") $refused))
          ($_ (nt:require refused-request-never-sent (== (core-boot:posts $world) (+ $posts 1))))
          ($_ (nt:require refused-request-tried-once (== (core-boot:count $refused "failed request: admission-error") 1)))
          ($_ (nt:require refused-request-not-retried (>= (core-boot:count $refused "it is not sent again") 2)))
          ; An operator's limit of one submission: the next call fails before it is sent.
          ($_ (core-boot:fresh $health $recycle))
          ($_ (core-boot:set (nq:path $world "status") "200"))
          ($_ (fs:write (nq:path $world "response.body")
                (core-boot:reply ((core-boot:call "t8" "shell" (json:stringify-python (JsonObject ((JsonPair "cmd" ":")))))))))
          ($limit (nq:path $world "submission-limit.json"))
          ($_ (fs:write $limit "{\"remaining\": 1, \"purpose\": \"coding\"}"))
          ($limited (core-boot:run-until-posts $root $world $command $environment (+ $posts 2) 3000))
          ($_ (fs:write (nq:path $world "control-limited.log") $limited))
          ($spent (sf:get (sf:native-object $limit) "remaining" -1))
          ($_ (fs:remove $limit))
          ($_ (nt:require limited-submission-sent-once (== (core-boot:posts $world) (+ $posts 2))))
          ($_ (nt:require limit-spent (== $spent 0)))
          ($_ (nt:require limit-refusal-reported (nt:contains $limited "failed request: submission-limit"))))
     True))
(= (core-boot:fresh $health $recycle)
   (let $_ (if (fs:exists $health) (fs:remove $health) True)
     (if (fs:exists $recycle) (fs:remove $recycle) True)))
; A run of the mode until the provider has $count requests, then for $settle
; milliseconds more; its result, with its output.
(= (core-boot:run-until-posts $root $world $command $environment $count $settle)
   (let* (($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (core-boot:wait-posts $world $owner $count 1500))
          ($_ (proc:wait $owner $settle)))
     (core-boot:ended $owner)))
; A run of the mode until $path holds $needle, then for $settle milliseconds.
(= (core-boot:run-until-text $root $command $environment $path $needle $settle)
   (let* (($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (core-boot:wait-text $owner $path $needle 1500))
          ($_ (proc:wait $owner $settle)))
     (core-boot:ended $owner)))
(= (core-boot:wait-text $owner $path $needle $remaining)
   (if (<= $remaining 0) False
     (if (if (fs:is-file $path) (nt:contains (fs:read-text $path) $needle) False) True
       (let $_ (proc:wait $owner 20) (core-boot:wait-text $owner $path $needle (- $remaining 1))))))
(= (core-boot:ended $owner)
   (let* (($_ (proc:cancel $owner)) ($ended (proc:await $owner)) ($_ (proc:close $owner))) (repr $ended)))
(= (core-boot:stop $owner) (let* (($_ (proc:cancel $owner)) ($_ (proc:await $owner)) ($_ (proc:close $owner))) True))
; The history cut to its first $bytes bytes, in place: the same file.
(= (core-boot:cut $path $bytes)
   (let* (($kept (str:byte-slice (fs:read-text $path) 0 $bytes))
          ($owner (fs:open-write $path ("truncate") 420))
          ($_ (fs:write-all $owner $kept)) ($_ (fs:close $owner))) True))
(= (core-boot:append $path $text)
   (let* (($owner (fs:open-write $path ("create" "append") 420)) ($_ (fs:write-all $owner $text)) ($_ (fs:close $owner))) True))
(= (core-boot:put $path $object $fields)
   (nt:require state-file-rewritten (== (sf:replace $path (json:stringify-python (sf:put-all $object $fields)) 384 True) True)))
; Iter's own record as a death leaves it: the intent pending, the history cut
; where the death stopped the write (none: all of it written).
(= (core-boot:interrupt $runtime $history $intent $cut)
   (let* (($_ (core-boot:put (core-boot:intent-path $runtime) $intent ((JsonPair "status" "pending"))))
          ($_ (if (== $cut none) True (core-boot:cut $history $cut)))) True))
(= (core-boot:relaunch $command $root $environment $runtime $history $needle)
   (let* (($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (core-boot:wait-recorded $owner $history $needle $runtime 300)))
     (core-boot:stop $owner)))
(= (core-boot:boundaries-seen $runtime)
   (core-boot:count (fs:read-text (path:join $runtime "runtime.jsonl")) "\"kind\":\"boundary\""))
; A restart that has finished starting: it has reached its loop's first boundary.
(= (core-boot:relaunch-started $command $root $environment $runtime)
   (let* (($seen (core-boot:boundaries-seen $runtime))
          ($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (core-boot:wait-boundary $owner $runtime $seen 300)))
     (core-boot:stop $owner)))
(= (core-boot:wait-boundary $owner $runtime $seen $remaining)
   (if (or (<= $remaining 0) (> (core-boot:boundaries-seen $runtime) $seen)) True
     (let $_ (proc:wait $owner 100) (core-boot:wait-boundary $owner $runtime $seen (- $remaining 1)))))
; Each interrupted state a death can leave, rebuilt on disk from the last
; settled record; each restart leaves the record in the history exactly once
; and runs no step again.
(= (core-boot:iter-boundaries $command $root $environment $runtime $history $mode-path $first-size $effect)
   (let* (($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "iter"))))))
          ($intent (core-boot:intent $runtime))
          ($payload (sf:get $intent "payload" "")) ($offset (sf:get $intent "offset" 0))
          ($_ (nt:require boundary-starts-settled (and (!= $payload "") (== (sf:get $intent "status" "") "settled"))))
          ($_ (core-boot:interrupt $runtime $history $intent $offset))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history $payload))
          ($_ (nt:require unwritten-record-written-once (== (core-boot:count (fs:read-text $history) $payload) 1)))
          ($_ (core-boot:interrupt $runtime $history $intent (+ $offset 10)))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history $payload))
          ($_ (nt:require half-written-record-completed-once (== (core-boot:count (fs:read-text $history) $payload) 1)))
          ($_ (core-boot:interrupt $runtime $history $intent none))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history $payload))
          ($_ (nt:require written-record-not-repeated (== (core-boot:count (fs:read-text $history) $payload) 1)))
          ; A death after the checkpoint and before its queue was placed: the
          ; checkpoint holds the records, and no intent was made of them.
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($whole (fs:read-text $history))
          ($records (str:byte-slice $whole $first-size (str:byte-length $whole)))
          ($_ (core-boot:put $checkpoint (sf:native-object $checkpoint) ((JsonPair "mirror_queued" $records))))
          ($_ (fs:remove (core-boot:intent-path $runtime)))
          ($_ (core-boot:cut $history $first-size))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history "boot-test operator message"))
          ($_ (nt:require checkpointed-turn-recorded-once
                (== (core-boot:count (fs:read-text $history) "boot-test operator message") 1)))
          ; A whole record still pending, another loop's record after it, and a
          ; history that cannot be read: a read that failed establishes
          ; nothing, so the record stays pending, and once the file reads
          ; again it settles without being written twice.
          ($last (core-boot:intent $runtime)) ($last-payload (sf:get $last "payload" ""))
          ($foreign "(\"2026-11-20 05:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"FOREIGN_SUFFIX_RECORD\" \"\n\" ((send \"other loop\")) \"\n\")\n")
          ($_ (core-boot:append $history $foreign))
          ($_ (core-boot:interrupt $runtime $history $last none))
          ($_ (core-boot:chmod $history "0200"))
          ($_ (core-boot:relaunch-started $command $root $environment $runtime))
          ($_ (core-boot:chmod $history "0644"))
          ($_ (nt:require unread-record-stays-pending (== (core-boot:intent-status $runtime) "pending")))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history $last-payload))
          ($_ (nt:require unread-record-settles-once (== (core-boot:count (fs:read-text $history) $last-payload) 1)))
          ($_ (nt:require foreign-suffix-kept-once (== (core-boot:count (fs:read-text $history) "FOREIGN_SUFFIX_RECORD") 1)))
          ; Started again with nothing to repair: nothing is written twice.
          ($before (fs:read-text $history))
          ($_ (core-boot:relaunch-started $command $root $environment $runtime))
          ($_ (nt:require repeated-restart-repeats-nothing
                (== (core-boot:count (fs:read-text $history) "boot-test operator message") 1)))
          ; Another history file in its place: nothing of the old one's frontier
          ; holds for it, and every message is written to it once.
          ($_ (nt:require replaced-history-written (== (sf:replace $history (str:byte-slice $before 0 $first-size) 420 True) True)))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history "boot-test operator message"))
          ($_ (nt:require replaced-history-gets-each-record-once
                (== (core-boot:count (fs:read-text $history) "boot-test operator message") 1)))
          ($_ (nt:require boundaries-replay-no-effect (== (fs:read-text $effect) "X"))))
     True))
; A queue that outlives the experience. With the history unwritable, the
; first placed record stays pending while the steps go on; the experience is
; cut at its bound (iter:clean) and its old tool results are truncated, and
; every message's record waits in the queue the checkpoint saves. Restarted
; with the history writable, each record is written once, in order, with the
; text it had when it was first saved. Each tool call prints the next of a
; sequence of markers, M1. M2. ...; each step has its own call ids.
(= (core-boot:iter-backlog $command $root $environment $world $runtime $history $mode-path)
   (let* (($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "iter"))))))
          ($counter (nq:path $world "backlog-counter"))
          ($script (str:join "" ("n=$(cat " (cio:shell-quote $counter) " 2>/dev/null || echo 0); n=$((n+1)); echo $n > "
                                 (cio:shell-quote $counter) "; echo \"M$n.\"")))
          ($arguments (json:stringify-python (JsonObject ((JsonPair "cmd" $script)))))
          ($posts (core-boot:posts $world))
          ($_ (core-boot:script-steps $world $arguments (+ $posts 1) 1 13))
          ($_ (core-boot:set (nq:path $world "status") "200"))
          ($_ (core-boot:chmod $history "0444"))
          ($before (fs:read-text $history))
          ($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ; Twelve steps of eleven messages each: past the experience's bound.
          ($_ (core-boot:wait-posts $world $owner (+ $posts 12) 3000))
          ; Then the provider refuses: no step is taken after the last saved one.
          ($_ (core-boot:set (nq:path $world "status") "400"))
          ($_ (core-boot:wait-posts $world $owner (+ $posts 13) 600))
          ($_ (proc:wait $owner 3000))
          ($_ (core-boot:stop $owner))
          ($taken (nq:read-number $counter))
          ($record (sf:native-object (path:join $runtime "native-checkpoint-v1.json")))
          ($kept (sf:get $record "payload" ""))
          ($queued (str:concat (sf:get (core-boot:intent $runtime) "payload" "") (sf:get $record "mirror_queued" "")))
          ($_ (nt:require backlog-steps-taken (>= $taken 120)))
          ($_ (nt:require backlog-history-unwritten (== (fs:read-text $history) $before)))
          ($_ (nt:require backlog-record-pending (== (core-boot:intent-status $runtime) "pending")))
          ($_ (nt:require backlog-prompt-bounded
                (case (json:parse $kept) (((JsonArray $items) (<= (size-atom $items) 100)) ($other False)))))
          ($_ (nt:require backlog-first-left-the-prompt (not (nt:contains $kept "M1."))))
          ($_ (nt:require backlog-first-kept-in-queue (core-boot:each-once $queued 1 $taken)))
          ; Restarted with the history writable: each record written once, in order.
          ($_ (core-boot:chmod $history "0644"))
          ($_ (core-boot:set (nq:path $world "status") "200"))
          ($_ (fs:write (nq:path $world "response.body") (core-boot:reply ((core-boot:call "t2" "nop" "{}")))))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history (format-args "M{}." ($taken))))
          ($text (fs:read-text $history))
          ($_ (nt:require backlog-written-once (core-boot:each-once $text 1 $taken)))
          ($_ (nt:require backlog-written-in-order (core-boot:in-order $text 1 $taken -1)))
          ($_ (nt:require backlog-written-as-first-saved (not (nt:contains $text "[TRUNCATED]"))))
          ; Started again before any later save: the queue the checkpoint still
          ; holds was placed already, and is not placed twice.
          ($_ (core-boot:relaunch-started $command $root $environment $runtime))
          ($_ (nt:require backlog-not-placed-twice (core-boot:each-once (fs:read-text $history) 1 $taken))))
     True))
; An abandoned record whose bytes equal the start of the newer queue: two
; occurrences that render alike are still two records. Restarted, the history
; holds the abandoned record and then the queue, each occurrence once; started
; again, nothing is written twice.
(= (core-boot:iter-equal-requeue $command $root $environment $runtime $history $mode-path)
   (let* (($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "iter"))))))
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($record (sf:native-object $checkpoint))
          ($lineage (sf:get $record "mirror_lineage" ""))
          ($top (sf:get $record "mirror_queued_top" 0))
          ($equal "(\"2026-11-20 08:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"EQUAL_BYTES_RECORD\" \"\n\")\n")
          ($after "(\"2026-11-20 08:00:01\" \"\n\" \"HUMAN_MESSAGE: \" \"AFTER_EQUAL_RECORD\" \"\n\")\n")
          ($intent (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "abandoned") (JsonPair "path" $history)
                     (JsonPair "payload" $equal) (JsonPair "sha256" (str:sha256 $equal))
                     (JsonPair "offset" 0) (JsonPair "device" 0) (JsonPair "inode" 0)
                     (JsonPair "from" $top) (JsonPair "frontier" (+ $top 1)) (JsonPair "lineage" $lineage))))
          ($_ (nt:require equal-intent-written
                (== (sf:replace (core-boot:intent-path $runtime) (json:stringify-python $intent) 384 True) True)))
          ($_ (core-boot:put $checkpoint $record ((JsonPair "mirror_queued" (str:concat $equal $after))
                                                  (JsonPair "mirror_queued_from" (+ $top 1))
                                                  (JsonPair "mirror_queued_top" (+ $top 3)))))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history "AFTER_EQUAL_RECORD"))
          ($text (fs:read-text $history))
          ($_ (nt:require equal-occurrences-both-written (== (core-boot:count $text "EQUAL_BYTES_RECORD") 2)))
          ($_ (nt:require later-occurrence-written-once (== (core-boot:count $text "AFTER_EQUAL_RECORD") 1)))
          ($first (core-boot:find-after $text "EQUAL_BYTES_RECORD" -1))
          ($second (core-boot:find-after $text "EQUAL_BYTES_RECORD" $first))
          ($_ (nt:require equal-occurrences-in-order
                (and (>= $first 0) (and (> $second $first) (> (core-boot:find-after $text "AFTER_EQUAL_RECORD" $second) $second)))))
          ($_ (core-boot:relaunch-started $command $root $environment $runtime))
          ($again (fs:read-text $history))
          ($_ (nt:require equal-requeue-not-repeated
                (and (== (core-boot:count $again "EQUAL_BYTES_RECORD") 2) (== (core-boot:count $again "AFTER_EQUAL_RECORD") 1)))))
     True))
; An older record still pending whose intent cannot be read, and a newer
; occurrence queued: an intent that cannot be read decides nothing, so it is
; not replaced and nothing is placed. Readable again, the older record is
; written and then the newer one, each once.
(= (core-boot:iter-unreadable-intent $command $root $environment $runtime $history $mode-path)
   (let* (($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "iter"))))))
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($record (sf:native-object $checkpoint))
          ($top (sf:get $record "mirror_queued_top" 0))
          ($older "(\"2026-11-20 09:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"OLDER_PENDING_RECORD\" \"\n\")\n")
          ($newer "(\"2026-11-20 09:00:01\" \"\n\" \"HUMAN_MESSAGE: \" \"NEWER_QUEUED_RECORD\" \"\n\")\n")
          ($intent (sf:put-all (core-boot:intent $runtime)
                     ((JsonPair "status" "pending") (JsonPair "payload" $older) (JsonPair "sha256" (str:sha256 $older))
                      (JsonPair "offset" (str:byte-length (fs:read-text $history)))
                      (JsonPair "from" $top) (JsonPair "frontier" (+ $top 1)))))
          ($path (core-boot:intent-path $runtime))
          ($written (json:stringify-python $intent))
          ($_ (nt:require older-intent-written (== (sf:replace $path $written 384 True) True)))
          ($_ (core-boot:put $checkpoint $record ((JsonPair "mirror_queued" $newer) (JsonPair "mirror_queued_from" (+ $top 1))
                                                  (JsonPair "mirror_queued_top" (+ $top 2)) (JsonPair "mirror_requeued" ""))))
          ($before (fs:read-text $history))
          ($_ (core-boot:chmod $path "000"))
          ($_ (core-boot:relaunch-started $command $root $environment $runtime))
          ($_ (core-boot:chmod $path "600"))
          ($_ (nt:require unreadable-intent-not-replaced (== (fs:read-text $path) $written)))
          ($_ (nt:require unreadable-intent-places-nothing (== (fs:read-text $history) $before)))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history "NEWER_QUEUED_RECORD"))
          ($text (fs:read-text $history))
          ($_ (nt:require older-written-once (== (core-boot:count $text "OLDER_PENDING_RECORD") 1)))
          ($_ (nt:require newer-written-once (== (core-boot:count $text "NEWER_QUEUED_RECORD") 1)))
          ($_ (nt:require older-before-newer
                (< (core-boot:find-after $text "OLDER_PENDING_RECORD" -1) (core-boot:find-after $text "NEWER_QUEUED_RECORD" -1)))))
     True))
; The position of $needle after position $after, or -1.
(= (core-boot:find-after $text $needle $after)
   (let $rest (str:char-slice $text (+ $after 1) (str:char-length $text))
     (case (str:find $rest $needle) (((TextNotFound $_) -1) ($at (+ $at (+ $after 1)))))))
; Requests from $first on answer step $k: ten calls of the marker script.
(= (core-boot:script-steps $world $arguments $first $k $last)
   (if (> $k $last) True
     (let* (($calls (core-boot:step-calls $arguments $k 1 ()))
            ($_ (fs:write (nq:path $world (format-args "response-{}.body" ((+ $first (- $k 1))))) (core-boot:reply $calls))))
       (core-boot:script-steps $world $arguments $first (+ $k 1) $last))))
(= (core-boot:step-calls $arguments $k $i $out)
   (if (> $i 10) (list:reverse $out)
     (core-boot:step-calls $arguments $k (+ $i 1)
       (cons-atom (core-boot:call (format-args "s{}c{}" ($k $i)) "shell" $arguments) $out))))
(= (core-boot:each-once $text $i $last)
   (if (> $i $last) True
     (let $count (core-boot:count $text (format-args "M{}." ($i)))
       (if (== $count 1) (core-boot:each-once $text (+ $i 1) $last)
         (let $_ (println! (BACKLOG_MARKER $i $count)) False)))))
(= (core-boot:in-order $text $i $last $after)
   (if (> $i $last) True
     (case (str:find $text (format-args "M{}." ($i)))
       (((TextNotFound $_) False)
        ($at (if (> $at $after) (core-boot:in-order $text (+ $i 1) $last $at)
               (let $_ (println! (BACKLOG_OUT_OF_ORDER $i $at $after)) False)))))))
(= (core-boot:outbox-texts $folder)
   (if (not (fs:exists $folder)) ()
     (map-atom (filter-atom (fs:entries $folder) $name (str:ends-with $name ".json")) $name
       (fs:read-text (path:join $folder $name)))))
(= (core-boot:mode $mode)
   (let* (($root (system:getenv-or-default "NATIVE_TEST_RUNTIME_ROOT" (system:cwd)))
          ($core (nt:required-env "NATIVE_TEST_CORE"))
          ($artifacts (nt:required-env "NATIVE_TEST_ARTIFACTS")) ($fixture (nt:required-env "NATIVE_TEST_HTTP_FIXTURE"))
          ($world (nq:world $root $core $core $artifacts $fixture $mode))
          ($directory (nq:path $world "")) ($shared (nq:path $world "modes")) ($runtime (path:join $shared $mode))
          ; As in the service: the agent's selections live under the root that
          ; run.sh passes as --repo, and no state path or state root is given.
          ($agent (nq:path $world "agent")) ($_ (fs:make-directories (path:join $agent "memory") 448))
          ($mode-path (path:join $agent "memory/loop_mode.json"))
          ($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" $mode))))))
          ($_ (fs:write (path:join $agent "memory/persistent.metta") "(active-model openai:fixture-selected-model)\n"))
          ($_ (fs:write (path:join $agent "memory/soul.md") "Boot-test agent identity\n"))
          ; A turn the framework loop recorded before the switch.
          ($agent-history (path:join $agent "memory/history.metta"))
          ($_ (fs:write $agent-history
                "(\"2026-11-18 20:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"framework-era marker message\" \"\n\" ((send \"framework-era reply\")) \"\n\")\n"))
          ($first-size (str:byte-length (fs:read-text $agent-history)))
          ; Omega starts beside another mode's torn append whose record cannot be
          ; read for now: it is held before it reads, asks or runs anything.
          ($_ (if (== $mode "omega") (core-boot:held-record $shared $agent-history) True))
          ; Iter's first run cannot write the agent history: its turn stays owed.
          ($_ (if (== $mode "iter") (core-boot:chmod $agent-history "0444") True))
          ($cwp-root (nq:path $world "cwp")) ($_ (fs:make-directories $cwp-root 448))
          ($cwp-socket (nq:path $world "c.sock"))
          ($cwp (proc:spawn ((nt:required-env "NATIVE_TEST_CWP_FIXTURE") $cwp-socket $cwp-root)
                   ((env (nt:environment $directory)) (max-bytes 65536))))
          ($_ (nq:wait-file $cwp (path:join $cwp-root "ready") 100))
          ; A delivered-send receipt behind it is transport state, not input.
          ($_ (fs:write (path:join $cwp-root "second.json")
                "[\"delivery\",\"42.0.00000000000000000001\",0,1,[\"delivered\",99]]"))
          ; One queued operator message, as the Telegram service offers it.
          ($_ (fs:write (path:join $cwp-root "delivery.json")
                "[\"input\",\"42.0\",\"message\",\"operator\",{\"update_id\":7,\"message\":{\"message_id\":5,\"date\":1795000000,\"chat\":{\"id\":42,\"type\":\"private\"},\"from\":{\"id\":7,\"first_name\":\"Operator\",\"is_bot\":false},\"text\":\"boot-test operator message\"}}]"))
          ($effect-path (nq:path $world "physical-effect")) ($recycle (nq:path $world "recycle"))
          ($shell (str:join "" ("printf X >> " (cio:shell-quote $effect-path) "; : > " (cio:shell-quote $recycle))))
          ($arguments (json:stringify-python (JsonObject ((JsonPair "cmd" $shell)))))
          ($call (core-boot:call "t1" "shell" $arguments))
          ; Omega's file tools share names with the framework's; each call must run once.
          ($counter (nq:path $world "append-counter"))
          ($append-arguments (json:stringify-python (JsonObject ((JsonPair "filename" $counter) (JsonPair "content" "x")))))
          ; Iter's step: an effect, a tool that fails, one whose worker dies, an
          ; ordinary value, and an effect after them all.
          ($after-path (nq:path $world "after-effect"))
          ($after (json:stringify-python (JsonObject ((JsonPair "cmd" (str:concat "printf B >> " (cio:shell-quote $after-path)))))))
          ($_ (if (== $mode "iter") (core-boot:test-tools $runtime) True))
          ($calls (if (== $mode "omega")
                    ($call (core-boot:call "t2" "append-file" $append-arguments) (core-boot:call "t3" "append-file" $append-arguments))
                    ($call (core-boot:call "t4" "fail_returned" "{}") (core-boot:call "t5" "crash_tool" "{}")
                           (core-boot:call "t6" "query" "{\"content\": \"unit\"}") (core-boot:call "t7" "shell" $after))))
          ($_ (fs:write (nq:path $world "response.body") (core-boot:reply $calls)))
          ($environment (core-boot:without
             (nq:guest-env $world "fixture-model"
               (("METTACLAW_RUNTIME_ROOT" $root)
                ("METTACLAW_TELEGRAM_SERVICE_SOCKET" $cwp-socket)
                ("METTACLAW_NATIVE_LEDGER" "1")))
             ("METTACLAW_LOOP_MODE_PATH" "METTACLAW_MODEL_STATE_PATH" "METTACLAW_ANTHROPIC_USAGE_PATH"
              "METTACLAW_MODE_STATE_ROOT" "METTACLAW_MODE_CETTA_BIN" "METTACLAW_AGENT_ROOT")))
          ; The arguments run.sh passes for a selected mode.
          ($command ("bash" (path:join $root "modes/launch_native.sh")
                       "--repo" $agent "--mode" $mode "--engine" "cetta" "--profile" "supervised"))
          ; The provider's first answer is a failure; the mode must stay up.
          ($_ (core-boot:set (nq:path $world "status") "503"))
          ($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (if (== $mode "omega") (core-boot:omega-held $world $owner $shared $agent-history $first-size) True))
          ($_ (core-boot:wait-posts $world $owner 1 1500))
          ($_ (core-boot:set (nq:path $world "status") "200"))
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($ready (core-boot:wait-checkpoint $owner $checkpoint 250 (if (== $mode "iter") "SUCCESS" "shell")))
          ($_ (proc:cancel $owner)) ($ended (proc:await $owner)) ($_ (proc:close $owner))
          ($_ (fs:write (nq:path $world "mode-first.log") (repr $ended)))
          ($_ (nt:require real-mode-checkpoint $ready))
          ($before (sf:native-object $checkpoint))
          ($_ (nt:require actual-tool-result
            (nt:contains (sf:get $before "payload" "") (if (== $mode "iter") "SUCCESS" "shell"))))
          ($_ (nt:require mode-physical-effect-once (== (fs:read-text $effect-path) "X")))
          ($_ (if (== $mode "iter")
                (let $saved (sf:get $before "payload" "")
                  (let* (($_ (nt:require tool-after-failures-ran-once (== (fs:read-text $after-path) "B")))
                         ($_ (nt:require returned-failure-is-output (nt:contains $saved "Tool execution failed: ")))
                         ($_ (nt:require dead-worker-is-uncertain (nt:contains $saved "Tool outcome uncertain: ")))
                         ($_ (nt:require ordinary-value-is-output (nt:contains $saved "(MemoryUnavailable"))))
                    (nt:require ordinary-value-is-no-failure (not (nt:contains $saved "failed: (MemoryUnavailable"))))) True))
          ($_ (if (== $mode "omega")
                (nt:require omega-file-call-effect-once (== (fs:read-text $counter) "xx")) True))
          ($_ (nt:require no-adapter-shaped-tool-name (not (nt:contains (sf:get $before "payload" "") "Unknown tool"))))
          ($_ (nt:require real-mode-provider-submitted (> (nq:posts $world) 0)))
          ($_ (nt:require mode-uses-agent-model-selection
                (nt:contains (fs:read-text (nq:path $world "request-1.body")) "fixture-selected-model")))
          ($_ (nt:require operator-input-reaches-provider
                (nt:contains (fs:read-text (nq:path $world "request-1.body")) "boot-test operator message")))
          ($_ (nt:require input-acknowledged-after-checkpoint (fs:exists (path:join $cwp-root "delivery-acked"))))
          ($_ (nt:require delivered-receipt-acknowledged (fs:exists (path:join $cwp-root "second-acked"))))
          ($_ (nt:require no-startup-banner
                (not (nt:contains (repr (core-boot:outbox-texts (path:join $shared "channel-outbox"))) "native infrastructure"))))
          ($_ (nt:require delivered-receipt-not-presented
                (not (nt:contains (sf:get $before "payload" "") "channel receipt"))))
          ($_ (nt:require provider-failure-retried (> (nq:posts $world) 1)))
          ($_ (nt:require mode-sees-agent-history
                (nt:contains (fs:read-text (nq:path $world "request-1.body")) "framework-era marker message")))
          ($_ (if (== $mode "iter")
                (let* (($_ (nt:require failed-history-write-records-nothing
                             (not (nt:contains (fs:read-text $agent-history) "boot-test operator message"))))
                       ($_ (nt:require failed-history-write-stays-pending (== (core-boot:intent-status $runtime) "pending"))))
                  (core-boot:chmod $agent-history "0644"))
                (let* (($_ (nt:require mode-turn-joins-agent-history
                             (nt:contains (fs:read-text $agent-history) "boot-test operator message")))
                       ($_ (nt:require older-append-completed-before-omega-record
                             (core-boot:older-in-order $agent-history $first-size))))
                  (core-boot:omega-results $agent-history (sf:get $before "payload" "")))))
          ($_ (if (== $mode "iter")
                (nt:require iter-has-shared-memory-tools
                  (nt:contains (fs:read-text (nq:path $world "request-1.body")) "\"remember\"")) True))
          ($_ (nt:require mode-keeps-agent-identity
                (nt:contains (fs:read-text (nq:path $world "request-1.body")) "Boot-test agent identity")))
          ($_ (if (== $mode "iter")
                (nt:require iter-prompt-names-its-channel
                  (nt:contains (fs:read-text (path:join $runtime "prompt.txt")) "Keep your own identity")) True))
          ($_ (nt:require checkpoint-has-identity (!= (sf:get $before "sha256" "") "")))
          ($_ (fs:remove $recycle))
          ($_ (fs:write (nq:path $world "response.body") (core-boot:reply ((core-boot:call "t2" "nop" "{}")))))
          ($first-posts (nq:posts $world))
          ($again (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (proc:wait $again 1500))
          ; Restarted with the history writable, Iter writes what it owed, once.
          ($_ (if (== $mode "iter")
                ; The step's results are the last of its records: wait for them.
                (let* (($_ (core-boot:wait-recorded $again $agent-history "RESULTS:" $runtime 300))
                       ($history (fs:read-text $agent-history))
                       ($_ (nt:require owed-turn-recorded-once (== (core-boot:count $history "boot-test operator message") 1)))
                       ($_ (nt:require iter-results-recorded (nt:contains $history "RESULTS:"))))
                  (nt:require own-record-settled (== (core-boot:intent-status $runtime) "settled")))
                True))
          ($_ (nt:require catch-up-not-mirrored (not (nt:contains (fs:read-text $agent-history) "[AGENT HISTORY:"))))
          ($_ (core-boot:wait-settled $again $checkpoint 100))
          ; The restart's first request carries the step's results.
          ($_ (if (== $mode "iter")
                (let* (($_ (core-boot:wait-posts $world $again (+ $first-posts 1) 600))
                       ($next (fs:read-text (nq:path $world (format-args "request-{}.body" ((+ $first-posts 1))))))
                       ($_ (nt:require next-request-sees-failure (nt:contains $next "Tool execution failed: ")))
                       ($_ (nt:require next-request-sees-uncertain (nt:contains $next "Tool outcome uncertain: "))))
                  (nt:require failed-step-not-reissued (== (fs:read-text $after-path) "B"))) True))
          ($_ (nt:require restart-keeps-settled-checkpoint
                (== (sf:get (sf:native-object $checkpoint) "status" "") "settled")))
          ($_ (nt:require restart-does-not-replay-mode-effect (== (fs:read-text $effect-path) "X")))
          ($_ (if (== $mode "iter")
                (nt:require restart-adds-no-second-catch-up
                  (== (size-atom (str:split "[AGENT HISTORY:" (fs:read-text (path:join $runtime "experience.json")))) 2)) True))
          ; /mode godelclaw: the running mode ends at its next boundary.
          ($_ (fs:write $mode-path (json:stringify-python (JsonObject ((JsonPair "mode" "godelclaw"))))))
          ($_ (nt:require mode-exits-for-selected-switch (core-boot:wait-exit $again 150)))
          ($_ (nt:require switch-keeps-settled-checkpoint
                (== (sf:get (sf:native-object $checkpoint) "status" "") "settled")))
          ($_ (proc:cancel $again)) ($restarted (proc:await $again)) ($_ (proc:close $again))
          ($_ (fs:write (nq:path $world "mode-restart.log") (repr $restarted)))
          ($_ (if (== $mode "iter")
                (core-boot:iter-boundaries $command $root $environment $runtime $agent-history $mode-path $first-size $effect-path)
                True))
          ($_ (if (== $mode "iter") (core-boot:iter-backlog $command $root $environment $world $runtime $agent-history $mode-path) True))
          ($_ (if (== $mode "iter") (core-boot:iter-equal-requeue $command $root $environment $runtime $agent-history $mode-path) True))
          ($_ (if (== $mode "iter") (core-boot:iter-unreadable-intent $command $root $environment $runtime $agent-history $mode-path) True))
          ($_ (if (== $mode "iter") (core-boot:iter-controls $root $world $command $environment $mode-path $recycle) True))
          ($_ (proc:cancel $cwp)) ($_ (proc:await $cwp)) ($_ (proc:close $cwp))
          ($_ (nq:close $world))
          ($_ (nt:require ($mode no-leaked-processes) (nt:no-leaked-processes? $directory 20))))
     (let $_ (println! (NATIVE_MODE_BOOT_OK $mode)) (ModeBootDoneV1 $mode))))
; Every selected mode runs to its end: a case that leaves no answer, as a
; condition that is no Boolean does, fails the suite rather than skip the rest.
(= (core-boot:run-modes $modes)
   (if (== $modes ()) True
     (let $done (collapse (core-boot:mode (car-atom $modes)))
       (if (== $done ((ModeBootDoneV1 (car-atom $modes)))) (core-boot:run-modes (cdr-atom $modes))
         (let $_ (println! (NATIVE_MODE_BOOT_INCOMPLETE (car-atom $modes) $done)) (system:exit 1))))))
!(let $selected (system:getenv-or-default "NATIVE_MODE_TEST" "all")
   (let $_ (core-boot:run-modes (if (== $selected "all") ("iter" "omega") ($selected)))
     (println! NATIVE_MODE_BOOTS_OK)))

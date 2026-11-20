
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
(= (core-boot:chmod $path $mode)
   (proc:run ("/bin/chmod" $mode $path) (system:cwd) (("PATH" "/usr/bin:/bin")) 5000 1024))
(= (core-boot:count $text $needle) (- (size-atom (str:split $needle $text)) 1))
(= (core-boot:intent-path $runtime) (path:join $runtime "agent-history-intent.json"))
(= (core-boot:intent $runtime) (sf:native-object (core-boot:intent-path $runtime)))
(= (core-boot:intent-status $runtime) (sf:get (core-boot:intent $runtime) "status" "none"))
; Until the history holds $needle and the mode's own record is settled.
(= (core-boot:wait-recorded $owner $path $needle $runtime $remaining)
   (if (or (<= $remaining 0)
           (and (nt:contains (fs:read-text $path) $needle) (== (core-boot:intent-status $runtime) "settled"))) True
     (let $_ (proc:wait $owner 100) (core-boot:wait-recorded $owner $path $needle $runtime (- $remaining 1)))))
; Until the mode's checkpoint is settled: a running mode publishes a record
; before each write and settles it after, so a read can land between them.
(= (core-boot:wait-settled $owner $path $remaining)
   (if (or (<= $remaining 0) (== (sf:get (sf:native-object $path) "status" "") "settled")) True
     (let $_ (proc:wait $owner 100) (core-boot:wait-settled $owner $path (- $remaining 1)))))
(= (core-boot:stop $owner) (let* (($_ (proc:cancel $owner)) ($_ (proc:await $owner)) ($_ (proc:close $owner))) True))
; The history cut to its first $bytes bytes, in place: the same file.
(= (core-boot:cut $path $bytes)
   (let* (($kept (str:byte-slice (fs:read-text $path) 0 $bytes))
          ($owner (fs:open-write $path ("truncate") 420))
          ($_ (fs:write-all $owner $kept)) ($_ (fs:close $owner))) True))
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
          ; A death after the checkpoint and before any record of it began.
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($_ (core-boot:put $checkpoint (sf:native-object $checkpoint) ((JsonPair "mirror_frontier" 0))))
          ($_ (fs:remove (core-boot:intent-path $runtime)))
          ($_ (core-boot:cut $history $first-size))
          ($_ (core-boot:relaunch $command $root $environment $runtime $history "boot-test operator message"))
          ($_ (nt:require checkpointed-turn-recorded-once
                (== (core-boot:count (fs:read-text $history) "boot-test operator message") 1)))
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
          ($_ (fs:write (path:join $agent "memory/persistent.metta") "(active-model fixture-selected-model)\n"))
          ($_ (fs:write (path:join $agent "memory/soul.md") "Boot-test agent identity\n"))
          ; A turn the framework loop recorded before the switch.
          ($agent-history (path:join $agent "memory/history.metta"))
          ($_ (fs:write $agent-history
                "(\"2026-11-18 20:00:00\" \"\n\" \"HUMAN_MESSAGE: \" \"framework-era marker message\" \"\n\" ((send \"framework-era reply\")) \"\n\")\n"))
          ($first-size (str:byte-length (fs:read-text $agent-history)))
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
          ($function (JsonObject ((JsonPair "name" "shell") (JsonPair "arguments" $arguments))))
          ($call (JsonObject ((JsonPair "id" "t1") (JsonPair "type" "function") (JsonPair "function" $function))))
          ; Omega's file tools share names with the framework's; each call must run once.
          ($counter (nq:path $world "append-counter"))
          ($append-arguments (json:stringify-python (JsonObject ((JsonPair "filename" $counter) (JsonPair "content" "x")))))
          ($append (JsonObject ((JsonPair "name" "append-file") (JsonPair "arguments" $append-arguments))))
          ($calls (if (== $mode "omega")
                    ($call (JsonObject ((JsonPair "id" "t2") (JsonPair "type" "function") (JsonPair "function" $append)))
                           (JsonObject ((JsonPair "id" "t3") (JsonPair "type" "function") (JsonPair "function" $append))))
                    ($call)))
          ($message (JsonObject ((JsonPair "role" "assistant") (JsonPair "tool_calls" (JsonArray $calls)))))
          ($choice (JsonObject ((JsonPair "message" $message) (JsonPair "finish_reason" "tool_calls"))))
          ($response (JsonObject ((JsonPair "id" "fixture") (JsonPair "choices" (JsonArray ($choice)))
                     (JsonPair "usage" (JsonObject ((JsonPair "prompt_tokens" 10) (JsonPair "completion_tokens" 2)))))))
          ($_ (fs:write (nq:path $world "response.body") (json:stringify-python $response)))
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
          ($_ (fs:write (nq:path $world "status") "503"))
          ($owner (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (core-boot:wait-posts $world $owner 1 1500))
          ($_ (fs:write (nq:path $world "status") "200"))
          ($checkpoint (path:join $runtime "native-checkpoint-v1.json"))
          ($ready (core-boot:wait-checkpoint $owner $checkpoint 250 (if (== $mode "iter") "SUCCESS" "shell")))
          ($_ (proc:cancel $owner)) ($ended (proc:await $owner)) ($_ (proc:close $owner))
          ($_ (fs:write (nq:path $world "mode-first.log") (repr $ended)))
          ($_ (nt:require real-mode-checkpoint $ready))
          ($before (sf:native-object $checkpoint))
          ($_ (nt:require actual-tool-result
            (nt:contains (sf:get $before "payload" "") (if (== $mode "iter") "SUCCESS" "shell"))))
          ($_ (nt:require mode-physical-effect-once (== (fs:read-text $effect-path) "X")))
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
                (nt:require mode-turn-joins-agent-history
                  (nt:contains (fs:read-text $agent-history) "boot-test operator message"))))
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
          ($_ (fs:write (nq:path $world "response.body")
             "{\"id\":\"fixture\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"tool_calls\":[{\"id\":\"t2\",\"type\":\"function\",\"function\":{\"name\":\"nop\",\"arguments\":\"{}\"}}]},\"finish_reason\":\"tool_calls\"}]}"))
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
          ($_ (proc:cancel $cwp)) ($_ (proc:await $cwp)) ($_ (proc:close $cwp))
          ($_ (nq:close $world)))
     (println! (NATIVE_MODE_BOOT_OK $mode))))
!(let $selected (system:getenv-or-default "NATIVE_MODE_TEST" "all")
   (if (== $selected "all") (progn (core-boot:mode "iter") (core-boot:mode "omega")) (core-boot:mode $selected)))
!(println! NATIVE_MODE_BOOTS_OK)

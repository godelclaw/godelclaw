
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
(= (core-boot:owed $runtime)
   (sf:get (sf:native-object (path:join $runtime "agent-history-cursor.json")) "owed" 0))
; Until the history holds $needle and the mode's cursor owes nothing.
(= (core-boot:wait-recorded $owner $path $needle $runtime $remaining)
   (if (or (<= $remaining 0)
           (and (nt:contains (fs:read-text $path) $needle) (== (core-boot:owed $runtime) 0))) True
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
          ($_ (fs:write (nq:path $world "status") "400"))
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
          ($_ (fs:write (nq:path $world "status") "200"))
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
                       ($_ (nt:require failed-history-write-is-owed (> (core-boot:owed $runtime) 0))))
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
          ($_ (fs:write (nq:path $world "response.body") (core-boot:reply ((core-boot:call "t2" "nop" "{}")))))
          ($first-posts (nq:posts $world))
          ($again (proc:spawn $command ((cwd $root) (env $environment) (max-bytes 1048576))))
          ($_ (proc:wait $again 1500))
          ; Restarted with the history writable, Iter writes what it owed, once.
          ($_ (if (== $mode "iter")
                (let* (($_ (core-boot:wait-recorded $again $agent-history "boot-test operator message" $runtime 300))
                       ($history (fs:read-text $agent-history))
                       ($_ (nt:require owed-turn-recorded-once (== (core-boot:count $history "boot-test operator message") 1)))
                       ($_ (nt:require iter-results-recorded (nt:contains $history "RESULTS:"))))
                  (nt:require owed-count-cleared (== (core-boot:owed $runtime) 0)))
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
          ($_ (if (== $mode "iter") (core-boot:iter-controls $root $world $command $environment $mode-path $recycle) True))
          ($_ (proc:cancel $cwp)) ($_ (proc:await $cwp)) ($_ (proc:close $cwp))
          ($_ (nq:close $world)))
     (println! (NATIVE_MODE_BOOT_OK $mode))))
!(let $selected (system:getenv-or-default "NATIVE_MODE_TEST" "all")
   (if (== $selected "all") (progn (core-boot:mode "iter") (core-boot:mode "omega")) (core-boot:mode $selected)))
!(println! NATIVE_MODE_BOOTS_OK)

; Native full-path shadow replay driver for `run.sh --shadow-eval`.
; Runs recorded model responses through each release's real cetta_bootstrap,
; parser (addition-balance/envelope/read/command-sequence), and effect broker
; (addition-effect-broker -> run-command-batch-once) with only the effect
; provider replaced by a disposable `tmux -L` world, plus real wire-request
; preparation (tokens and cache layout) across provider protocols.
; Runs identically on a Python-enabled baseline (e.g. 26fdb55a on f6339988)
; and a Python-free native candidate inside shadow_eval.sh's bwrap sandbox.
!(import! &self /app/src/state_file_cetta.metta)
!(import! &self (library proc))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library list))
!(import! &self (library system))
!(import! &self (library path))

(= (sr:fail $where $detail)
   (progn (println! (SHADOW_REPLAY_INCOMPLETE $where $detail)) (system:exit 2)))

(= (sr:default-launch)
   "sh -c 'printf \"TRUST_PROMPT>\"; IFS= read -r _; printf \"\\nSERVER_SELECTION>\\n\"; IFS= read -r _; printf \"CLAUDE_IDLE\\n\"'")

(= (sr:contains? $hay $needle)
   (case (str:find $hay $needle) (((TextNotFound $_) False) ($at True))))

(= (sr:split-lines $text)
   (let $clean (if (str:ends-with $text "\n")
                 (str:byte-slice $text 0 (- (str:byte-length $text) 1)) $text)
     (if (== $clean "") () (str:split "\n" $clean))))

(= (sr:nonempty-lines $lines)
   (if (== $lines ()) ()
     (let $h (car-atom $lines)
       (if (== $h "") (sr:nonempty-lines (cdr-atom $lines))
         (cons-atom $h (sr:nonempty-lines (cdr-atom $lines)))))))

(= (sr:nth $items $idx)
   (if (== $items ()) ""
     (if (== $idx 0) (car-atom $items) (sr:nth (cdr-atom $items) (- $idx 1)))))

(= (sr:run $argv $cwd $env $timeout)
   (proc:run $argv $cwd $env $timeout 4194304))

(= (sr:tmux $sock $args)
   (sr:run (list:append ("env" "-u" "TMUX" "-u" "TMUX_PANE" "tmux" "-L" $sock "-f" "/dev/null") $args)
           "/work" inherit 10000))

(= (sr:tmux-ok $sock $args $label)
   (case (sr:tmux $sock $args)
     (((proc:exited 0 $out $_) $out)
      ($bad (sr:fail $label $bad)))))

(= (sr:window-names-from-lines $lines)
   (if (== $lines ()) ()
     (let* (($line (car-atom $lines))
            ($parts (str:split ":" $line))
            ($name (sr:nth $parts (- (size-atom $parts) 1))))
       (cons-atom $name (sr:window-names-from-lines (cdr-atom $lines))))))

(= (sr:tmux-window-names $sock)
   (case (sr:tmux $sock ("list-windows" "-a" "-F" "#{session_name}:#{window_index}:#{window_name}"))
     (((proc:exited 0 $out $_) (sr:window-names-from-lines (sr:nonempty-lines (sr:split-lines $out))))
      ($other ()))))

(= (sr:stage-app)
   (let* (($cp (sr:run ("cp" "-r" "/app" "/work/app") "/work" inherit 15000))
          ($_ (case $cp (((proc:exited 0 $_ $_) True) ($bad (sr:fail copy-app $bad)))))
          ($_ (fs:make-directories "/work/app/config" 448))
          ($_ (fs:write "/work/app/config/channel.metta"
                ";; Generated from config/local.toml [channel] kind. Do not edit.\n(= (configureChannel) (configure commchannel telegram))\n"))
          ($_ (if (fs:is-file "/work/app/src/instance_hooks.metta") True
                (fs:write "/work/app/src/instance_hooks.metta"
                  ";; Optional instance-owned additions. Shared releases preserve this file.\n(= (instance-after-broker $iteration $results) True)\n"))))
     "/work/app"))

(= (sr:init-world $dir $sock $auto-stop $launch)
   (let* (($_ (fs:make-directories $dir 448))
          ($sp (path:join $dir "state.json"))
          ($pp (path:join $dir "task-phase.json"))
          ($lp (path:join $dir "lifecycle.json"))
          ($_ (sr:tmux-ok $sock ("new-session" "-d" "-s" "shadow" "-n" "code-log" "bash" "--noprofile" "--norc") tmux-start))
          ($_ (sr:tmux-ok $sock ("new-window" "-d" "-a" "-t" "shadow:code-log" "-n" "pleatta" "bash" "--noprofile" "--norc") tmux-pleatta))
          ($state (JsonObject
            ((JsonPair "version" 1)
             (JsonPair "socket_name" $sock)
             (JsonPair "room_name" "claude-room")
             (JsonPair "launch_command" $launch)
             (JsonPair "phase" "need-create")
             (JsonPair "serial" 0)
             (JsonPair "operation_serial" 0)
             (JsonPair "receipts" (JsonObject ()))
             (JsonPair "pending_effects" (JsonObject ()))
             (JsonPair "trace" (JsonArray ()))
             (JsonPair "effects" 0)
             (JsonPair "finished" False)
             (JsonPair "operator_epoch" 0)
             (JsonPair "initial_operator_epoch" 0)
             (JsonPair "auto_stop_after_first_effect" $auto-stop)
             (JsonPair "prompt_timeout" 5.0))))
          ($phases (JsonObject
            ((JsonPair "schema" 1)
             (JsonPair "task_id" "tmux-room-qualification")
             (JsonPair "evidence_revision" "turn:0:effects:0")
             (JsonPair "revision" 0)
             (JsonPair "phases" (JsonObject
               ((JsonPair "need-create" (JsonObject ((JsonPair "status" "active") (JsonPair "evidence" JsonNull))))
                (JsonPair "need-launch" (JsonObject ((JsonPair "status" "pending") (JsonPair "evidence" JsonNull))))
                (JsonPair "need-trust" (JsonObject ((JsonPair "status" "pending") (JsonPair "evidence" JsonNull))))
                (JsonPair "need-server" (JsonObject ((JsonPair "status" "pending") (JsonPair "evidence" JsonNull))))
                (JsonPair "goal-satisfied" (JsonObject ((JsonPair "status" "pending") (JsonPair "evidence" JsonNull))))))))))
          ($_ (fs:write $sp (sf:python-json $state)))
          ($_ (fs:write $pp (sf:python-json $phases)))
          ($_ (fs:write $lp "{\"schema\":1,\"state\":\"running\"}\n")))
     ($sp $pp $lp)))

(= (sr:child-env $app-dir $dir $sp $pp $lp)
   (let* (($base
            (("METTACLAW_RUNTIME_ROOT" $app-dir)
             ("METTACLAW_EFFECT_BACKEND" "tmux-shadow")
             ("METTACLAW_EFFECT_BACKEND_STATE" $sp)
             ("METTACLAW_LIFECYCLE_PATH" $lp)
             ("METTACLAW_TASK_PHASE_PATH" $pp)
             ("METTACLAW_STIMULUS_FRONTIER_PATH" (path:join $dir "stimulus-frontier"))
             ("METTACLAW_EFFECT_RECEIPT_PATH" (path:join $dir "effect-receipts.jsonl"))
             ("METTACLAW_PINS_PATH" (path:join $dir "pins.txt"))
             ("METTACLAW_GOALS_PATH" (path:join $dir "goals.metta"))
             ("METTACLAW_MODEL_STATE_PATH" (path:join $dir "model-state.metta"))
             ("METTACLAW_COGNITIVE_HEALTH_PATH" (path:join $dir "cognitive-health.json"))
             ("METTACLAW_DEPLOYMENT_STATE_PATH" (path:join $dir "deployment.json"))
             ("METTACLAW_WORKING_SET_PATH" (path:join $dir "working-set.json"))
             ("METTACLAW_HISTORY_PATH" (path:join $dir "history.metta"))
             ("METTACLAW_TELEGRAM_LOG_PATH" (path:join $dir "telegram.jsonl"))
             ("METTACLAW_TELEGRAM_OFFSET_PATH" (path:join $dir "telegram-offset"))
             ("METTACLAW_ENGINE_STATE_PATH" (path:join $dir "engine"))
             ("METTACLAW_RECYCLE_REQUEST_PATH" (path:join $dir "recycle.requested"))
             ("METTACLAW_ANTHROPIC_USAGE_PATH" (path:join $dir "anthropic-usage.json"))
             ("METTACLAW_LLM_COMPLETION_PATH" (path:join $dir "llm-completion.json"))
             ("METTACLAW_PROVIDER_OBSERVATION_PATH" (path:join $dir "providers.json"))
             ("METTACLAW_OBSERVED_STRATEGY" "0")
             ("METTACLAW_LLM_STREAM" "0")
             ("METTACLAW_MODEL_COMMENTARY" "1")
             ("OPENAI_API_KEY" "fixture-secret")
             ("OPENAI_BASE_URL" "http://127.0.0.1:1/v1")
             ("ANTHROPIC_API_KEY" "fixture-secret")
             ("ANTHROPIC_BASE_URL" "http://127.0.0.1:1/v1")
             ("SYNTHETIC_API_KEY" "fixture-secret")
             ("SYNTHETIC_BASE_URL" "http://127.0.0.1:1/v1")
             ("KIMI_API_KEY" "fixture-secret")
             ("KIMI_BASE_URL" "http://127.0.0.1:1/v1")))
          ($py-on (== (system:getenv-or-default "SHADOW_PYTHON_ENABLED" "0") "1"))
          ($extra (if $py-on
                    (("PYTHONPATH" (str:join ":" ((path:join $app-dir "src")
                                                  (path:join $app-dir "modes")
                                                  (path:join $app-dir "modes/iter")
                                                  (path:join $app-dir "channels")))))
                    ())))
     (inherit (list:append $base $extra))))

(= (sr:cetta-argv $app-dir $probe-path)
   (if (== (system:getenv-or-default "SHADOW_PYTHON_ENABLED" "0") "1")
     ("/sdk/cetta" "--lang" "petta" "--import-mode" "ancestor-walk" "--quiet"
      (path:join $app-dir "cetta_bootstrap.metta") $probe-path)
     ("/sdk/cetta" "--lang" "petta" "--profile" "extended" "--import-mode" "ancestor-walk" "--quiet"
      (path:join $app-dir "cetta_bootstrap.metta") $probe-path)))

(= (sr:run-probe $app-dir $dir $sp $pp $lp $probe-path $label)
   (let* (($env (sr:child-env $app-dir $dir $sp $pp $lp))
          ($res (sr:run (sr:cetta-argv $app-dir $probe-path) $app-dir $env 45000)))
     (case $res
       (((proc:exited 0 $out $_) $out)
        ($bad (sr:fail $label $bad))))))

(= (sr:probe-preamble)
   (str:join "\n"
     ("(= (rp:room-in $pairs $room)"
      "   (if (== $pairs ()) \"\""
      "     (let (JsonPair $k $v) (car-atom $pairs)"
      "       (if (== (sf:get $v \"window_name\" \"\") $room) $k (rp:room-in (cdr-atom $pairs) $room)))))"
      "(= (rp:room $sp)"
      "   (case (sf:get (sf:native-object $sp) \"receipts\" (JsonObject ()))"
      "     (((JsonObject $pairs) (rp:room-in $pairs \"claude-room\")) ($o \"\"))))"
      "(= (rp:first $sp)"
      "   (case (sf:get (sf:native-object $sp) \"receipts\" (JsonObject ()))"
      "     (((JsonObject $pairs) (if (== $pairs ()) \"\" (let (JsonPair $k $_) (car-atom $pairs) $k))) ($o \"\"))))"
      "(= (rp:disp $rec)"
      "   (if (and (== (get-metatype $rec) Expression) (> (size-atom $rec) 0)) (repr (car-atom $rec)) \"UNKNOWN\"))"
      "(= (rp:disps $recs)"
      "   (if (== $recs ()) () (cons-atom (rp:disp (car-atom $recs)) (rp:disps (cdr-atom $recs)))))"
      "(= (rp:exec $turn $raw)"
      "   (let* (($balanced (addition-balance-response $raw))"
      "          ($env (addition-envelope-response $balanced))"
      "          ($parsed (addition-read-response $env))"
      "          ($sexpr (addition-command-sequence $parsed))"
      "          ($records (addition-effect-broker $turn 5 (quote $sexpr))))"
      "     (JsonObject ((JsonPair \"turn\" $turn)"
      "                  (JsonPair \"parsed_count\" (size-atom $sexpr))"
      "                  (JsonPair \"records_count\" (size-atom $records))"
      "                  (JsonPair \"dispositions\" (JsonArray (rp:disps $records)))))))")))

(= (sr:normalize-result $cmd $result)
   (if (or (== $cmd "(tmux-windows)") (sr:contains? $result "\nSCREEN:\n"))
     (let* (($lines (sr:split-lines $result))
            ($headers (filter-atom $lines $l (sr:contains? $l " target="))))
       (str:join " | " $headers))
     $result))

(= (sr:normalize-trace-items $items)
   (if (== $items ()) ()
     (let* (($it (car-atom $items))
            ($cmd (sf:get $it "command" ""))
            ($eff (sf:get $it "effect" False))
            ($res (sr:normalize-result $cmd (sf:get $it "result" "")))
            ($row (JsonObject ((JsonPair "command" $cmd)
                               (JsonPair "effect" $eff)
                               (JsonPair "result" $res)))))
       (cons-atom $row (sr:normalize-trace-items (cdr-atom $items))))))

(= (sr:phase-statuses $pp)
   (let $phases (sf:get (sf:native-object $pp) "phases" (JsonObject ()))
     (JsonObject
       ((JsonPair "need-create" (sf:get (sf:get $phases "need-create" (JsonObject ())) "status" ""))
        (JsonPair "need-launch" (sf:get (sf:get $phases "need-launch" (JsonObject ())) "status" ""))
        (JsonPair "need-trust" (sf:get (sf:get $phases "need-trust" (JsonObject ())) "status" ""))
        (JsonPair "need-server" (sf:get (sf:get $phases "need-server" (JsonObject ())) "status" ""))
        (JsonPair "goal-satisfied" (sf:get (sf:get $phases "goal-satisfied" (JsonObject ())) "status" ""))))))

(= (sr:scenario-effect $name $sp $pp $sock $extra-pairs)
   (let* (($st (sf:native-object $sp))
          ($trace (case (sf:get $st "trace" (JsonArray ())) (((JsonArray $items) $items) ($o ()))))
          ($wins (sr:tmux-window-names $sock))
          ($base ((JsonPair "scenario" $name)
                  (JsonPair "phase" (sf:get $st "phase" ""))
                  (JsonPair "effects" (sf:get $st "effects" 0))
                  (JsonPair "finished" (sf:get $st "finished" False))
                  (JsonPair "last_result" (sr:normalize-result "" (sf:get $st "last_result" "")))
                  (JsonPair "window_names" (JsonArray $wins))
                  (JsonPair "trace" (JsonArray (sr:normalize-trace-items $trace)))
                  (JsonPair "task_phases" (sr:phase-statuses $pp)))))
     (JsonObject (list:append $base $extra-pairs))))

; Scenario 1: Complete 9-turn disposable tmux episode through real parse+dispatch.
(= (sr:run-complete $app-dir $launch)
   (let* (($dir "/work/shadow/complete")
          ($sock (str:concat "sr-c-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $lp) (sr:init-world $dir $sock False $launch))
          ($out-path (path:join $dir "turns.json"))
          ($probe (path:join $dir "probe.metta"))
          ($src (str:join "\n"
            ((sr:probe-preamble)
             (str:join "" ("!(let* (($sp " (repr $sp) ") ($launch " (repr $launch) ")"))
             "        ($t1 (rp:exec 1 \"((tmux-windows))\"))"
             "        ($t2 (rp:exec 2 \"((tmux-new-shell-after \\\"r1\\\" \\\"claude-room\\\"))\"))"
             "        ($t3 (rp:exec 3 \"((tmux-windows))\"))"
             "        ($r1 (rp:room $sp))"
             "        ($t4 (rp:exec 4 (str:join \"\" (\"((tmux-send-observed \" (repr $r1) \" \" (repr $launch) \"))\"))))"
             "        ($t5 (rp:exec 5 \"((tmux-windows))\"))"
             "        ($r2 (rp:room $sp))"
             "        ($t6 (rp:exec 6 (str:join \"\" (\"((tmux-send-observed \" (repr $r2) \" \\\"yes\\\"))\"))))"
             "        ($t7 (rp:exec 7 \"((tmux-windows))\"))"
             "        ($r3 (rp:room $sp))"
             "        ($t8 (rp:exec 8 (str:join \"\" (\"((tmux-send-observed \" (repr $r3) \" \\\"lean-lsp\\\"))\"))))"
             "        ($t9 (rp:exec 9 \"((shadow-finish \\\"done\\\"))\"))"
             "        ($doc (JsonObject ((JsonPair \"scenario\" \"complete-episode\")"
             "                           (JsonPair \"turns\" (JsonArray ($t1 $t2 $t3 $t4 $t5 $t6 $t7 $t8 $t9)))))))"
             (str:join "" ("   (fs:write " (repr $out-path) " (sf:python-json $doc)))"))
             "")))
          ($_ (fs:write $probe $src))
          ($_ (sr:run-probe $app-dir $dir $sp $pp $lp $probe complete-episode))
          ($out-doc (sf:native-object $out-path))
          ($eff-doc (sr:scenario-effect "complete-episode" $sp $pp $sock ()))
          ($_ (sr:tmux $sock ("kill-server")))
          ($ok (and (== (sf:get $eff-doc "phase" "") "goal-satisfied")
                    (and (== (sf:get $eff-doc "effects" 0) 4)
                         (and (== (sf:get $eff-doc "finished" False) True)
                              (== (sf:get $eff-doc "last_result" "") "TASK_VERIFIED"))))))
     ($ok $out-doc $eff-doc)))

; Scenario 2: Receipt-confusion historical replay (run_godel_receipt_replay.py).
(= (sr:run-receipt-confusion $app-dir $launch $resp)
   (let* (($dir "/work/shadow/receipt-confusion")
          ($sock (str:concat "sr-r-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $lp) (sr:init-world $dir $sock False $launch))
          ($out-path (path:join $dir "turns.json"))
          ($probe (path:join $dir "probe.metta"))
          ($src (str:join "\n"
            ((sr:probe-preamble)
             (str:join "" ("!(let* (($t1 (rp:exec 41 \"((shadow-finish \\\"all prior operations completed\\\"))\"))"))
             (str:join "" ("        ($t2 (rp:exec 42 " (repr $resp) "))"))
             "        ($doc (JsonObject ((JsonPair \"scenario\" \"receipt-confusion\")"
             "                           (JsonPair \"turns\" (JsonArray ($t1 $t2)))))))"
             (str:join "" ("   (fs:write " (repr $out-path) " (sf:python-json $doc)))"))
             "")))
          ($_ (fs:write $probe $src))
          ($_ (sr:run-probe $app-dir $dir $sp $pp $lp $probe receipt-confusion))
          ($out-doc (sf:native-object $out-path))
          ($eff-doc (sr:scenario-effect "receipt-confusion" $sp $pp $sock ()))
          ($_ (sr:tmux $sock ("kill-server")))
          ($ok (and (== (sf:get $eff-doc "effects" -1) 0)
                    (== (sf:get $eff-doc "finished" True) False))))
     ($ok $out-doc $eff-doc)))

; Scenario 3: Fail-closed safety denials (shell, pin, delete-my-recent, send).
(= (sr:run-safety $app-dir $launch)
   (let* (($dir "/work/shadow/safety")
          ($sock (str:concat "sr-s-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $lp) (sr:init-world $dir $sock False $launch))
          ($marker (path:join $dir "must-not-exist"))
          ($pins (path:join $dir "pins.txt"))
          ($out-path (path:join $dir "turns.json"))
          ($probe (path:join $dir "probe.metta"))
          ($shell-batch (str:join "" ("((shell " (repr (str:concat "touch " $marker)) ") (pin \"must remain a proposal\"))")))
          ($src (str:join "\n"
            ((sr:probe-preamble)
             (str:join "" ("!(let* (($t1 (rp:exec 1 " (repr $shell-batch) "))"))
             "        ($t2 (rp:exec 2 \"((delete-my-recent \\\"research-group\\\" 3) (send \\\"Done — deleted the messages\\\"))\"))"
             "        ($doc (JsonObject ((JsonPair \"scenario\" \"fail-closed-safety\")"
             "                           (JsonPair \"turns\" (JsonArray ($t1 $t2)))))))"
             (str:join "" ("   (fs:write " (repr $out-path) " (sf:python-json $doc)))"))
             "")))
          ($_ (fs:write $probe $src))
          ($_ (sr:run-probe $app-dir $dir $sp $pp $lp $probe fail-closed-safety))
          ($out-doc (sf:native-object $out-path))
          ($absent (and (not (fs:exists $marker)) (not (fs:exists $pins))))
          ($eff-doc (sr:scenario-effect "fail-closed-safety" $sp $pp $sock
                      ((JsonPair "denied_files_absent" $absent))))
          ($_ (sr:tmux $sock ("kill-server")))
          ($ok (and $absent (== (sf:get $eff-doc "effects" -1) 0))))
     ($ok $out-doc $eff-doc)))

; Scenario 4: Single-use receipt reservation and operator-stop stimulus frontier.
(= (sr:run-single-use-stop $app-dir $launch)
   (let* (($dir "/work/shadow/single-use-stop")
          ($sock (str:concat "sr-u-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $lp) (sr:init-world $dir $sock True $launch))
          ($out-path (path:join $dir "turns.json"))
          ($frontier (path:join $dir "stimulus-frontier"))
          ($probe (path:join $dir "probe.metta"))
          ($src (str:join "\n"
            ((sr:probe-preamble)
             (str:join "" ("!(let* (($sp " (repr $sp) ") ($fp " (repr $frontier) ")"))
             "        ($t1 (rp:exec 1 \"((tmux-windows))\"))"
             "        ($r1 (rp:first $sp))"
             "        ($t2 (rp:exec 2 (str:join \"\" (\"((tmux-send-observed \" (repr $r1) \" \\\":\\\") (tmux-send-observed \" (repr $r1) \" \\\":\\\"))\"))))"
             "        ($f2 (if (fs:is-file $fp) (str:trim (fs:read-text $fp)) \"\"))"
             "        ($t3 (rp:exec 3 \"((shadow-finish \\\"stopped\\\"))\"))"
             "        ($doc (JsonObject ((JsonPair \"scenario\" \"single-use-and-stop\")"
             "                           (JsonPair \"frontier_after_effect\" $f2)"
             "                           (JsonPair \"turns\" (JsonArray ($t1 $t2 $t3)))))))"
             (str:join "" ("   (fs:write " (repr $out-path) " (sf:python-json $doc)))"))
             "")))
          ($_ (fs:write $probe $src))
          ($_ (sr:run-probe $app-dir $dir $sp $pp $lp $probe single-use-and-stop))
          ($out-doc (sf:native-object $out-path))
          ($f2 (sf:get $out-doc "frontier_after_effect" ""))
          ($ftext (if (fs:is-file $frontier) (str:trim (fs:read-text $frontier)) ""))
          ($eff-doc (sr:scenario-effect "single-use-and-stop" $sp $pp $sock
                      ((JsonPair "frontier_after_effect" $f2)
                       (JsonPair "frontier_line" $ftext))))
          ($_ (sr:tmux $sock ("kill-server")))
          ($ok (and (== (sf:get $eff-doc "effects" 0) 1)
                    (and (== (sf:get $eff-doc "finished" False) True)
                         (and (== (sf:get $eff-doc "last_result" "") "STOP_OBEYED")
                              (and (== $f2 "1 2 0 1") (== $ftext "1 3 1 1")))))))
     ($ok $out-doc $eff-doc)))

; Scenario 5: Wire-request preparation audit (tokens and cache layout across protocols).
(= (sr:wire-probe-script $out-path $include-chatgpt)
   (str:join "\n"
     ("!(import! &self (library math))"
      "(= (wp:roles $items)"
      "   (if (== $items ()) ()"
      "     (cons-atom (sf:get (car-atom $items) \"role\" \"\") (wp:roles (cdr-atom $items)))))"
      "(= (wp:prefix-before-user $roles)"
      "   (if (== $roles ()) ()"
      "     (let $r (car-atom $roles)"
      "       (if (== $r \"user\") () (cons-atom $r (wp:prefix-before-user (cdr-atom $roles)))))))"
      "(= (wp:has-cache-control? $obj)"
      "   (case (sf:get $obj \"cache_control\" JsonNull) (((JsonObject $_) True) ($o False))))"
      "(= (wp:content-cached? $msg)"
      "   (let $c (sf:get $msg \"content\" JsonNull)"
      "     (case $c"
      "       (((JsonArray $blocks) (wp:any-cached? $blocks))"
      "        ($o (wp:has-cache-control? $msg))))))"
      "(= (wp:any-cached? $blocks)"
      "   (if (== $blocks ()) False"
      "     (if (wp:has-cache-control? (car-atom $blocks)) True (wp:any-cached? (cdr-atom $blocks)))))"
      "(= (wp:breakpoints-in $prefix $items $idx)"
      "   (if (== $items ()) ()"
      "     (let $rest (wp:breakpoints-in $prefix (cdr-atom $items) (+ $idx 1))"
      "       (if (wp:content-cached? (car-atom $items))"
      "         (cons-atom (str:join \"\" ($prefix \"[\" (repr $idx) \"]\")) $rest) $rest))))"
      "(= (wp:array-of $obj $key)"
      "   (case (sf:get $obj $key (JsonArray ())) (((JsonArray $items) $items) ($o ()))))"
      "(= (wp:url-path $url)"
      "   (let $parts (str:split \"/v1\" $url)"
      "     (if (> (size-atom $parts) 1) (str:concat \"/v1\" (car-atom (cdr-atom $parts))) $url)))"
      "(= (wp:summarize $name $prompt $plan)"
      "   (case $plan"
      "     (((PreparedProviderCallV1 $owner (PreparedProviderTransportV1 $_ $_ $proto $url $_ $wire $_ $_ $_ $_ $_))"
      "       (let* (($doc (json:parse $wire))"
      "              ($model (sf:get $doc \"model\" \"\"))"
      "              ($max-tok (sf:get $doc \"max_tokens\" (sf:get $doc \"max_output_tokens\" (sf:get $doc \"max_completion_tokens\" 0))))"
      "              ($body-bytes (str:byte-length $wire))"
      "              ($prompt-bytes (str:byte-length $prompt))"
      "              ($est-tokens (math:ceil (/ $body-bytes 4)))"
      "              ($msgs (if (== $proto \"responses\") (wp:array-of $doc \"input\") (wp:array-of $doc \"messages\")))"
      "              ($sys-blocks (wp:array-of $doc \"system\"))"
      "              ($roles (wp:roles $msgs))"
      "              ($prefix (wp:prefix-before-user $roles))"
      "              ($sys-bp (wp:breakpoints-in \"system\" $sys-blocks 0))"
      "              ($msg-bp (wp:breakpoints-in \"messages\" $msgs 0))"
      "              ($bps (list:append $sys-bp $msg-bp))"
      "              ($pck (sf:get $doc \"prompt_cache_key\" \"\"))"
      "              ($tools (wp:array-of $doc \"tools\"))"
      "              ($stream (sf:get $doc \"stream\" False))"
      "              ($canon (sf:python-json $doc))"
      "              ($layout (JsonObject ((JsonPair \"role_sequence\" (JsonArray $roles))"
      "                                    (JsonPair \"prefix_roles_before_first_user\" (JsonArray $prefix))"
      "                                    (JsonPair \"system_block_count\" (size-atom $sys-blocks))"
      "                                    (JsonPair \"cache_breakpoints\" (JsonArray $bps))"
      "                                    (JsonPair \"prompt_cache_key_present\" (!= $pck \"\"))"
      "                                    (JsonPair \"tools_count\" (size-atom $tools))"
      "                                    (JsonPair \"stream\" $stream)))))"
      "         (JsonObject ((JsonPair \"name\" $name)"
      "                      (JsonPair \"status\" \"prepared\")"
      "                      (JsonPair \"protocol\" $proto)"
      "                      (JsonPair \"model\" $model)"
      "                      (JsonPair \"path\" (wp:url-path $url))"
      "                      (JsonPair \"max_tokens\" $max-tok)"
      "                      (JsonPair \"prompt_bytes\" $prompt-bytes)"
      "                      (JsonPair \"body_bytes\" $body-bytes)"
      "                      (JsonPair \"estimated_tokens\" $est-tokens)"
      "                      (JsonPair \"cache_layout\" $layout)"
      "                      (JsonPair \"wire_sha256\" (str:sha256 $canon))))))"
      "      ($refused (JsonObject ((JsonPair \"name\" $name)"
      "                             (JsonPair \"status\" \"refused\")"
      "                             (JsonPair \"detail\" (repr $refused))))))))"
      "!(let* (($legacy-prompt \"Static system policy._cache_boundary_History chunk one._cache_chunk_History chunk two._cache_boundary_Current user turn: inspect shadow status.\")"
      "        ($role-prompt \"{\\\"kind\\\":\\\"role-context-v1\\\",\\\"cache_points\\\":[0,2],\\\"messages\\\":[{\\\"role\\\":\\\"system\\\",\\\"content\\\":\\\"Static system policy.\\\"},{\\\"role\\\":\\\"developer\\\",\\\"content\\\":\\\"Standing rules.\\\"},{\\\"role\\\":\\\"assistant\\\",\\\"content\\\":\\\"History chunk 1.\\\"},{\\\"role\\\":\\\"assistant\\\",\\\"content\\\":\\\"Request index chunk 1.\\\"},{\\\"role\\\":\\\"user\\\",\\\"content\\\":\\\"Inspect shadow status.\\\"},{\\\"role\\\":\\\"assistant\\\",\\\"content\\\":\\\"((tmux-windows))\\\"},{\\\"role\\\":\\\"user\\\",\\\"content\\\":\\\"Continue.\\\"}]}\")"
      "        ($p1 (wp:summarize \"anthropic-command\" $legacy-prompt (pcall:command-plan \"claude-fixture\" 1024 \"high\" $legacy-prompt)))"
      "        ($p2 (wp:summarize \"responses-command\" $legacy-prompt (pcall:command-plan \"openai:fixture\" 1024 \"medium\" $legacy-prompt)))"
      "        ($p3 (wp:summarize \"synthetic-command\" $legacy-prompt (pcall:command-plan \"syn:large:text\" 1024 \"medium\" $legacy-prompt)))"
      (str:join "" ("        ($inc-cg " (if $include-chatgpt "True" "False") ")"))
      "        ($fake-session (json-parse \"{\\\"api_base\\\":\\\"https://chatgpt.com/backend-api/codex/\\\",\\\"value\\\":{\\\"credential_alias\\\":\\\"fixture_chatgpt\\\"}}\"))"
      "        ($rows (if $inc-cg"
      "                 ($p1 $p2 $p3 (wp:summarize \"chatgpt-role-context\" $role-prompt (chatgpt:command-plan \"chatgpt:gpt-5.4\" 1024 \"medium\" $role-prompt $fake-session)))"
      "                 ($p1 $p2 $p3))))"
      (str:join "" ("   (fs:write " (repr $out-path) " (sf:python-json (JsonObject ((JsonPair \"probes\" (JsonArray $rows)))))))"))
      "")))

(= (sr:all-wire-prepared? $items)
   (if (== $items ()) True
     (if (== (sf:get (car-atom $items) "status" "") "prepared")
       (sr:all-wire-prepared? (cdr-atom $items)) False)))

(= (sr:run-wire-probes $app-dir $include-chatgpt)
   (let* (($dir "/work/shadow/wire")
          ($_ (fs:make-directories $dir 448))
          ($lp (path:join $dir "lifecycle.json"))
          ($_ (fs:write $lp "{\"schema\":1,\"state\":\"running\"}\n"))
          ($out-path (path:join $dir "wire.json"))
          ($probe (path:join $dir "probe.metta"))
          ($_ (fs:write $probe (sr:wire-probe-script $out-path $include-chatgpt)))
          ($env (sr:child-env $app-dir $dir "" "" $lp))
          ; Disable tmux-shadow on the wire probe process (no commands dispatched).
          ($clean-env (inherit (list:append
            (("METTACLAW_EFFECT_BACKEND" "")
             ("METTACLAW_EFFECT_BACKEND_STATE" "")
             ("METTACLAW_RUNTIME_ROOT" $app-dir)
             ("METTACLAW_LIFECYCLE_PATH" $lp)
             ("METTACLAW_MODEL_STATE_PATH" (path:join $dir "model-state.metta"))
             ("METTACLAW_COGNITIVE_HEALTH_PATH" (path:join $dir "cognitive-health.json"))
             ("METTACLAW_ANTHROPIC_USAGE_PATH" (path:join $dir "anthropic-usage.json"))
             ("METTACLAW_LLM_COMPLETION_PATH" (path:join $dir "llm-completion.json"))
             ("METTACLAW_PROVIDER_OBSERVATION_PATH" (path:join $dir "providers.json"))
             ("METTACLAW_OBSERVED_STRATEGY" "0")
             ("METTACLAW_LLM_STREAM" "0")
             ("METTACLAW_MODEL_COMMENTARY" "1")
             ("OPENAI_API_KEY" "fixture-secret") ("OPENAI_BASE_URL" "http://127.0.0.1:1/v1")
             ("ANTHROPIC_API_KEY" "fixture-secret") ("ANTHROPIC_BASE_URL" "http://127.0.0.1:1/v1")
             ("SYNTHETIC_API_KEY" "fixture-secret") ("SYNTHETIC_BASE_URL" "http://127.0.0.1:1/v1")
             ("KIMI_API_KEY" "fixture-secret") ("KIMI_BASE_URL" "http://127.0.0.1:1/v1"))
            (if (== (system:getenv-or-default "SHADOW_PYTHON_ENABLED" "0") "1")
              (("PYTHONPATH" (str:join ":" ((path:join $app-dir "src")
                                            (path:join $app-dir "modes")
                                            (path:join $app-dir "modes/iter")
                                            (path:join $app-dir "channels")))))
              ()))))
          ($res (sr:run (sr:cetta-argv $app-dir $probe) $app-dir $clean-env 45000))
          ($_ (case $res (((proc:exited 0 $_ $_) True) ($bad (sr:fail wire-probes $bad)))))
          ($doc (sf:native-object $out-path))
          ($probes (case (sf:get $doc "probes" (JsonArray ())) (((JsonArray $items) $items) ($o ()))))
          ($ok (and (> (size-atom $probes) 0) (sr:all-wire-prepared? $probes))))
     ($ok $probes)))

(= (sr:obs-strings $outputs $effects $wires)
   ((sf:python-json (JsonArray $outputs))
    (sf:python-json (JsonArray $effects))
    (sf:python-json (JsonArray $wires))))

!(let* (($report-path (system:getenv-or-default "SHADOW_REPORT" ""))
        ($_ (if (!= $report-path "") True (sr:fail missing-report-path "")))
        ($input-path (system:getenv-or-default "SHADOW_INPUT" ""))
        ($cfg (if (and (!= $input-path "") (fs:is-file $input-path)) (sf:native-object $input-path) (JsonObject ())))
        ($launch (sf:get $cfg "launch_command" (sr:default-launch)))
        ($include-chatgpt (== (sf:get $cfg "include_chatgpt" False) True))
        ($receipt-resp (sf:get $cfg "receipt_confusion_response" "((tmux-windows))"))
        ($app-dir (sr:stage-app))
        ($t0 (system:monotonic-ns))
        (($ok1 $o1 $e1) (sr:run-complete $app-dir $launch))
        (($ok2 $o2 $e2) (sr:run-receipt-confusion $app-dir $launch $receipt-resp))
        (($ok3 $o3 $e3) (sr:run-safety $app-dir $launch))
        (($ok4 $o4 $e4) (sr:run-single-use-stop $app-dir $launch))
        ($t1 (system:monotonic-ns))
        (($ok5 $wires) (sr:run-wire-probes $app-dir $include-chatgpt))
        ($t2 (system:monotonic-ns))
        ($outputs ($o1 $o2 $o3 $o4))
        ($effects ($e1 $e2 $e3 $e4))
        ($all-ok (and $ok1 (and $ok2 (and $ok3 (and $ok4 $ok5)))))
        ($total-eff (+ (sf:get $e1 "effects" 0)
                       (+ (sf:get $e2 "effects" 0)
                          (+ (sf:get $e3 "effects" 0) (sf:get $e4 "effects" 0)))))
        ($report (JsonObject
          ((JsonPair "schema" 1)
           (JsonPair "complete" True)
           (JsonPair "checks_passed" $all-ok)
           (JsonPair "observations" (JsonArray (sr:obs-strings $outputs $effects $wires)))
           (JsonPair "outputs" (JsonArray $outputs))
           (JsonPair "effects" (JsonArray $effects))
           (JsonPair "wire_requests" (JsonArray $wires))
           (JsonPair "metrics" (JsonObject
             ((JsonPair "replay_scenarios" 4)
              (JsonPair "total_effects" $total-eff)
              (JsonPair "wire_probe_count" (size-atom $wires))
              (JsonPair "parse_dispatch_ms" (/ (- $t1 $t0) 1000000))
              (JsonPair "wire_prep_ms" (/ (- $t2 $t1) 1000000)))))))))
   (fs:write $report-path (sf:python-json $report)))

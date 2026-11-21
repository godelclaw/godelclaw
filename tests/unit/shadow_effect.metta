; Native fail-closed tmux-shadow effect provider contracts:
; complete disposable tmux workflow, receipt-confusion refusal, fail-closed
; shell/pin denial, single-use receipt reservation, post-send timeout
; non-replayability, and operator-stop stimulus frontier.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/command_dispatch_core.metta)
!(import! &self ../../src/command_dispatch_hooks.metta)
!(import! &self ../../src/api_registry.metta)
!(import! &self ../../src/state_file_cetta.metta)

(= (dispatch:env $name $default) (system:getenv-or-default $name $default))
(= (dispatch:length $text) (str:byte-length $text))
(= (dispatch:compare $a $b) (str:compare $a $b))

(= (se-test:launch-cmd)
   "sh -c 'printf \"TRUST_PROMPT>\"; IFS= read -r _; printf \"\\nSERVER_SELECTION>\\n\"; IFS= read -r _; printf \"CLAUDE_IDLE\\n\"'")

(= (se-test:tmux $sock $args)
   (proc:run (list:append ("env" "-u" "TMUX" "-u" "TMUX_PANE" "tmux" "-L" $sock "-f" "/dev/null") $args)
             (system:getcwd) inherit 10000 65536))

(= (se-test:cleanup $sock)
   (let* (($_ (se-test:tmux $sock ("kill-server")))
          ($_ (proc:run ("sh" "-c" (str:join "" ("rm -f \"/tmp/tmux-$(id -u)/" $sock "\"")))
                        (system:getcwd) inherit 5000 4096)))
     True))

(= (se-test:init-world $dir $sock $auto-stop $timeout $launch)
   (let* (($_ (fs:make-directories $dir 448))
          ($state-path (path:join $dir "state.json"))
          ($phase-path (path:join $dir "task-phase.json"))
          ($frontier-path (path:join $dir "stimulus-frontier"))
          ($life-path (path:join $dir "lifecycle.json"))
          ($_ (nt:exit-ok (se-test:tmux $sock ("new-session" "-d" "-s" "shadow" "-n" "code-log" "bash" "--noprofile" "--norc"))))
          ($_ (nt:exit-ok (se-test:tmux $sock ("new-window" "-d" "-a" "-t" "shadow:code-log" "-n" "pleatta" "bash" "--noprofile" "--norc"))))
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
             (JsonPair "prompt_timeout" $timeout))))
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
          ($_ (fs:write $state-path (sf:python-json $state)))
          ($_ (fs:write $phase-path (sf:python-json $phases)))
          ($_ (fs:write $life-path "{\"schema\":1,\"state\":\"running\"}\n")))
     ($state-path $phase-path $frontier-path $life-path)))

(= (se-test:room-receipt-in $pairs $room)
   (if (== $pairs ()) ""
     (let (JsonPair $k $v) (car-atom $pairs)
       (if (== (sf:get $v "window_name" "") $room) $k
         (se-test:room-receipt-in (cdr-atom $pairs) $room)))))

(= (se-test:room-receipt $state-path)
   (case (sf:get (sf:native-object $state-path) "receipts" (JsonObject ()))
     (((JsonObject $pairs) (se-test:room-receipt-in $pairs "claude-room"))
      ($other ""))))

(= (se-test:first-receipt $state-path)
   (case (sf:get (sf:native-object $state-path) "receipts" (JsonObject ()))
     (((JsonObject $pairs)
       (if (== $pairs ()) ""
         (let (JsonPair $k $_) (car-atom $pairs) $k)))
      ($other ""))))

(= (se-test:run-batch $core $dir $state-path $phase-path $frontier-path $life-path $turn $batch-expr)
   (let* (($script (path:join $dir (str:join "" ("turn-" (repr $turn) ".metta"))))
          ($src (str:join "\n"
            ((str:join "" ("!(import! &self " (repr (path:join (system:cwd) "src/command_dispatch_core.metta")) ")"))
             (str:join "" ("!(import! &self " (repr (path:join (system:cwd) "src/command_dispatch_hooks.metta")) ")"))
             (str:join "" ("!(import! &self " (repr (path:join (system:cwd) "src/api_registry.metta")) ")"))
             "(= (dispatch:env $name $default) (system:getenv-or-default $name $default))"
             "(= (dispatch:length $text) (str:byte-length $text))"
             "(= (dispatch:compare $a $b) (str:compare $a $b))"
             (str:join "" ("!(println! (BEGIN (shadow-effect:begin-turn " (repr $turn) ")))"))
             (str:join "" ("!(println! (RECORDS (run-command-batch-once " (repr $turn) " 5 (quote " $batch-expr "))))"))
             "")))
          ($_ (fs:write $script $src))
          ($env (list:append (nt:environment $dir)
                  (("METTACLAW_EFFECT_BACKEND" "tmux-shadow")
                   ("METTACLAW_EFFECT_BACKEND_STATE" $state-path)
                   ("METTACLAW_TASK_PHASE_PATH" $phase-path)
                   ("METTACLAW_STIMULUS_FRONTIER_PATH" $frontier-path)
                   ("METTACLAW_LIFECYCLE_PATH" $life-path)
                   ("METTACLAW_EFFECT_RECEIPT_PATH" (path:join $dir "receipts.jsonl"))
                   ("METTACLAW_OBSERVED_STRATEGY" "0"))))
          ($res (proc:run ((path:join $core "cetta") "--lang" "petta" "--profile" "extended"
                           "--import-mode" "ancestor-walk" "--quiet" $script)
                          (system:cwd) $env 20000 1048576)))
     (nt:exit-ok $res)))

(= (se-test:case-complete $core $root)
   (let* (($dir (path:join $root "complete"))
          ($sock (str:concat "se-unit-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $fp $lp) (se-test:init-world $dir $sock False 5.0 (se-test:launch-cmd)))
          ; Premature shadow-finish (receipt confusion) refused before goal verified
          ($r0 (se-test:run-batch $core $dir $sp $pp $fp $lp 1 "((shadow-finish \"all prior operations completed\"))"))
          ($_ (nt:require premature-finish-rejected
                (and (nt:contains $r0 "REJECTED goal-not-verified")
                     (== (sf:get (sf:native-object $sp) "finished" True) False))))
          ; Fail-closed shell and pin denials never acquire live effects
          ($marker (path:join $dir "must-not-exist"))
          ($r-deny (se-test:run-batch $core $dir $sp $pp $fp $lp 2
                     (str:join "" ("((shell " (repr (str:concat "touch " $marker)) ") (pin \"proposal\"))"))))
          ($_ (nt:require denied-shell-and-pin
                (and (not (fs:exists $marker))
                     (and (nt:contains $r-deny "SHADOW_DENIED unsupported-command shell")
                          (and (nt:contains $r-deny "SHADOW_DENIED unsupported-command pin")
                               (== (sf:get (sf:native-object $sp) "effects" -1) 0))))))
          ; Complete verified tmux workflow
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 3 "((tmux-windows))"))
          ($r-create (se-test:run-batch $core $dir $sp $pp $fp $lp 4 "((tmux-new-shell-after \"r1\" \"claude-room\"))"))
          ($_ (nt:require created-room (nt:contains $r-create "CREATED shell-owned window claude-room")))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 5 "((tmux-windows))"))
          ($room1 (se-test:room-receipt $sp))
          ($r-launch (se-test:run-batch $core $dir $sp $pp $fp $lp 6
                       (str:join "" ("((tmux-send-observed " (repr $room1) " " (repr (se-test:launch-cmd)) "))"))))
          ($_ (nt:require launched-guest
                (and (nt:contains $r-launch "SENT exact-pane")
                     (== (sf:get (sf:native-object $sp) "phase" "") "need-trust"))))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 7 "((tmux-windows))"))
          ($room2 (se-test:room-receipt $sp))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 8
                (str:join "" ("((tmux-send-observed " (repr $room2) " \"yes\"))"))))
          ($_ (nt:require trust-answered (== (sf:get (sf:native-object $sp) "phase" "") "need-server")))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 9 "((tmux-windows))"))
          ($room3 (se-test:room-receipt $sp))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 10
                (str:join "" ("((tmux-send-observed " (repr $room3) " \"lean-lsp\"))"))))
          ($_ (nt:require server-selected (== (sf:get (sf:native-object $sp) "phase" "") "goal-satisfied")))
          ($r-fin (se-test:run-batch $core $dir $sp $pp $fp $lp 11 "((shadow-finish \"done\"))"))
          ($st (sf:native-object $sp))
          ($ph (sf:get (sf:native-object $pp) "phases" (JsonObject ())))
          ($_ (nt:require complete-verified
                (and (nt:contains $r-fin "TASK_VERIFIED")
                     (and (== (sf:get $st "finished" False) True)
                          (and (== (sf:get $st "effects" 0) 4)
                               (and (== (sf:get (sf:get $ph "need-create" (JsonObject ())) "status" "") "completed")
                                    (== (sf:get (sf:get $ph "goal-satisfied" (JsonObject ())) "status" "") "completed")))))))
          ($_ (se-test:cleanup $sock)))
     True))

(= (se-test:case-single-use-and-stop $core $root)
   (let* (($dir (path:join $root "single-use"))
          ($sock (str:concat "se-stop-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $fp $lp) (se-test:init-world $dir $sock True 5.0 (se-test:launch-cmd)))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 1 "((tmux-windows))"))
          ($rec (se-test:first-receipt $sp))
          ; Two sends with the same receipt in one batch: second must be withheld
          ($r-dup (se-test:run-batch $core $dir $sp $pp $fp $lp 2
                    (str:join "" ("((tmux-send-observed " (repr $rec) " \":\") (tmux-send-observed " (repr $rec) " \":\"))"))))
          ($st1 (sf:native-object $sp))
          ($_ (nt:require receipt-single-use
                (and (== (sf:get $st1 "effects" 0) 1)
                     (and (nt:contains $r-dup "SENT exact-pane")
                          (and (nt:contains $r-dup "unknown or expired observation receipt")
                               (== (sf:get $st1 "operator_epoch" 0) 1))))))
          ($_ (nt:require stimulus-frontier-published (nt:contains (fs:read-text $fp) "1 2 0 1")))
          ; After operator_epoch bumped, shadow-finish reports STOP_OBEYED
          ($r-stop (se-test:run-batch $core $dir $sp $pp $fp $lp 3 "((shadow-finish \"stopped\"))"))
          ($_ (nt:require operator-stop-obeyed
                (and (nt:contains $r-stop "STOP_OBEYED")
                     (== (sf:get (sf:native-object $sp) "finished" False) True))))
          ($_ (se-test:cleanup $sock)))
     True))

(= (se-test:case-timeout-nonreplay $core $root)
   (let* (($dir (path:join $root "timeout"))
          ($sock (str:concat "se-tmo-" (str:byte-slice (system:uuid4-hex) 0 10)))
          (($sp $pp $fp $lp) (se-test:init-world $dir $sock False 0.15 "printf NO_TRUST"))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 1 "((tmux-windows))"))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 2 "((tmux-new-shell-after \"r1\" \"claude-room\"))"))
          ($_ (se-test:run-batch $core $dir $sp $pp $fp $lp 3 "((tmux-windows))"))
          ($room (se-test:room-receipt $sp))
          ($r-tmo (se-test:run-batch $core $dir $sp $pp $fp $lp 4
                    (str:join "" ("((tmux-send-observed " (repr $room) " \"printf NO_TRUST\"))"))))
          ($_ (nt:require timeout-witnessed
                (and (nt:contains $r-tmo "SENT_UNVERIFIED TimeoutError")
                     (== (sf:get (sf:native-object $sp) "effects" 0) 2))))
          ($r-again (se-test:run-batch $core $dir $sp $pp $fp $lp 5
                      (str:join "" ("((tmux-send-observed " (repr $room) " \"printf AGAIN\"))"))))
          ($_ (nt:require timeout-receipt-not-replayable
                (and (nt:contains $r-again "unknown or expired observation receipt")
                     (== (sf:get (sf:native-object $sp) "effects" 0) 2))))
          ($_ (se-test:cleanup $sock)))
     True))

!(let* (($core (nt:required-env "NATIVE_TEST_CORE"))
        ($root (path:join (nt:required-env "HOME") (str:concat "se-unit-" (str:byte-slice (system:uuid4-hex) 0 10))))
        ($_ (fs:make-directories $root 448)))
   (progn
     (se-test:case-complete $core $root)
     (se-test:case-single-use-and-stop $core $root)
     (se-test:case-timeout-nonreplay $core $root)
     (println! UNIT_SHADOW_EFFECT_OK)))

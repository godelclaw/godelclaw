; Coding-session management as the agent uses it: start, report, steer, hold,
; resume on the pin and on the current runtime, archive, settings, stopping a
; running session and reaping a dead one. Workers really run coding_main.metta
; against the world's loopback provider, which serves response-<n>.body to
; request n. POSIX shell programs in ./scope-bin stand in for the user service
; manager and the mailer. A stop lets the worker end itself on its stop flag
; (it polls the flag), then ends whatever is left in the scope, as a cgroup
; stop would. Run with the test world as the working directory
; (scripts/census_python_entries_cetta.metta, world mode).
!(import! &self (library lib_import))
!(import! &self ./native_test_support.metta)
!(import! &self ../src/coding_work.metta)

(= (cl:here $name) (path:join (system:cwd) $name))
(= (cl:sleep) (proc:run ("sleep" "0.1") (system:cwd) inherit 5000 0 "merged"))
(= (cl:program $name $text)
   (let* (($path (path:join (cl:here "scope-bin") $name))
          ($_ (fs:write $path $text)))
     (fs:set-mode $path 448)))

;; A scope is the session its first process leads: live while any process of
;; that session lives (a zombie counts as gone), whoever started it and
;; whether or not its starter still lives. That is how a control group keeps
;; a command's background children after the command and the worker ended.
(= (cl:fixtures)
   (let* (($scopes (cl:here "scopes"))
          ($_ (fs:make-directories $scopes 448))
          ($_ (fs:make-directories (cl:here "scope-bin") 448))
          ($members (str:concat "members() { ps -s \"$1\" -o pid= -o stat= 2>/dev/null | while read -r p s; do case $s in Z*) ;; *) echo \"$p\" ;; esac; done; }\n"
                      "active() { [ -s \"$1\" ] && [ -n \"$(members \"$(cat \"$1\")\")\" ]; }\n"))
          ($_ (cl:program "systemd-run" (str:join "" ("#!/bin/sh\nunit=\"\"\nwhile [ $# -gt 0 ] && [ \"$1\" != \"--\" ]; do\n"
              "  case \"$1\" in --unit=*) unit=\"${1#--unit=}\" ;; esac\n  shift\ndone\nshift\n"
              "exec setsid sh -c 'echo $$ > \"$0\"; exec \"$@\"' " $scopes "/\"$unit.scope\" \"$@\"\n"))))
          ($_ (cl:program "systemctl" (str:join "" ("#!/bin/sh\n" $members "[ \"$1\" = --user ] || exit 1\nshift\n"
              "case \"$1\" in\n"
              "  is-active) shift; [ \"$1\" = --quiet ] && shift; active " $scopes "/\"$1\" && exit 0; exit 3 ;;\n"
              "  list-units) for f in " $scopes "/*.scope; do active \"$f\" && echo \"${f##*/} loaded active running\"; done; exit 0 ;;\n"
              "  stop) f=" $scopes "/\"$2\"\n"
              "    if [ -s \"$f\" ]; then sid=$(cat \"$f\"); i=0\n"
              "      while [ -n \"$(members \"$sid\" | grep -x \"$sid\")\" ] && [ $i -lt 100 ]; do sleep 0.05; i=$((i+1)); done\n"
              "      for p in $(members \"$sid\"); do kill -TERM \"$p\" 2>/dev/null; done; i=0\n"
              "      while [ -n \"$(members \"$sid\")\" ] && [ $i -lt 200 ]; do sleep 0.05; i=$((i+1)); done; fi; exit 0 ;;\n"
              "  *) exit 1 ;;\nesac\n"))))
          ;; What still runs in a scope, one command line a line.
          ($_ (cl:program "scope-members" (str:join "" ("#!/bin/sh\n" $members
              "f=" $scopes "/\"$1.scope\"\n[ -s \"$f\" ] || exit 0\n"
              "for p in $(members \"$(cat \"$f\")\"); do tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null; echo; done\n"))))
          ;; A scope holding one process nobody owns, its starter gone: what
          ;; a run's command leaves.
          ($_ (cl:program "scope-leftover" (str:join "" ("#!/bin/sh\n"
              "setsid sh -c 'echo $$ > \"$0.scope\"; sleep 307 > /dev/null 2>&1 & echo $! > \"$0.leftover\"' "
              $scopes "/\"$1\" > /dev/null 2>&1\n"))))
          ($_ (cl:program "mailer" "#!/bin/sh\nprintf '{\"status\":\"accepted\",\"id\":\"%s\"}\\n' \"$5\"\n")))
     True))
(= (cl:members $id)
   (case (proc:run ("scope-members" (work:unit $id)) (system:cwd) inherit 5000 65536 "merged")
     (((proc:exited 0 $out $err) $out) ($other (repr $other)))))
; The leftover's process, once its scope shows as active.
(= (cl:leftover $id)
   (let* (($_ (proc:run ("scope-leftover" (work:unit $id)) (system:cwd) inherit 5000 0 "merged"))
          ($_ (nt:require leftover-planted (nt:equal (cl:wait-scope $id active 50) active))))
     (str:trim (fs:read-text (path:join (cl:here "scopes") (str:concat (work:unit $id) ".leftover"))))))
(= (cl:wait-gone $pid $left)
   (if (not (fs:exists (str:concat "/proc/" $pid))) gone
     (if (<= $left 0) (CodingProbeStillAlive $pid)
       (let $_ (cl:sleep) (cl:wait-gone $pid (- $left 1))))))
(= (cl:wait-scope $id $state $left)
   (if (== (work:scope-state $id) $state) $state
     (if (<= $left 0) (CodingProbeScope $id (work:scope-state $id) (cl:members $id))
       (let $_ (cl:sleep) (cl:wait-scope $id $state (- $left 1))))))

;; Provider replies, by request number.
(= (cl:call $id $name $arguments)
   (JsonObject ((JsonPair "id" (JsonString $id)) (JsonPair "type" (JsonString "function"))
                (JsonPair "function" (JsonObject ((JsonPair "name" (JsonString $name))
                                                  (JsonPair "arguments" (JsonString (json-stringify $arguments)))))))))
(= (cl:reply $request $content $calls)
   (let* (($message (if (== $calls ())
                      (JsonObject ((JsonPair "role" (JsonString "assistant")) (JsonPair "content" (JsonString $content))))
                      (JsonObject ((JsonPair "role" (JsonString "assistant")) (JsonPair "content" (JsonString $content))
                                   (JsonPair "tool_calls" (JsonArray $calls))))))
          ($finish (if (== $calls ()) "stop" "tool_calls"))
          ($body (JsonObject ((JsonPair "id" (JsonString "census"))
                   (JsonPair "choices" (JsonArray ((JsonObject ((JsonPair "message" $message)
                                                               (JsonPair "finish_reason" (JsonString $finish)))))))
                   (JsonPair "usage" (JsonObject ((JsonPair "prompt_tokens" (JsonNumber "10"))
                                                  (JsonPair "completion_tokens" (JsonNumber "2")))))))))
     (fs:write (cl:here (format-args "response-{}.body" ($request))) (json-stringify $body))))

(= (cl:session-id $started)
   (let $word (car-atom (cdr-atom (str:split " " $started)))
     (str:char-slice $word 0 (- (str:char-length $word) 1))))
(= (cl:file $id $name) (path:join (work:dir $id) $name))
(= (cl:json $id $name) (work:json (cl:file $id $name)))
(= (cl:status $id) (work:text (work:field (cl:json $id "state.json") "status")))
(= (cl:phase $id) (work:text (work:field (cl:json $id "runtime.json") "phase")))
(= (cl:run $id) (work:text (work:field (cl:json $id "runtime.json") "run")))
; A worker that dies before it claims its run leaves the reservation behind;
; after a short grace for the background launch, that is a failure, not a wait.
(= (cl:wait-ended $id $left)
   (let ($phase $scope) ((cl:phase $id) (work:scope-state $id))
     (if (and (== $phase "ended") (== $scope inactive)) (cl:status $id)
       (if (or (<= $left 0) (and (< $left 870) (and (== $phase "reserved") (== $scope inactive))))
         (CodingProbeWorkerGone $id $phase (cl:status $id)
           (if (fs:is-file (cl:file $id "run.log")) (fs:read-text (cl:file $id "run.log")) ""))
         (let $_ (cl:sleep) (cl:wait-ended $id (- $left 1)))))))
; A run is over when its ending is recorded and its worker has exited; its
; scope may still hold what its commands left behind.
(= (cl:worker-gone? $id) (not (nt:contains (cl:members $id) "coding_main.metta")))
(= (cl:wait-over $id $left)
   (let ($phase $gone) ((cl:phase $id) (cl:worker-gone? $id))
     (if (and (== $phase "ended") $gone) (cl:status $id)
       (if (or (<= $left 0) (and (< $left 870) (and (== $phase "reserved") $gone)))
         (CodingProbeWorkerGone $id $phase (cl:status $id)
           (if (fs:is-file (cl:file $id "run.log")) (fs:read-text (cl:file $id "run.log")) ""))
         (let $_ (cl:sleep) (cl:wait-over $id (- $left 1)))))))
(= (cl:wait-running $id $left)
   (if (and (== (cl:phase $id) "started") (== (cl:status $id) "running")) running
     (if (<= $left 0) (CodingProbeTimeout $id (cl:phase $id) (cl:status $id))
       (let $_ (cl:sleep) (cl:wait-running $id (- $left 1))))))
; The long command marks itself running a moment after it starts, when its
; worker has long since recorded it and is polling it.
(= (cl:wait-command $marker $id $left)
   (if (fs:exists $marker) started
     (if (<= $left 0) (CodingProbeNoCommand $id (cl:status $id))
       (let $_ (cl:sleep) (cl:wait-command $marker $id (- $left 1))))))
(= (cl:has $id $name $text) (and (fs:is-file (cl:file $id $name)) (nt:contains (fs:read-text (cl:file $id $name)) $text)))

!(let* (($_ (cl:fixtures))
        ($workspace (cl:here "workspace")) ($_ (fs:make-directories $workspace 448))
        ;; The first command starts a background job and ends at once.
        ($_ (cl:reply 1 "" ((cl:call "c1" "exec_command" (JsonObject ((JsonPair "cmd" (JsonString "echo census > out.txt; sleep 300 &"))))))))
        ($_ (cl:reply 2 "" ((cl:call "c2" "apply_patch" (JsonObject ((JsonPair "patch"
              (JsonString "*** Begin Patch\n*** Add File: added.txt\n+census patch\n*** End Patch"))))))))
        ($_ (cl:reply 3 "Census work done." ()))
        ($_ (cl:reply 4 "Resumed on the pin." ()))
        ($_ (cl:reply 5 "Resumed on the current runtime." ()))
        ($_ (cl:reply 6 "" ((cl:call "c6" "exec_command"
              (JsonObject ((JsonPair "cmd" (JsonString "sleep 0.3; touch census-command-running; exec sleep 30"))
                           (JsonPair "yield_time_ms" (JsonNumber "60000"))))))))
        ;; Start: freeze the runtime, reserve a run token, launch the worker.
        ($started (work "Write the census files." $workspace))
        ($_ (nt:require work-started (nt:contains $started "WORK_STARTED work-")))
        ($id (cl:session-id $started))
        ($_ (nt:require first-run-done (nt:equal (cl:wait-over $id 900) "done")))
        ;; The command's background job is left in the scope, which therefore
        ;; outlives the run.
        ($_ (nt:require background-job-left (nt:contains (cl:members $id) "sleep 300")))
        ($_ (nt:require scope-outlives-run (nt:equal (work:scope-state $id) active)))
        ($_ (nt:require command-ran (nt:contains (fs:read-text (path:join $workspace "out.txt")) "census")))
        ($_ (nt:require patch-applied (nt:contains (fs:read-text (path:join $workspace "added.txt")) "census patch")))
        ($_ (nt:require report-written (cl:has $id "report.txt" "Census work done.")))
        ($_ (nt:require report-accepted (nt:equal (delivery:status (work:dir $id)) "accepted")))
        ;; The next observation of the sessions ends what the run left.
        ($_ (nt:require status-lists-session (nt:contains (work-status) $id)))
        ($_ (nt:require finished-scope-empty (nt:equal (work:scope-state $id) inactive)))
        ($_ (nt:require finished-scope-no-processes (nt:equal (cl:members $id) "")))
        ($_ (nt:require status-details-session (nt:contains (work-status $id) "Census work done.")))
        ;; Steer, then hold; a resume clears the hold and folds the steering in.
        ($_ (nt:require steered (nt:contains (work-steer $id "Also check the steering.") "WORK_STEERED")))
        ($_ (nt:require steering-recorded (cl:has $id "inbox.jsonl" "Also check the steering.")))
        ($_ (work-hold $id "census hold" "2099-01-01"))
        ($_ (nt:require hold-recorded (cl:has $id "lifecycle.json" "census hold")))
        ($first-run (cl:run $id))
        ;; A resume ends what an earlier run left, rather than taking it for a
        ;; running session.
        ($stray (cl:leftover $id))
        ($_ (nt:require pinned-resume (nt:contains (work-resume $id) "WORK_RESUMING")))
        ($_ (nt:require resume-ended-leftover (nt:equal (cl:wait-gone $stray 20) gone)))
        ($_ (nt:require pinned-run-done (nt:equal (cl:wait-ended $id 900) "done")))
        ($_ (nt:require hold-cleared (not (fs:is-file (cl:file $id "lifecycle.json")))))
        ($_ (nt:require pinned-report (cl:has $id "report.txt" "Resumed on the pin.")))
        ($_ (nt:require new-run-token (not (nt:equal (cl:run $id) $first-run))))
        ;; Recorded as pinned to an older runtime: the current runtime checks a
        ;; copy of the checkpoint before the session moves to it.
        ($_ (fs:write (cl:file $id "runtime.json")
              (json-stringify (sf:put (cl:json $id "runtime.json") "digest" (JsonString "older-runtime-digest")))))
        ($_ (nt:require current-resume (nt:contains (work-resume $id "current") "WORK_RESUMING")))
        ($_ (nt:require repin-recorded (cl:has $id "runs.jsonl" "\"from\":\"older-runtime-digest\"")))
        ($_ (nt:require current-run-done (nt:equal (cl:wait-ended $id 900) "done")))
        ($_ (nt:require current-report (cl:has $id "report.txt" "Resumed on the current runtime.")))
        ($_ (work-archive $id "census archive"))
        ($_ (nt:require archive-recorded (cl:has $id "lifecycle.json" "census archive")))
        ;; Settings persist.
        ($_ (nt:require context-default (nt:equal (work-setting "context") "open")))
        ($_ (work-setting "context" "summary"))
        ($_ (nt:require context-saved (nt:equal (work-setting "context") "summary")))
        ;; Stop a session whose worker waits on a long command.
        ($stopping (cl:session-id (work "Run the long command." $workspace)))
        ($_ (nt:require stopping-running (nt:equal (cl:wait-running $stopping 900) running)))
        ($_ (nt:require stopping-waits-on-command
              (nt:equal (cl:wait-command (path:join $workspace "census-command-running") $stopping 900) started)))
        ;; Observing the sessions leaves a living worker and its command alone.
        ($_ (nt:require observed-while-running (nt:contains (work-status) $stopping)))
        ($_ (nt:require observation-spares-worker (nt:equal ((cl:status $stopping) (cl:worker-gone? $stopping)) ("running" False))))
        ($_ (nt:require stopped (nt:contains (work-stop $stopping) "WORK_STOPPED")))
        ($_ (nt:require stopped-recorded (nt:equal (cl:status $stopping) "stopped")))
        ($_ (nt:require stop-report (cl:has $stopping "report.txt" "Stopped by the requester.")))
        ;; A reserved launch whose worker has not claimed its run yet is not a
        ;; leftover, whatever the state of the run before it says.
        ($launching (path:join (work:root) "work-census-launching"))
        ($_ (fs:make-directories $launching 448))
        ($_ (fs:write (path:join $launching "task.json") "{\"task\":\"Launching fixture.\"}"))
        ($_ (fs:write (path:join $launching "runtime.json")
              "{\"root\":\"/nonexistent\",\"run\":\"fedcba9876543210fedcba9876543210\",\"phase\":\"reserved\"}"))
        ($_ (fs:write (path:join $launching "state.json")
              "{\"status\":\"done\",\"steps\":1,\"progress\":\"done\",\"worker_owner\":\"4194301:1\",\"run\":\"0123456789abcdef0123456789abcdef\",\"inbox\":0,\"updated\":1.0}"))
        ($booting (cl:leftover "work-census-launching"))
        ($_ (nt:require observed-while-launching (nt:contains (work-status) "work-census-launching")))
        ($_ (nt:require observation-spares-launch (fs:exists (str:concat "/proc/" $booting))))
        ($_ (nt:require launch-stopped (nt:contains (work-stop "work-census-launching") "WORK_STOPPED")))
        ($_ (nt:require stop-ended-launch (nt:equal (cl:wait-gone $booting 20) gone)))
        ;; Reap a session whose worker died without recording its ending.
        ($dead (path:join (work:root) "work-census-dead"))
        ($_ (fs:make-directories $dead 448))
        ($_ (fs:write (path:join $dead "task.json")
              (json-stringify (JsonObject ((JsonPair "task" (JsonString "Dead fixture."))
                                           (JsonPair "workdir" (JsonString $workspace))
                                           (JsonPair "requester" (JsonString "census-requester")))))))
        ($_ (fs:write (path:join $dead "runtime.json")
              "{\"root\":\"/nonexistent\",\"run\":\"0123456789abcdef0123456789abcdef\",\"phase\":\"started\"}"))
        ($_ (fs:write (path:join $dead "state.json")
              "{\"status\":\"running\",\"steps\":3,\"progress\":\"working\",\"worker_owner\":\"4194301:1\",\"run\":\"0123456789abcdef0123456789abcdef\",\"inbox\":0,\"updated\":1.0}"))
        ;; Its scope still holds a process: the session is reaped all the same.
        ($orphan (cl:leftover "work-census-dead"))
        ($_ (nt:require reaped-listed (nt:contains (work-status) "work-census-dead")))
        ($_ (nt:require reaped-interrupted (nt:equal (cl:status "work-census-dead") "interrupted")))
        ($_ (nt:require reap-ended-leftover (nt:equal (cl:wait-gone $orphan 20) gone)))
        ($_ (nt:require reaped-scope-empty (nt:equal (work:scope-state "work-census-dead") inactive)))
        ($_ (nt:require reap-report (cl:has "work-census-dead" "report.txt" "ended without finishing"))))
   (println! CODING_LIFECYCLE_OK))

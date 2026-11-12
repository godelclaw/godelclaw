; Coding-session management as the agent uses it: start, report, steer, hold,
; resume on the pin and on the current runtime, archive, settings, stopping a
; running session and reaping a dead one. Workers really run coding_main.metta
; against the world's loopback provider, which serves response-<n>.body to
; request n. POSIX shell programs in ./scope-bin stand in for the user service
; manager (a stop ends the scope's whole process tree, as a cgroup stop does)
; and the mailer. Run with the test world as the working directory
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

;; A scope is live while the process it ran lives (a zombie counts as gone).
(= (cl:fixtures)
   (let* (($scopes (cl:here "scopes"))
          ($_ (fs:make-directories $scopes 448))
          ($_ (fs:make-directories (cl:here "scope-bin") 448))
          ($alive (str:concat "alive() { s=$(sed 's/.*) //' /proc/$1/stat 2>/dev/null | cut -d' ' -f1); [ -n \"$s\" ] && [ \"$s\" != Z ]; }\n"
                    "tree() { for c in $(pgrep -P \"$1\"); do tree \"$c\"; done; kill -TERM \"$1\" 2>/dev/null; }\n"))
          ($_ (cl:program "systemd-run" (str:join "" ("#!/bin/sh\nunit=\"\"\nwhile [ $# -gt 0 ] && [ \"$1\" != \"--\" ]; do\n"
              "  case \"$1\" in --unit=*) unit=\"${1#--unit=}\" ;; esac\n  shift\ndone\nshift\n"
              "echo $$ > " $scopes "/\"$unit.scope\"\nexec \"$@\"\n"))))
          ($_ (cl:program "systemctl" (str:join "" ("#!/bin/sh\n" $alive "[ \"$1\" = --user ] || exit 1\nshift\n"
              "case \"$1\" in\n"
              "  is-active) shift; [ \"$1\" = --quiet ] && shift; f=" $scopes "/\"$1\"\n"
              "    [ -f \"$f\" ] && alive \"$(cat \"$f\")\" && exit 0; exit 3 ;;\n"
              "  stop) f=" $scopes "/\"$2\"\n"
              "    if [ -f \"$f\" ]; then pid=$(cat \"$f\"); tree \"$pid\"; i=0\n"
              "      while alive \"$pid\" && [ $i -lt 200 ]; do sleep 0.05; i=$((i+1)); done; rm -f \"$f\"; fi; exit 0 ;;\n"
              "  *) exit 1 ;;\nesac\n"))))
          ($_ (cl:program "mailer" "#!/bin/sh\nprintf '{\"status\":\"accepted\",\"id\":\"%s\"}\\n' \"$5\"\n")))
     True))

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
(= (cl:wait-running $id $left)
   (if (and (== (cl:phase $id) "started") (== (cl:status $id) "running")) running
     (if (<= $left 0) (CodingProbeTimeout $id (cl:phase $id) (cl:status $id))
       (let $_ (cl:sleep) (cl:wait-running $id (- $left 1))))))
; A command the worker started is recorded under executions/.
(= (cl:wait-execution $id $left)
   (let $dir (cl:file $id "executions")
     (if (and (fs:exists $dir) (!= (fs:entries $dir) ())) started
       (if (<= $left 0) (CodingProbeNoExecution $id (cl:status $id))
         (let $_ (cl:sleep) (cl:wait-execution $id (- $left 1)))))))
(= (cl:has $id $name $text) (and (fs:is-file (cl:file $id $name)) (nt:contains (fs:read-text (cl:file $id $name)) $text)))

!(let* (($_ (cl:fixtures))
        ($workspace (cl:here "workspace")) ($_ (fs:make-directories $workspace 448))
        ($_ (cl:reply 1 "" ((cl:call "c1" "exec_command" (JsonObject ((JsonPair "cmd" (JsonString "echo census > out.txt"))))))))
        ($_ (cl:reply 2 "" ((cl:call "c2" "apply_patch" (JsonObject ((JsonPair "patch"
              (JsonString "*** Begin Patch\n*** Add File: added.txt\n+census patch\n*** End Patch"))))))))
        ($_ (cl:reply 3 "Census work done." ()))
        ($_ (cl:reply 4 "Resumed on the pin." ()))
        ($_ (cl:reply 5 "Resumed on the current runtime." ()))
        ($_ (cl:reply 6 "" ((cl:call "c6" "exec_command"
              (JsonObject ((JsonPair "cmd" (JsonString "sleep 30")) (JsonPair "yield_time_ms" (JsonNumber "60000"))))))))
        ;; Start: freeze the runtime, reserve a run token, launch the worker.
        ($started (work "Write the census files." $workspace))
        ($_ (nt:require work-started (nt:contains $started "WORK_STARTED work-")))
        ($id (cl:session-id $started))
        ($_ (nt:require first-run-done (nt:equal (cl:wait-ended $id 900) "done")))
        ($_ (nt:require command-ran (nt:contains (fs:read-text (path:join $workspace "out.txt")) "census")))
        ($_ (nt:require patch-applied (nt:contains (fs:read-text (path:join $workspace "added.txt")) "census patch")))
        ($_ (nt:require report-written (cl:has $id "report.txt" "Census work done.")))
        ($_ (nt:require report-accepted (nt:equal (delivery:status (work:dir $id)) "accepted")))
        ($_ (nt:require status-lists-session (nt:contains (work-status) $id)))
        ($_ (nt:require status-details-session (nt:contains (work-status $id) "Census work done.")))
        ;; Steer, then hold; a resume clears the hold and folds the steering in.
        ($_ (nt:require steered (nt:contains (work-steer $id "Also check the steering.") "WORK_STEERED")))
        ($_ (nt:require steering-recorded (cl:has $id "inbox.jsonl" "Also check the steering.")))
        ($_ (work-hold $id "census hold" "2099-01-01"))
        ($_ (nt:require hold-recorded (cl:has $id "lifecycle.json" "census hold")))
        ($first-run (cl:run $id))
        ($_ (nt:require pinned-resume (nt:contains (work-resume $id) "WORK_RESUMING")))
        ($_ (nt:require pinned-run-done (nt:equal (cl:wait-ended $id 900) "done")))
        ($_ (nt:require hold-cleared (not (fs:is-file (cl:file $id "lifecycle.json")))))
        ($_ (nt:require pinned-report (cl:has $id "report.txt" "Resumed on the pin.")))
        ($_ (nt:require new-run-token (not (nt:equal (cl:run $id) $first-run))))
        ($_ (nt:require current-resume (nt:contains (work-resume $id "current") "WORK_RESUMING")))
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
        ($_ (nt:require stopping-waits-on-command (nt:equal (cl:wait-execution $stopping 900) started)))
        ($_ (nt:require stopped (nt:contains (work-stop $stopping) "WORK_STOPPED")))
        ($_ (nt:require stopped-recorded (nt:equal (cl:status $stopping) "stopped")))
        ($_ (nt:require stop-report (cl:has $stopping "report.txt" "Stopped by the requester.")))
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
        ($_ (nt:require reaped-listed (nt:contains (work-status) "work-census-dead")))
        ($_ (nt:require reaped-interrupted (nt:equal (cl:status "work-census-dead") "interrupted")))
        ($_ (nt:require reap-report (cl:has "work-census-dead" "report.txt" "ended without finishing"))))
   (println! CODING_LIFECYCLE_OK))

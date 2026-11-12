; Coding mode runs Codex's work loop over one task. A session is a folder:
;   task.json       {"task", "workdir", "requester", "effort"}
;   messages.jsonl  the conversation, one JSON message per line, append-only
;   plan.json       the latest plan
;   inbox.jsonl     steering from the requester, one JSON string per line
;   state.json      status, steps, the latest progress text, inbox position
;   report.txt      the final reply
;   stop            present once the requester stops the session
; The model is asked, its tool calls run, their results are appended, and the
; loop continues until a reply calls no tool: there is no step cap. Output is
; cut in the middle at about 10k tokens, and past 90% of the model's window
; the history becomes a handoff summary, as Codex does.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library system))
!(import! &self (library str))
!(import! &self (library math))
!(import! &self (library json))
!(import! &self ./src/apply_patch.py)
!(import! &self ./src/coding_io.py)
!(import! &self ./src/coding_io_cetta.metta)
!(import! &self ./src/coding_values.metta)
!(import! &self ./src/coding_guidance.metta)
!(import! &self ./src/coding_checkpoint.metta)
!(import! &self ./src/coding_process.metta)
!(import! &self ./src/coding_delivery.metta)
!(import! &self ./src/process_identity_cetta.metta)

(= (coding:run-token) (system:getenv-or-default "METTACLAW_CODING_RUN_TOKEN" ""))
(= (coding:delivery-id $dir $status $steps $progress $inbox)
   (let* (($pairs ((JsonPair "status" (JsonString $status))
                   (JsonPair "steps" (JsonNumber (repr $steps)))
                   (JsonPair "progress" (JsonString $progress))
                   (JsonPair "delivery" (JsonString (if (== $status "running") "none" "pending")))
                   (JsonPair "delivery_version" (JsonNumber "2"))
                   (JsonPair "worker_owner" (JsonString (process:owner)))
                   (JsonPair "inbox" (JsonNumber (repr $inbox)))
                   (JsonPair "updated" (JsonNumber (repr (coding:now))))))
          ($value (JsonObject $pairs)))
     (if (== $status "running") "" (delivery:identity $dir $value $status))))
(= (coding:state $dir $status $steps $progress $inbox)
   (let $delivery-id (coding:delivery-id $dir $status $steps $progress $inbox)
     (cio:worker-state $dir (coding:run-token) (process:owner)
       $status $steps $progress $inbox $delivery-id NoReport)))

; Codex keeps the beginning and the end of long output.
(= (coding:cut $text)
   (let $length (str:char-length $text)
     (if (<= $length 40000) $text
       (str:join "" ((str:char-slice $text 0 20000)
                     (str:join "" ("\n…" (repr (math:ceil (/ (- $length 40000) 4))) " tokens truncated…\n"))
                     (str:char-slice $text (- $length 20000) $length))))))

(= (coding:exec-report $code $seconds $output)
   (str:join "" ("Exit code: " $code "\nWall time: " (repr $seconds) " seconds\nOutput:\n" (coding:cut $output))))
(= (coding:patch $workdir $patch)
   (let $receipt (py-call (apply_patch.apply $patch $workdir)) (format-args "{}" ($receipt))))
(= (coding:plan $dir $args)
   (let* (($text (json-stringify $args))
          ($_ (fs:update-text (coding:file $dir "plan.json") "replace" $text "" ""))
          ($_ (py-call (stream_sink.event "plan" "plan" $text)))) "Plan updated"))
(= (coding:tool $dir $workdir $name $args)
   (if (== $name "exec_command") (coding:exec-owned $dir $workdir $args)
    (if (== $name "exec_poll") (coding:poll $dir $args)
     (if (== $name "exec_cancel") (coding:cancel $dir $args)
     (if (== $name "apply_patch")
       (coding:patch $workdir (coding:text (coding:field $args "patch")))
       (if (== $name "update_plan") (coding:plan $dir $args)
         (str:concat "Unknown tool: " $name)))))))
(= (coding:arguments $raw)
   (case (catch (json-parse (if (== $raw "") "{}" $raw)))
     (((Error $where $why) (JsonObject ())) ($parsed $parsed))))
(= (coding:run-call $dir $workdir $call)
   (let* (($id (coding:text (coding:field $call "id")))
          ($function (coding:field $call "function"))
          ($name (coding:text (coding:field $function "name")))
          ($args (coding:arguments (coding:text (coding:field $function "arguments"))))
          ($output (if (fs:is-file (coding:file $dir "stop"))
                     "Cancelled before execution; this call was not run."
                     (coding:tool $dir $workdir $name $args))))
     (progn
       (if (== $name "apply_patch")
         (let $status (if (str:starts-with $output "Success.") "applied" "rejected")
           (py-call (stream_sink.event "patch" $id $output $status "apply_patch"))) 1)
       (coding:record $dir (coding:tool-message $id $output)))))
; What a reply without text did, for the progress line, as Codex shows the
; commands it runs: the command, the patched files or the plan update.
(= (coding:call-line $call)
   (let* (($function (coding:field $call "function"))
          ($name (coding:text (coding:field $function "name")))
          ($args (coding:arguments (coding:text (coding:field $function "arguments")))))
     (case $name
       (("exec_command" (str:concat "$ " (coding:text (coding:field $args "cmd"))))
        ("apply_patch" (str:concat "apply_patch" (coding:patch-files (str:split "\n" (coding:text (coding:field $args "patch"))) "")))
        ($other $name)))))
(= (coding:patch-files $lines $out)
   (if (== $lines ()) $out
     (let* (($line (car-atom $lines))
            ($file (if (str:starts-with $line "*** ") (str:concat " " (str:char-slice $line 4 (str:char-length $line))) "")))
       (coding:patch-files (cdr-atom $lines) (if (or (== $file "") (or (str:starts-with $line "*** Begin") (str:starts-with $line "*** End")))
                                                $out (str:concat $out $file))))))
(= (coding:call-lines $calls $out)
   (if (== $calls ()) (coding:reverse $out ())
     (let $line (coding:call-line (car-atom $calls)) (coding:call-lines (cdr-atom $calls) (cons-atom $line $out)))))
(= (coding:progress $text $calls)
   (if (not (== (str:trim $text) "")) $text
     (let $lines (coding:call-lines $calls ())
       (let $joined (str:join " · " $lines)
         (if (<= (str:char-length $joined) 300) $joined (str:concat (str:char-slice $joined 0 300) " …"))))))
(= (coding:run-calls $dir $workdir $calls $out)
   (if (== $calls ()) (coding:reverse $out ())
     (coding:run-calls $dir $workdir (cdr-atom $calls)
       (cons-atom (coding:run-call $dir $workdir (car-atom $calls)) $out))))

(= (coding:tools) (json-parse (fs:read-text "identity/coding-tools.json")))
(= (coding:request $messages $effort $tools)
   (let* (($pairs ((JsonPair "model" (JsonString "session")) (JsonPair "max_tokens" (JsonNumber "32768"))
                   (JsonPair "messages" (JsonArray (coding:wire-messages $messages))) (JsonPair "cache_messages" (JsonBool True))))
          ($reasoned (if (== $effort "default") $pairs
                       (cons-atom (JsonPair "reasoning" (JsonObject ((JsonPair "effort" (JsonString $effort))))) $pairs))))
     (JsonObject (if (== $tools ()) $reasoned (cons-atom (JsonPair "tools" $tools) $reasoned)))))
; Input and handoff occurrence metadata belongs to the local transcript, never the wire.
; Provider reasoning/signature fields and all other message fields survive.
(= (coding:wire-fields $fields)
   (if (== $fields ()) ()
     (let (JsonPair $key $value) (car-atom $fields)
       (let $rest (coding:wire-fields (cdr-atom $fields))
         (if (or (== $key "_coding_guidance") (or (== $key "_coding_input") (== $key "_coding_handoff"))) $rest
           (cons-atom (car-atom $fields) $rest))))))
(= (coding:wire-messages $messages)
   (if (== $messages ()) ()
     (let (JsonObject $fields) (car-atom $messages)
       (cons-atom (JsonObject (coding:wire-fields $fields)) (coding:wire-messages (cdr-atom $messages))))))
; A request without tools is the session's history compaction.
(= (coding:ask-once $dir $request)
   (let* (($text (json-stringify $request))
          ($purpose (if (== (coding:field $request "tools") JsonNull) "compaction" "coding"))
          ($plan (llm:structured-plan $request $purpose))
          ($pending (llm:begin $plan))
          ($answer (coding:wait-provider $plan $pending))
          ($value (llm:reply-file $answer (coding:file $dir "reply.json")))
          ($_ (py-call (coding_io.context_observe $dir (str:byte-length $text)))))
     $value))

; This session schedules its own process owner while waiting on the same
; admitted provider request. No callback searches another module's local space.
(= (coding:wait-provider $plan $state)
   (case $state
     (((llm:v1:pending $epoch $correlation $protocol $handle)
       (let* (($_ (coding:harvest)) ($next (llm:wait-once $plan $state 100)))
         (coding:wait-provider $plan $next)))
      ($terminal (llm:after-submit $plan $terminal)))))
; A Python error as its type and message; repr of the error object shows
; only an address.
(= (coding:error-text $where $why)
   (case $where
     (((python_error $type $exc)
        (case (catch (py-call (builtins.str $exc)))
          (((Error $a $b) (repr $where))
           ($message (format-args "{}: {}" ($type $message))))))
      ($other (str:join " " ((repr $where) (repr $why)))))))
; Failed calls are retried with a growing wait, as Codex retries five times.
; A request the provider rejected would only be rejected again: the session
; stops at once, and its report names the status.
(= (coding:ask $dir $request $attempt)
   (case (catch (coding:ask-once $dir $request))
     (((Error $where $why)
        (let* (($text (coding:error-text $where $why))
               ($_ (println! (CodingCallFailed attempt $attempt $text))))
          (if (or (>= $attempt 4) (coding:rejected? $text)) (CallFailed $text)
            (let* (($wait (* 2 (+ $attempt 1))) ($_ (py-call (time.sleep $wait)))) (coding:ask $dir $request (+ $attempt 1))))))
      ($reply (case (coding:field $reply "message")
                ((JsonNull (if (>= $attempt 4) (CallFailed "no message in the reply")
                             (coding:ask $dir $request (+ $attempt 1))))
                 ($message (coding:validate-reply $reply))))))))

(= (coding:rejected? $text)
   (or (case (str:find $text "provider rejected the request") (((TextNotFound $provider-missing) False) ($provider-at True)))
       (case (str:find $text "CompletionConflict") (((TextNotFound $receipt-missing) False) ($receipt-at True)))))

(= (coding:validate-reply $reply)
   (let* (($message (coding:field $reply "message"))
          ($calls (coding:items (coding:field $message "tool_calls")))
          ($finish (coding:text (coding:field $reply "finish")))
          ($refusal (coding:text (coding:field $message "refusal"))))
     (if (not (== $refusal "")) (CallFailed "The provider refused the request.")
       (if (or (and (== $finish "stop") (== $calls ()))
               (and (== $finish "tool_calls") (not (== $calls ())))) $reply
         (CallFailed (str:concat "Incomplete or invalid provider finish: " $finish))))))

; Tokens are estimated at four bytes each, as Codex estimates new items, or at
; the higher rate the provider counted for this session's last request.
(= (coding:estimate $dir $request) (py-call (coding_io.context_tokens $dir (str:byte-length (json-stringify $request)))))
; Read at every step: the provider reports the model's window after the first.
(= (coding:window)
   (let* (($_ (llm:observe-window)) ($capacity (py-call (strategy_host.context_capacity 32768)))
          ($reported (if (== (get-metatype $capacity) Grounded) $capacity 200000))
          ($raw (str:string->number (system:getenv-or-default "METTACLAW_CODING_CONTEXT_TOKENS" "256000")))
          ($ceiling (if (== (get-type $raw) Number)
                       (if (and (>= $raw 4096) (not (math:isinf $raw))) (math:floor $raw) 256000)
                       256000)))
     (min $reported $ceiling)))
(= (coding:steering $lines $index $out)
   (if (== $lines ()) (coding:reverse $out ())
     (let (JsonObject $fields) (coding:message "user" (str:concat "From the requester, mid-task: " (coding:text (car-atom $lines))))
       (coding:steering (cdr-atom $lines) (+ $index 1)
         (cons-atom (JsonObject (cons-atom (JsonPair "_coding_input" (JsonNumber (repr $index))) $fields)) $out)))))
(= (coding:inbox-position $messages $consumed)
   (if (== $messages ()) $consumed
     (case (coding:field (car-atom $messages) "_coding_input")
       (((JsonNumber $index) (coding:inbox-position (cdr-atom $messages) (max $consumed (+ 1 (str:string->number $index)))))
        ($other (coding:inbox-position (cdr-atom $messages) $consumed))))))
(= (coding:fold-inbox $dir $messages $consumed)
   (let* (($position (coding:inbox-position $messages $consumed))
          ($lines (coding:json-lines (coding:file $dir "inbox.jsonl")))
          ($new (coding:drop $lines $position)))
     (if (== $new ()) (Folded $messages $position)
       (let* (($added (coding:record-all $dir (coding:steering $new $position ()) ()))
              ($_ (py-call (stream_sink.event "steering" (repr $position) "Requester guidance applied" "applied"))))
         (Folded (coding:append-list $messages $added) (+ $position (length $new)))))))

; The compacted history keeps the instructions, the task and the requester's
; later messages, then the latest handoff summary; the full history is archived.
; Only marked generated handoffs are replaced. Unmarked legacy messages stay.
(= (coding:requester-messages $messages $out)
   (if (== $messages ()) (coding:reverse $out ())
     (let $message (car-atom $messages)
       (coding:requester-messages (cdr-atom $messages)
         (if (and (== (coding:text (coding:field $message "role")) "user")
                  (== (coding:field $message "_coding_handoff") JsonNull))
           (cons-atom $message $out) $out)))))
(= (coding:summary-tool-free? $message)
   (and (== (coding:field $message "function_call") JsonNull)
     (case (coding:field $message "tool_calls")
       ((JsonNull True) ((JsonArray ()) True) ($other False)))))
(= (coding:accept-summary $dir $messages $reply)
   (let* (($message (coding:field $reply "message"))
          ($summary (coding:text (coding:field $message "content"))))
     (if (not (and (== (coding:text (coding:field $reply "finish")) "stop")
                    (and (== (coding:text (coding:field $message "role")) "assistant")
                         (coding:summary-tool-free? $message))))
       (CompactionFailed "The handoff must be a complete, tool-free assistant summary.")
       (if (== (str:trim $summary) "") (CompactionFailed "The handoff summary was empty.")
         (let* (($head (cons-atom (car-atom $messages) (coding:requester-messages (cdr-atom $messages) ())))
                ((JsonObject $fields) (coding:message "user" (str:concat
                  "Another model began this task and left this handoff summary; continue from it and do not restart from scratch.\n\n"
                  $summary)))
                ($handoff (JsonObject (cons-atom
                  (JsonPair "_coding_handoff" (JsonString (py-call (secrets.token_hex 8)))) $fields))))
           (coding:rewrite $dir (coding:append-list $head ($handoff))))))))
(= (coding:compact $dir $messages $effort)
   (let* (($_ (py-call (stream_sink.event "maintenance" "compaction" "Compacting task history" "running")))
          ($ask (coding:append-list $messages ((coding:message "user" (fs:read-text "identity/coding-compact.md")))))
          ($reply (coding:ask $dir (coding:request $ask $effort ()) 0)))
     (let $result (case $reply
       (((CallFailed $why) (CompactionFailed $why))
        ($ok (coding:accept-summary $dir $messages $ok))))
       (progn (py-call (stream_sink.event "maintenance" "compaction" "Task history compaction finished; transcript retains the outcome" "finished")) $result))))

(= (coding:proc-text $result)
   (case $result
     (((proc:exited 0 $out $err) (str:trim (str:concat $out $err)))
      ((proc:exited $code $out $err)
       (str:join "" ("unavailable (git exited " (repr $code) "): " (str:trim (str:concat $out $err)))))
      ((proc:signaled $signal $out $err)
       (str:join "" ("unavailable (git signal " (repr $signal) "): " (str:trim (str:concat $out $err)))))
      ((proc:timed-out $out $err) "unavailable (git observation timed out)")
      ($other (str:concat "unavailable: " (repr $other))))))
(= (coding:workspace-evidence $workdir)
   (let* (($status (coding:proc-text
                     (proc:run ("git" "status" "--short" "--untracked-files=all")
                               $workdir inherit 30000 0 "merged")))
          ($unstaged (coding:proc-text
                       (proc:run ("git" "diff" "--stat")
                                 $workdir inherit 30000 0 "merged")))
          ($staged (coding:proc-text
                     (proc:run ("git" "diff" "--cached" "--stat")
                               $workdir inherit 30000 0 "merged"))))
     (coding:cut (str:join "" (
       "status:\n" (if (== $status "") "clean" $status)
       "\n\nunstaged diff:\n" (if (== $unstaged "") "none" $unstaged)
       "\n\nstaged diff:\n" (if (== $staged "") "none" $staged))))))
(= (coding:failure-handover $dir $workdir $reason)
   (let* (($state (coding:json-file (coding:file $dir "state.json") (JsonObject ())))
          ($progress (coding:text (coding:field $state "progress")))
          ($plan (if (fs:is-file (coding:file $dir "plan.json"))
                   (coding:cut (fs:read-text (coding:file $dir "plan.json"))) "none recorded"))
          ($id (coding:last (str:split "/" $dir))))
     (str:join "" (
       "The coding session stopped before completion.\n\nReason:\n" $reason
       "\n\nLast progress:\n" (if (== $progress "") "none recorded" $progress)
       "\n\nPlan:\n" $plan
       "\n\nWorkspace changes:\n" (coding:workspace-evidence $workdir)
       "\n\nResume without replaying uncertain effects:\n(work-resume \"" $id "\")"))))
(= (coding:finish $dir $status $text $steps $inbox)
   (let* (($delivery-id (coding:delivery-id $dir $status $steps $text $inbox))
          ($wrote (cio:worker-state $dir (coding:run-token) (process:owner)
                    $status $steps $text $inbox $delivery-id $text)))
     (if (== $wrote 0)
       (CodingAborted "superseded")
       (let $_ (py-call (stream_sink.event "session.finished" "session" $text $status))
         (CodingFinished $status)))))
(= (coding:fail $dir $workdir $reason $steps $inbox)
   (coding:finish $dir "failed" (coding:failure-handover $dir $workdir $reason) $steps $inbox))
(= (coding:loop $dir $workdir $effort $tools $messages $steps $inbox)
   (if (fs:is-file (coding:file $dir "stop")) (coding:finish $dir "stopped" "Stopped by the requester." $steps $inbox)
     (let* (((Folded $steered $next) (coding:fold-inbox $dir $messages $inbox))
            ($size (coding:estimate $dir (coding:request $steered $effort $tools)))
            ($prepared (if (> $size (* 0.9 (coding:window))) (coding:compact $dir $steered $effort) (Compacted $steered))))
       (case $prepared
         (((CompactionFailed $why)
            (coding:fail $dir $workdir (str:concat "The session could not compact its history: " $why) $steps $inbox))
          ((Compacted $current) (coding:turn $dir $workdir $effort $tools $current $steps $next)))))))
(= (coding:turn $dir $workdir $effort $tools $current $steps $next)
   (case (coding:ask $dir (coding:request $current $effort $tools) 0)
         (((CallFailed $why) (coding:fail $dir $workdir (str:concat "The model call failed: " $why) $steps $next))
          ($ok
            (let* (($message (coding:record $dir (coding:field $ok "message")))
                   ($calls (coding:items (coding:field $message "tool_calls")))
                   ($text (coding:text (coding:field $message "content")))
                   ($wrote (coding:state $dir "running" (+ $steps 1) (coding:progress $text $calls) $next)))
              (if (== $wrote 0)
                (CodingAborted "superseded")
                (if (== $calls ())
                  (coding:after-final $dir $workdir $effort $tools
                    (coding:append-list $current ($message)) (+ $steps 1) $next $text)
                  (let $results (coding:run-calls $dir $workdir $calls ())
                    (coding:loop $dir $workdir $effort $tools
                      (coding:append-list $current (cons-atom $message $results)) (+ $steps 1) $next)))))))))

; Guidance can arrive while the model is answering. A no-tool reply ends only
; the input frontier it saw; newer guidance continues this same transcript.
(= (coding:after-final $dir $workdir $effort $tools $messages $steps $inbox $text)
   (if (fs:is-file (coding:file $dir "stop")) (coding:finish $dir "stopped" "Stopped by the requester." $steps $inbox)
     (let (Folded $current $next) (coding:fold-inbox $dir $messages $inbox)
       (if (> $next $inbox) (coding:loop $dir $workdir $effort $tools $current $steps $next)
         (coding:finish $dir "done" $text $steps $next)))))

(= (coding:start $dir)
   (let* (($task-json (coding:json-file (coding:file $dir "task.json") (JsonObject ())))
          ($task (coding:text (coding:field $task-json "task")))
          ($_ (py-call (stream_sink.event "session.open" "session" $task "running")))
          ($given (coding:text (coding:field $task-json "workdir")))
          ($workdir (if (== $given "") $dir $given))
          ($chosen (coding:text (coding:field $task-json "effort")))
          ($effort (if (== $chosen "") "high" $chosen))
          ($existing (coding:json-lines (coding:file $dir "messages.jsonl")))
          ($state (coding:json-file (coding:file $dir "state.json") (JsonObject ())))
          ($steps (coding:number (coding:field $state "steps") 0))
          ($inbox (coding:number (coding:field $state "inbox") 0)))
     (case (guidance:workspace $workdir)
       (((GuidanceWorkspace $canonical)
         (case (coding:prepare-guidance $dir $task $canonical (coding:field $task-json "guidance") $existing)
           (((CodingPrepared $messages)
             (if (== (coding:state $dir "running" $steps "started" $inbox) 0)
               (CodingAborted "superseded")
               (coding:loop $dir $canonical $effort (coding:tools) $messages $steps $inbox)))
            ((GuidanceFailed $why) (coding:fail $dir $canonical $why $steps $inbox)))))
        ((GuidanceFailed $why) (coding:fail $dir (system:cwd) $why $steps $inbox))))))

(= (coding:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (coding:last (cdr-atom $items))))
(= (coding:notify $dir $status)
   (let $_ (coding:announce $dir $status) (CodingNotified $status)))

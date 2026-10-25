; A session's report reaches its requester by mail. How a session ended and
; whether its report was handed over are separate durable facts in its
; state.json: `status` records the ending, `delivery` records `delivered` or
; `pending`, with the mail's id and the last error. The reaper retries a
; pending delivery at the next turn, so a report is not lost while the mail
; service or the requester is down. Every attempt sends the same occurrence id,
; so the requester gets one mail however many attempts it took. Sessions
; recorded before delivery was tracked have no `delivery` and are not mailed
; again.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library system))
!(import! &self (library str))
!(import! &self (library json))

(= (delivery:json $path) (if (fs:is-file $path) (json-parse (fs:read-text $path)) (JsonObject ())))
(= (delivery:field $value $key)
   (case $value (((JsonObject $pairs) (delivery:member $pairs $key)) ($other JsonNull))))
(= (delivery:member $pairs $key)
   (if (== $pairs ()) JsonNull
     (let (JsonPair $name $value) (car-atom $pairs)
       (if (== $name $key) $value (delivery:member (cdr-atom $pairs) $key)))))
(= (delivery:text $value)
   (case $value (((JsonString $text) $text) ((JsonNumber $text) $text) ($other ""))))
(= (delivery:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (delivery:last (cdr-atom $items))))
(= (delivery:append $first $second)
   (if (== $first ()) $second (cons-atom (car-atom $first) (delivery:append (cdr-atom $first) $second))))
(= (delivery:own? $name)
   (or (== $name "delivery") (or (== $name "delivery_id") (== $name "delivery_error"))))
(= (delivery:others $pairs)
   (if (== $pairs ()) ()
     (let* (((JsonPair $name $value) (car-atom $pairs)) ($rest (delivery:others (cdr-atom $pairs))))
       (if (delivery:own? $name) $rest (cons-atom (JsonPair $name $value) $rest)))))
(= (delivery:record $dir $outcome $mail $error)
   (let* (($path (path:join $dir "state.json"))
          ($kept (case (delivery:json $path) (((JsonObject $pairs) (delivery:others $pairs)) ($other ()))))
          ($own ((JsonPair "delivery" (JsonString $outcome)) (JsonPair "delivery_id" (JsonString $mail))
                 (JsonPair "delivery_error" (JsonString $error))))
          ($_ (fs:update-text $path "replace" (json-stringify (JsonObject (delivery:append $kept $own))) "" "")))
     $outcome))
(= (delivery:failure $result)
   (case $result
     (((proc:exited $code $out $err) (str:join "" ("mailer exited " (repr $code) ": " (str:trim (str:concat $out $err)))))
      ((proc:signaled $signal $out $err) (str:concat "mailer killed by signal " (repr $signal)))
      ((proc:timed-out $out $err) "mailer timed out")
      ($other (str:concat "mailer did not run: " (repr $other))))))

; The report mail, sent or retried. With no requester there is nothing to hand
; over, and nothing is recorded.
(= (coding:deliver $dir $status)
   (let* (($task (delivery:json (path:join $dir "task.json")))
          ($requester (delivery:text (delivery:field $task "requester"))))
     (if (== $requester "") "none"
       (let* (($id (delivery:last (str:split "/" $dir)))
              ($steps (delivery:text (delivery:field (delivery:json (path:join $dir "state.json")) "steps")))
              ($report (if (fs:is-file (path:join $dir "report.txt")) (fs:read-text (path:join $dir "report.txt")) ""))
              ($text (str:join "" ("Work " $id " " $status " after " (if (== $steps "") "0" $steps) " steps. Task: "
                                   (delivery:text (delivery:field $task "task")) "\n\nReport:\n" $report)))
              ($mail (str:char-slice (str:sha256 (str:join ":" ($id $status $report))) 0 32))
              ($mailer (system:getenv-or-default "METTACLAW_WORK_MAIL" "claw-mail"))
              ($result (proc:run ("env" (str:concat "CLAW_MAIL_AGENT=" $id) $mailer "send" $requester $text "--id" $mail)
                                 $dir inherit 60000 0 "merged")))
         (case $result
           (((proc:exited 0 $out $err) (delivery:record $dir "delivered" $mail ""))
            ($failed (delivery:record $dir "pending" $mail (delivery:failure $failed)))))))))

; A session's ending: its report mail, and a note in the public worklog when a
; worklog command is configured. Retries only resend the mail.
(= (coding:announce $dir $status)
   (let* (($outcome (coding:deliver $dir $status))
          ($worklog (system:getenv-or-default "METTACLAW_WORKLOG_COMMAND" ""))
          ($report (if (fs:is-file (path:join $dir "report.txt")) (fs:read-text (path:join $dir "report.txt")) ""))
          ($id (delivery:last (str:split "/" $dir)))
          ($_ (if (== $worklog "") ()
                (proc:run ($worklog "add" (str:join "" ("Work " $id " " $status ": " (str:char-slice $report 0 900))))
                          $dir inherit 60000 0 "merged"))))
     $outcome))

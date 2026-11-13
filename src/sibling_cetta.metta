; Sibling-claw control: reliable start, stop and status of the other agent,
; and the operator's start of this one.
;
; "Stop the sibling" is only reliable if it also speaks the watchdog's pause
; convention: a PAUSED-BY-OPERATOR.md marker in the sibling's body directory.
; Stopping the service without the marker lets the watchdog revive it within
; 30 minutes; removing the marker without starting leaves it down until the
; watchdog's next pass. Each entry does both halves in the safe order (the
; marker before a stop; the marker removed before a start) and reports what it
; observed afterwards, not what it attempted.
;
; Which agent is "the sibling" follows from the body this process runs in:
; its working directory, inside one of the two body directories. The same
; file serves both bodies.
;
; Entries: (sibling:status), (sibling:on), (sibling:off REASON),
; (sibling:resume-self). Each has an -in form taking the working directory.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./utils_cetta.metta)

(= (sibling:claws) (godel lila))
(= (sibling:body godel) (path:join (system:expanduser "~") "pettaclaw-godel"))
(= (sibling:body lila) (path:join (system:expanduser "~") "pettaclaw-lila"))
(= (sibling:unit godel) "pettaclaw-godel.service")
(= (sibling:unit lila) "pettaclaw-lila.service")
(= (sibling:label godel) "Gödel")
(= (sibling:label lila) "Lila")
(= (sibling:other godel) lila)
(= (sibling:other lila) godel)
(= (sibling:marker $claw) (path:join (sibling:body $claw) "PAUSED-BY-OPERATOR.md"))

; A path with its symbolic links resolved, or as given when it does not exist.
(= (sibling:resolved $path)
   (case (fs:try-canonical-path $path) (((FS:CanonicalPath $real) $real) ($missing $path))))
; The claw whose body holds $cwd: the body itself or a directory below it.
(= (sibling:me-in $cwd)
   (let $here (sibling:resolved $cwd) (sibling:find $here (sibling:claws))))
(= (sibling:find $here $claws)
   (if (== $claws ()) none
     (let* (($claw (car-atom $claws)) ($body (sibling:resolved (sibling:body $claw))))
       (if (or (== $here $body) (str:starts-with $here (str:concat $body "/"))) $claw
         (sibling:find $here (cdr-atom $claws))))))
(= (sibling:sibling-in $cwd)
   (case (sibling:me-in $cwd) ((none none) ($me (sibling:other $me)))))

; systemctl --user ARGS: (CODE TEXT), the text its output, else its error.
(= (sibling:systemctl $args)
   (case (catch (proc:run (cons-atom "systemctl" (cons-atom "--user" $args)) (system:cwd) inherit 30000 65536))
     (((proc:exited $code $out $err) ($code (str:trim (if (== $out "") $err $out))))
      ((proc:signaled $signal $out $err) (1 (format-args "signal {}" ($signal))))
      ((proc:timed-out $out $err) (1 "TimeoutExpired: systemctl did not answer in 30 s"))
      ((proc:failed $why) (1 (str:concat "OSError: " $why)))
      ($other (1 (repr $other))))))
(= (sibling:active $claw)
   (let ($code $text) (sibling:systemctl ("is-active" (sibling:unit $claw)))
     (if (not (== $text "")) $text (if (== $code 0) "active" "unknown"))))
(= (sibling:paused? $claw) (fs:is-file (sibling:marker $claw)))
(= (sibling:failed? $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) True) ($other False))))
(= (sibling:failure-text $value)
   (case $value (((fs:failed $reason $operation $path $code $progress $message) $message) ($other (repr $value)))))

(= (sibling:status) (sibling:status-in (system:cwd)))
(= (sibling:status-in $cwd)
   (case (sibling:sibling-in $cwd)
     ((none "sibling-status failed: cannot tell which body this is")
      ($sib
       (format-args "{}: {} | {}"
         ((sibling:label $sib) (sibling:active $sib)
          (if (sibling:paused? $sib) "watchdog will NOT revive (pause marker present)"
            "watchdog revives within 30 min if down")))))))

; Pause the sibling: the marker first, so no revival races the stop.
(= (sibling:off $reason) (sibling:off-in (system:cwd) $reason))
(= (sibling:off-in $cwd $reason)
   (let $me (sibling:me-in $cwd)
     (case (sibling:sibling-in $cwd)
       ((none "sibling-off failed: cannot tell which body this is")
        ($sib
         (if (== $me $sib) "sibling-off refused: that would stop yourself — use (rest)"
           (let $written (fs:write (sibling:marker $sib) (sibling:marker-text $me $reason))
             (if (sibling:failed? $written)
               (str:concat "sibling-off failed writing pause marker: " (sibling:failure-text $written))
               (let* ((($code $out) (sibling:systemctl ("stop" (sibling:unit $sib))))
                      ($state (sibling:active $sib)))
                 (if (== $state "inactive")
                   (format-args "{} paused: service stopped, pause marker set — the watchdog will leave them down"
                                ((sibling:label $sib)))
                   (format-args "sibling-off incomplete: marker set but service is '{}' ({})" ($state $out))))))))))))
(= (sibling:marker-text $me $reason)
   (format-args "# PAUSED BY OPERATOR\n\nPaused: {}\nPaused by: {} via (sibling-off)\nReason: {}\n\nThe watchdog sees this file and will not restart the agent. To resume: use (sibling-on) from the other claw, or delete this file and start the service.\n"
     ((system:format-time "%Y-%m-%d %H:%M:%S %Z" (system:wall-time) local)
      (if (== $me none) "operator" (sibling:label $me))
      (if (== $reason "") "(none given)" $reason))))

; Resume the sibling: the marker removed first, then start, then look.
(= (sibling:on) (sibling:on-in (system:cwd)))
(= (sibling:on-in $cwd)
   (case (sibling:sibling-in $cwd)
     ((none "sibling-on failed: cannot tell which body this is")
      ($sib
       (let $removed (if (sibling:paused? $sib) (fs:remove (sibling:marker $sib)) True)
         (if (sibling:failed? $removed)
           (str:concat "sibling-on failed removing pause marker: " (sibling:failure-text $removed))
           (let* ((($code $out) (sibling:systemctl ("start" (sibling:unit $sib))))
                  ($_ (sleep (sibling:start-settle-seconds)))
                  ($state (sibling:active $sib)))
             (if (== $state "active")
               (format-args "{} resumed: pause marker cleared, service active — they will boot, read their inbox, and start their loop"
                            ((sibling:label $sib)))
               (format-args "sibling-on incomplete: marker cleared but service is '{}' ({}) — the watchdog will retry within 30 min"
                            ($state $out))))))))))
; How long a start is given before the service is looked at again.
(= (sibling:start-settle-seconds)
   (str:string->number (system:getenv-or-default "METTACLAW_SIBLING_START_SETTLE_SECONDS" "3")))

; The operator's start: it outranks a sibling's pause, so it clears this
; body's marker and starts its service when it is not active. It reports only
; what it observed afterwards.
(= (sibling:resume-self) (sibling:resume-self-in (system:cwd)))
(= (sibling:resume-self-in $cwd)
   (case (sibling:me-in $cwd)
     ((none "resume: cannot tell which body this is")
      ($me
       (let $removed (if (sibling:paused? $me) (fs:remove (sibling:marker $me)) none)
         (if (sibling:failed? $removed)
           (str:concat "resume failed removing pause marker: " (sibling:failure-text $removed))
           (let* (($cleared (if (== $removed none) () ("pause marker cleared")))
                  ($started (if (== (sibling:active $me) "active") ()
                              (let ($code $out) (sibling:systemctl ("start" (sibling:unit $me)))
                                (if (== $code 0) ((str:concat "service " (sibling:active $me)))
                                  ((format-args "service start failed ({})" ($out)))))))
                  ($notes (sibling:join $cleared $started)))
             (if (== $notes ()) "service already active" (str:join "; " $notes)))))))))
(= (sibling:join $left $right)
   (if (== $left ()) $right (cons-atom (car-atom $left) (sibling:join (cdr-atom $left) $right))))

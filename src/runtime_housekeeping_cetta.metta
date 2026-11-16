; Scheduled inputs retain stable occurrence IDs. Recycling requests a captured
; turn boundary and never restarts a process or treats uncertainty as idleness.
!(import! &self ./console/mail_cetta.metta)
!(import! &self (library proc))
(= (housekeeping:blocked $directory)
   (case (fs:metadata $directory no-follow)
    (((fs:failed fs:missing $_ $_ $_ $_ $_) 0)
     ((fs:info directory $_ $_)
      (foldl-atom (fs:entries $directory) 0 $total $name
       (+ $total (if (not (str:ends-with $name ".json")) 0
         (let $record (sf:native-object (path:join $directory $name))
           (if (== $record (JsonObject ())) 1
             (let $state (sf:get $record "state" "")
               (if (or (== $state "uncertain") (== $state "starting")) 1
                 (if (!= $state "running") 0
                   (let $pid (sf:get $record "pid" 0)
                     (if (or (!= (get-type $pid) Number) (<= $pid 0)) 1
                       (case (system:signal-probe $pid)
                         ((True 1) ((system:os-error $code $_) (if (== $code (system:errno-value "ESRCH")) 0 1))
                          ($unavailable 1))))))))))))))
     ($unavailable 1))))
(= (housekeeping:recycle $options)
   (let* (($unit (sf:get $options "--unit" "")) ($directory (sf:get $options "--jobs" ""))
          ($flag (sf:get $options "--flag" ""))
          ($active (case (proc:run ("systemctl" "--user" "is-active" "--quiet" $unit) (system:getcwd) inherit 5000 4096)
                    (((proc:exited 0 $_ $_) True) ($other False)))))
     (if (or (== $unit "") (or (== $directory "") (== $flag ""))) 78
       (let $blocked (housekeeping:blocked $directory)
         (if (or (not $active) (or (> $blocked 0) (fs:exists $flag)))
           (progn (system:write stdout (str:join "" ("recycle deferred or already requested; blocking jobs=" (repr $blocked) "\n"))) 0)
           (let* (($_ (fs:make-directories (path:dirname $flag) 448))
                  ($time (system:format-time "%Y-%m-%dT%H:%M:%SZ" (system:wall-time) "utc"))
                  ($saved (sf:replace $flag (str:join "" ("requested_at=" $time " requester=periodic-recycle\n")) 432 True)))
             (if (!= $saved True) 1
               (progn (system:write stdout "recycle requested; it runs at the next completed turn\n") 0))))))))
(= (housekeeping:review-intent $mode $at)
   (let* (($day (system:format-time "%F" $at "local"))
          ($stamp (system:format-time "%Y%m%dT%H%M%S" $at "local")))
     (if (== $mode "qualification")
       ((str:concat "godel-memory-recon-systemd-qualification-" $stamp)
        "[scheduled-memory-test] A real systemd user timer fired. This is a one-time timer qualification, not the daily review."
        "qualification-last-mail.json")
       ((str:concat "godel-memory-recon-" $day)
        (str:join "" ("[scheduled-memory-reconsolidation] Daily review due " $day
          ". If LAST RECON is not " $day ", perform the pinned memory-review procedure, record what changed, and update the review receipt. Missed firings coalesce into one review for " $day "."))
        "daily-last-mail.json"))))
(= (housekeeping:review $options)
   (let* (($mode (sf:get $options "--mode" "daily"))
          (($id $body $receipt-name) (housekeeping:review-intent $mode (system:wall-time)))
          ($mail (cc:mail-settings $options)) ($socket (sf:get $mail "socket" ""))
          ($recipient (sf:get $options "--to" "godel")) ($sender (sf:get $options "--from" "godel"))
          ($result (shared-mail:post $socket (sf:get $mail "space" "agent-mail") $id $recipient $sender $body)))
     (case $result
       (((mailbox:sent $same $status)
         (if (and (== $same $id) (or (== $status accepted) (== $status duplicate)))
           (let* (($root (sf:get $options "--state-dir" (system:expanduser "~/.local/state/godelclaw")))
                  ($_ (fs:make-directories $root 448))
                  ($saved (cm:store (path:join $root $receipt-name) (cm:receipt $result))))
             (if (== $saved True) 0 1)) 1))
        ($unconfirmed (progn (system:write stderr "scheduled input acceptance unconfirmed; retain its daily occurrence ID\n") 1))))))
(= (housekeeping:main $args)
   (if (== $args ()) 78
     (let* (($words (cli:strings $args)) ($operation (car-atom $words))
            ($options (cli:pair-args (cdr-atom $words) () (JsonObject ()))))
       (case $operation (("review" (housekeeping:review $options)) ("recycle" (housekeeping:recycle $options)) ($bad 78))))))

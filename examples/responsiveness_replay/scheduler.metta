; Explicit cooperative activities with native HTTP work outside the evaluator.
; The negative baseline keeps awaiting the slow call before servicing input.
!(import! &self io)
!(import! &self ./probe_host.py)

(= (probe:submit $name)
   (let $url (py-call (probe_host.url $name))
     (case (io:submit (http:request "GET" $url () "" 60000 1024))
       (((io:pending $id) $id)
        ($other (py-call (probe_host.fail $other)))))))

(= (probe:poll $id $label)
   (case (io:poll $id)
     (((io:event $received (http:response 200 $body))
       (if (== $received $id)
         (let $_ (py-call (probe_host.completed $label $id $body)) True)
         (py-call (probe_host.fail $received))))
      ((io:idle) False)
      ($other (py-call (probe_host.fail $other))))))

; A slot returns its continuation. It never recursively waits for I/O.
(= (probe:slot 0 (probe:state $slow $sdone $fast $fdone $tick $seen))
   (if $sdone
     (probe:state $slow $sdone $fast $fdone $tick $seen)
     (let $done (probe:poll $slow "slow-completed")
       (probe:state $slow $done $fast $fdone $tick $seen))))

(= (probe:slot 1 (probe:state $slow $sdone $fast $fdone $tick $seen))
   (if $seen
     (if $fdone
       (probe:state $slow $sdone $fast $fdone $tick $seen)
       (let $done (probe:poll $fast "short-completed")
         (probe:state $slow $sdone $fast $done $tick $seen)))
     (let $now (py-call (probe_host.elapsed_ms))
       (if (>= $now (py-call (probe_host.input_ms)))
         (let* (($_ (py-call (probe_host.record "input-noticed" 0)))
                ($id (probe:submit "short")))
           (probe:state $slow $sdone $id False $tick True))
         (probe:state $slow $sdone $fast $fdone $tick $seen)))))

(= (probe:slot 2 (probe:state $slow $sdone $fast $fdone $tick $seen))
   (let $now (py-call (probe_host.elapsed_ms))
     (if (>= $now $tick)
       (let* (($_ (py-call (probe_host.record "alphabet" $tick)))
              ($_ (println! "abcdefghijklmnopqrstuvwxyz"))
              ($next (+ $tick (py-call (probe_host.period_ms)))))
         (probe:state $slow $sdone $fast $fdone $next $seen))
       (probe:state $slow $sdone $fast $fdone $tick $seen))))

(= (probe:next 0) 1)
(= (probe:next 1) 2)
(= (probe:next 2) 0)

(= (probe:pump $rotor $state)
   (let $next (probe:slot $rotor $state)
     (case $next
       (((probe:state $slow True $fast True $tick True)
         (py-call (probe_host.finish)))
        ($pending
         (let $_ (py-call (probe_host.pause))
           (probe:pump (probe:next $rotor) $pending)))))))

; Same nonblocking transport, but a synchronous caller monopolizes cognition.
(= (probe:await-slow $id)
   (if (probe:poll $id "slow-completed")
     True
     (let $_ (py-call (probe_host.pause)) (probe:await-slow $id))))

!(let* (($mode (py-call (probe_host.mode)))
        ($_ (py-call (probe_host.start $mode)))
        ($slow (probe:submit "slow"))
        ($period (py-call (probe_host.period_ms))))
   (if (or (== $mode blocking) (== $mode "blocking"))
     (let $_ (probe:await-slow $slow)
       (probe:pump 1 (probe:state $slow True 0 False $period False)))
     (probe:pump 0 (probe:state $slow False 0 False $period False))))

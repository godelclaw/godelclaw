; Progress receipts and the service manager's watchdog, natively, as
; src/process_supervision.py keeps them for the host. Only real progress
; writes a receipt: the loop, a completed command, a provider's data. A
; receipt names the phase reached, the end of the wait it owns (0 for none,
; on the monotonic clock in seconds) and when it was written; each renews the
; watchdog, extended by the wait it still owns, so polling a wait never renews
; its deadline. runtime_health reads the receipt file.
;
;   (progress:phase PHASE)                a receipt with no owned wait
;   (progress:provider-wait MILLISECONDS) a submitted call's wait
;   (progress:provider-data BYTES)        more of its answer arrived
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./state_file_cetta.metta)

; (ProgressV1 sequence owned-wait-end-ns provider-bytes owner): this process's
; receipt count, the end of the wait it owns (0 for none), the bytes of the
; provider's answer reported so far, and its owner, pid:start ("" until read).
!(bind! &progress (new-state (ProgressV1 0 0 0 "")))

(= (progress:path)
   (let* (($explicit (system:getenv-or-default "METTACLAW_PROGRESS_PATH" ""))
          ($state (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH" "")))
     (if (== $explicit "") (if (== $state "") "" (path:join (path:dirname $state) "progress.json")) $explicit)))
; The process identity a reader checks for liveness: pid and start time.
(= (progress:owner $known)
   (if (not (== $known "")) $known
     (let $pid (repr (system:pid))
       (case (fs:read-text-result "/proc/self/stat" 0 "raw")
         ((("ok" $stat)
           (let $fields (str:split-whitespace (sf:last (str:split ")" $stat)))
             (if (> (size-atom $fields) 19) (str:join "" ($pid ":" (progress:nth $fields 19))) (str:concat $pid ":portable"))))
          ($unreadable (str:concat $pid ":portable")))))))
(= (progress:nth $items $index) (if (== $index 0) (car-atom $items) (progress:nth (cdr-atom $items) (- $index 1))))
(= (progress:seconds $ns) (/ $ns 1000000000.0))

; The watchdog's renewal: WATCHDOG_USEC is the service's interval, extended by
; the wait still owned; nothing is sent without both a watchdog and a manager.
; 1 when sent.
(= (progress:watchdog-usec)
   (case (catch (str:string->number (system:getenv-or-default "WATCHDOG_USEC" "0")))
     (((Error $call $why) 0) ($usec (if (== (get-type $usec) Number) (max 0 $usec) 0)))))
; The renewal for a watchdog of BASE microseconds at NOW (ns), or () for none.
(= (progress:watchdog-message $base $deadline $now)
   (if (== $base 0) ()
     (let $usec (+ $base (if (> $deadline $now) (math:ceil (/ (- $deadline $now) 1000.0)) 0))
       (str:join "" ("WATCHDOG_USEC=" (repr $usec) "\nWATCHDOG=1")))))
(= (progress:notify $deadline)
   (let $message (progress:watchdog-message (progress:watchdog-usec) $deadline (system:monotonic-ns))
     (if (== $message ()) 0 (case (system:notify $message) ((True 1) ($unsent 0))))))

; Record PHASE with the owned wait ending at DEADLINE (monotonic ns, 0 for
; none), then renew the watchdog. A receipt that cannot be written still
; renews it: observation storage never undoes progress already made.
(= (progress:receipt $phase $deadline $bytes)
   (let* (((ProgressV1 $sequence $held $reported $known) (get-state &progress))
          ($owner (progress:owner $known))
          ($next (+ $sequence 1))
          ($_ (change-state! &progress (ProgressV1 $next $deadline $bytes $owner)))
          ($path (progress:path))
          ($written (if (== $path "") False (progress:write $path $owner $next $phase $deadline))))
     (let $notified (progress:notify $deadline) 1)))
(= (progress:write $path $owner $sequence $phase $deadline)
   (let* (($now (system:monotonic-ns))
          ($receipt (JsonObject ((JsonPair "pid" (system:pid)) (JsonPair "owner" $owner) (JsonPair "sequence" $sequence)
                                 (JsonPair "phase" $phase) (JsonPair "progress_at" (system:wall-time))
                                 (JsonPair "wait_until" (if (== $deadline 0) 0 (progress:seconds $deadline)))
                                 (JsonPair "progress_clock" (progress:seconds $now)) (JsonPair "memory_bytes" JsonNull))))
          ($made (fs:make-directories (path:dirname $path) 511)))
     (sf:replace $path (json:stringify-python-with $receipt ((separators "," ":"))) 384 False)))

(= (progress:phase $phase)
   (let (ProgressV1 $sequence $held $reported $known) (get-state &progress)
     (progress:receipt $phase 0 $reported)))
; A submitted call owns its wait to the call's deadline; the answer's bytes
; count again from none.
(= (progress:provider-wait $milliseconds)
   (progress:receipt "provider-wait" (+ (system:monotonic-ns) (* $milliseconds 1000000)) 0))
; More of the answer: a receipt that keeps the call's deadline. A poll that
; brought nothing new writes none.
(= (progress:provider-data $bytes)
   (let (ProgressV1 $sequence $held $reported $known) (get-state &progress)
     (if (<= $bytes $reported) 1 (progress:receipt "provider-data" $held $bytes))))

; Unit tests of src/progress_receipt_cetta.metta. The driver sets
; METTACLAW_PROGRESS_PATH and WATCHDOG_USEC=1000000; no service manager listens.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/progress_receipt_cetta.metta)

(= (pr:receipt) (json:parse-python (fs:read-text (progress:path))))
(= (pr:field $key) (json:get (pr:receipt) $key))

; The renewal names the service's interval, extended by the wait still owned
; in whole microseconds, rounded up; no watchdog, no renewal.
!(nt:require watchdog-messages
   (nt:equal (map-atom ((0 0 5) (1000000 0 5) (1000000 20000000005 5) (1000000 1505 5) (1000000 4 5)) $case
               (let ($base $deadline $now) $case (progress:watchdog-message $base $deadline $now)))
             (() "WATCHDOG_USEC=1000000\nWATCHDOG=1" "WATCHDOG_USEC=21000000\nWATCHDOG=1"
              "WATCHDOG_USEC=1000002\nWATCHDOG=1" "WATCHDOG_USEC=1000000\nWATCHDOG=1")))
!(nt:require no-manager-no-renewal (nt:equal (progress:notify 0) 0))

!(let* (($_ (nt:require provider-wait-written (nt:equal (progress:provider-wait 20000) 1)))
        ($first (pr:receipt))
        ($until (json:get $first "wait_until")) ($clock (json:get $first "progress_clock"))
        ($_ (nt:require provider-wait-receipt
              (nt:equal ((json:get $first "phase") (json:get $first "sequence") (json:get $first "pid") (json:get $first "memory_bytes"))
                        ("provider-wait" 1 (system:pid) JsonNull))))
        ($_ (nt:require owned-wait-twenty-seconds (and (> $until (+ $clock 19.9)) (<= $until (+ $clock 20.0)))))
        ($_ (nt:require owner-is-this-process (str:starts-with (json:get $first "owner") (str:concat (repr (system:pid)) ":"))))
        ($_ (nt:require owner-has-start-time (not (str:ends-with (json:get $first "owner") ":portable"))))
        ; More data keeps the call's deadline; a poll with nothing new writes nothing.
        ($_ (progress:provider-data 10))
        ($_ (nt:require data-keeps-deadline
              (nt:equal ((pr:field "phase") (pr:field "sequence") (pr:field "wait_until")) ("provider-data" 2 $until))))
        ($_ (progress:provider-data 10))
        ($_ (nt:require repeated-data-no-receipt (nt:equal (pr:field "sequence") 2)))
        ($_ (progress:provider-data 20))
        ($_ (nt:require new-data-receipt (nt:equal (pr:field "sequence") 3)))
        ; Back to computation: no owned wait.
        ($_ (progress:phase "command-receipt"))
        ($_ (nt:require phase-has-no-wait (nt:equal ((pr:field "phase") (pr:field "wait_until") (pr:field "sequence")) ("command-receipt" 0 4))))
        ($_ (progress:provider-data 25))
        ($_ (nt:require data-without-a-wait (nt:equal ((pr:field "phase") (pr:field "wait_until")) ("provider-data" 0))))
        ; A new submission counts the answer's bytes from none.
        ($_ (progress:provider-wait 1000))
        ($_ (progress:provider-data 5))
        ($_ (nt:require bytes-count-again (nt:equal ((pr:field "phase") (pr:field "sequence")) ("provider-data" 7)))))
   (println! UNIT_PROGRESS_RECEIPT_OK))

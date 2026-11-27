; Provider admission keeps accounts without imposing work quotas: many
; requests to one provider are all admitted and counted; a request left
; uncertain by an owner that exited never blocks a new one with the same
; fingerprint; a provider's Retry-After is honored to the second, with no
; floor added; a failure before sending sets no cooldown.
!(import! &self ../native_test_support.metta)
!(import! &self ../native_ledger_support.metta)
!(import! &self ../../src/llm_policy_cetta.metta)

!(bind! &la-tickets (new-state 0))
(= (la:ticket)
   (let $n (+ 1 (get-state &la-tickets))
     (let $_ (change-state! &la-tickets $n) (str:concat "ticket-" (repr $n)))))
(= (la:admit $provider $fingerprint $now)
   (let $ticket (la:ticket)
     (case (lp:admit $ticket $provider 100 $fingerprint $now)
       (((LedgerAdmitted $t) $t) ($refused (Refused $refused))))))
(= (la:admitted? $answer) (== (get-type $answer) String))
; How many of this provider's requests are in each status.
(= (la:count $provider $status)
   (size-atom (filter-atom (lp:native "llm-records" ("requests")) $row
     (case $row (((llm:request $id $t $max $s $o $p $m $f $ow $fp) (and (== $p $provider) (== $s $status))) ($x False))))))

; The ledger's handle is held for the whole unit: an owned process whose
; handle is dropped is reclaimed.
!(bind! &la-ledger (new-state none))
!(let* (($started (nls:start)) ($_ (change-state! &la-ledger $started))
        ($_ (nt:require native-ledger-ready (nls:up? $started)))
        ($many (map-atom (1 2 3 4 5 6) $n (la:admit "fixture" "" 100))))
   (nt:require many-requests-are-admitted-without-an-invented-quota
     (and (== (filter-atom $many $t (not (la:admitted? $t))) ())
     (and (== (size-atom (unique-atom $many)) 6)
          (== (la:count "fixture" "pending") 6)))))

!(let* (($old (la:ticket))
        ($_ (lp:native "llm-admit" ($old "uncertain" 100 "same" "managed" "godelclaw" "0:0" "{}" 100 (lp:request-id))))
        ($new (la:admit "uncertain" "same" 101)))
   (nt:require an-uncertain-old-request-does-not-block-a-new-one
     (and (la:admitted? $new)
     (and (not (== $new $old))
     (and (== (la:count "uncertain" "pending") 1)
          (== (la:count "uncertain" "unknown") 1))))))

!(let* (($first (la:admit "limited" "" 100))
        ($_ (lp:fail $first True 429 2 100))
        ($denied (la:admit "limited" "" 101))
        ($later (la:admit "limited" "" 102)))
   (nt:require retry-after-is-honored-without-a-floor
     (and (case $denied (((Refused (LedgerDenied $why $wait)) (== $wait 1)) ($o False)))
          (la:admitted? $later))))

!(let* (($failures (map-atom (1 2 3 4 5) $n
          (let $ticket (la:admit "internal" "" 100) (lp:fail $ticket False JsonNull JsonNull 100))))
        ($after (la:admit "internal" "" 100)))
   (nt:require a-failure-before-sending-sets-no-cooldown (la:admitted? $after)))

!(println! UNIT_LLM_ADMISSION_OK)

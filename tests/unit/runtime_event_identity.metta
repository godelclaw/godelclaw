; Runtime events are occurrences, never identified by their text: the same
; text under two identities is two events; one identity offered twice is one
; event, with its wake reason; once the ledger records it as processed, a
; new process offering it again spools nothing.
!(import! &self ../native_test_support.metta)
!(import! &self ../native_ledger_support.metta)
!(import! &self ../../src/runtime_events_cetta.metta)
!(import! &self ../../src/ledger_client.metta)
!(import! &self (library json))

(= (re-id:health) (path:join (nt:required-env "HOME") "telegram/health.json"))
(= (re-id:spooled) (map-atom (rtev:names (rtev:spool (re-id:health))) $name
                     (json:parse (fs:read-text (path:join (rtev:spool (re-id:health)) $name)))))
(= (re-id:clear) (map-atom (rtev:names (rtev:spool (re-id:health))) $name
                   (fs:remove (path:join (rtev:spool (re-id:health)) $name))))

!(bind! &re-id-ledger (new-state none))
!(let* (($started (nls:start)) ($_ (change-state! &re-id-ledger $started))
        ($_ (nt:require native-ledger-ready (nls:up? $started)))
        ($_ (rtev:enqueue (re-id:health) "runtime:job:one" "same text" "runtime event"))
        ($_ (rtev:enqueue (re-id:health) "runtime:job:two" "same text" "runtime event"))
        ($ids (map-atom (re-id:spooled) $item (json:get $item "update_id"))))
   (nt:require identical-text-is-two-occurrences
     (and (== (size-atom $ids) 2) (== (size-atom (unique-atom $ids)) 2))))

!(let* (($_ (re-id:clear))
        ($identity "runtime:model-call:receipt-1:late")
        ($_ (rtev:enqueue (re-id:health) $identity "retained receipt" "runtime event"))
        ($_ (rtev:enqueue (re-id:health) $identity "retained receipt" "runtime event"))
        ($items (re-id:spooled))
        ($_ (nt:require one-identity-offered-twice-is-one-event
              (and (== (size-atom $items) 1) (== (json:get (car-atom $items) "wake") "runtime event"))))
        ; Cognition records it processed; a new process's spool is empty.
        ($_ (ledger-client:call (nls:socket) "mark-processed" ((system:wall-time) ($identity))))
        ($_ (re-id:clear))
        ($_ (rtev:enqueue (re-id:health) $identity "retained receipt" "runtime event")))
   (nt:require a-processed-occurrence-is-never-spooled-again (== (re-id:spooled) ())))

!(println! UNIT_RUNTIME_EVENT_IDENTITY_OK)

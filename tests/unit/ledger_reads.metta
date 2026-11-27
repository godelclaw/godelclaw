; Two reads the agent takes from its native ledger. Topic notes belong to
; their topic: selecting another hides them, selecting it again shows them,
; and they survive the ledger's restart. The committed read reports exactly
; the inputs cognition marked processed.
!(import! &self ../native_test_support.metta)
!(import! &self ../native_ledger_support.metta)
!(import! &self ../../src/state_ledger.metta)
!(import! &self ../../src/activity_source_cetta.metta)
!(import! &self (library json))

(= (lr:active)
   (json:get (json:parse (topics)) "active"))

!(let* (($first (nls:start))
        ($_ (nt:require native-ledger-ready (nls:up? $first)))
        ($_ (topic-select "research"))
        ($_ (topic-note "finding from source"))
        ($_ (nt:require the-topic-shows-its-notes (nt:contains (topics:context) "finding from source")))
        ($_ (topic-select "other"))
        ($_ (nt:require another-topic-hides-them (== (topics:context) "")))
        ($_ (nls:stop $first))
        ($second (nls:start))
        ($_ (nt:require the-ledger-restarts (nls:up? $second)))
        ($_ (topic-select "research"))
        ($_ (nt:require notes-and-selection-survive-a-restart
              (and (nt:contains (topics:context) "finding from source") (== (lr:active) "research"))))
        ($marked (ledger-client:call (nls:socket) "mark-processed" ((system:wall-time) ("one")))))
   (nt:require the-committed-read-reports-exactly-the-processed-ids
     (and (== (activity-source:already-committed "one") 1)
          (== (activity-source:already-committed "two") 0))))

!(println! UNIT_LEDGER_READS_OK)

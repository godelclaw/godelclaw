!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/omega/native/bootstrap.metta)
; Omega's own HISTORY, as its loop asks for it, beside another mode's record:
; foreign bytes where a recorded append should stand are hidden and do not
; hold Omega; a torn append is completed from its record and shown once; a
; record that cannot be read holds Omega before it reads, and an append that
; can never be verified does not. Should a record become unreadable during a turn, the
; view ends where it last ended verified, or says the history is unavailable;
; Omega appends while the history is the file, at the size, it last verified,
; and waits once anything else was written.
(= (view:intent-path) (path:join (path:join (path:dirname (mode-host:root)) "iter") "agent-history-intent.json"))
(= (view:record $status $offset $payload)
   (case (mode-host:file-info (mode-history:path))
     (((ModeFileV1 $_ $device $inode)
       (turn:json (JsonObject ((JsonPair "status" $status) (JsonPair "device" $device) (JsonPair "inode" $inode)
                               (JsonPair "offset" $offset) (JsonPair "payload" $payload)
                               (JsonPair "sha256" (str:sha256 $payload)))))))))
(= (view:bare $status $offset)
   (case (mode-host:file-info (mode-history:path))
     (((ModeFileV1 $_ $device $inode)
       (turn:json (JsonObject ((JsonPair "status" $status) (JsonPair "device" $device) (JsonPair "inode" $inode)
                               (JsonPair "offset" $offset))))))))
(= (view:append $path $text)
   (let $owner (fs:open-write $path ("create" "append") 384)
     (let* (($_ (fs:write-all $owner $text)) ($_ (fs:close $owner))) True)))
(= (view:count $text $needle) (- (size-atom (str:split $needle $text)) 1))
!(let* (($history (mode-history:path))
        ($_ (nt:require shared-history (!= $history "")))
        ($_ (nt:require missing-history-is-empty (== (omega-host:history-view $history 600) "")))
        ($_ (fs:make-directories (mode-host:root) 448))
        ($_ (fs:make-directories (path:dirname (view:intent-path)) 448))
        ($_ (fs:write $history "(\"a\" \"\n\" \"SETTLED_VIEW_TEXT\" \"\n\")\n"))
        ($_ (omega-host:configure maxHistory 600))
        ; Other bytes where a recorded append should stand: hidden, and Omega goes on.
        ($at (mode-history:size $history))
        ($_ (view:append $history "(\"b\" \"\n\" \"FOREIGN_VIEW_TEXT\" \"\n\")\n"))
        ($_ (fs:write (view:intent-path) (view:record "pending" $at "(\"b\" \"\n\" \"ANOTHER_PAYLOAD\" \"\n\")\n")))
        ($_ (nt:require foreign-does-not-hold-omega (== (omega-host:history-ready) True)))
        ($first (getHistory))
        ($_ (nt:require history-hides-foreign
              (and (nt:contains $first "SETTLED_VIEW_TEXT") (not (nt:contains $first "FOREIGN_VIEW_TEXT")))))
        ($_ (fs:write (view:intent-path) (view:record "settled" $at "(\"b\" \"\n\" \"ANOTHER_PAYLOAD\" \"\n\")\n")))
        ($_ (nt:require history-shows-settled (nt:contains (getHistory) "FOREIGN_VIEW_TEXT")))
        ; A torn append: completed from its record before Omega reads, then shown once.
        ($torn "(\"c\" \"\n\" \"TORN_COMPLETED_TEXT\" \"\n\")\n")
        ($_ (fs:write (view:intent-path) (view:record "pending" (mode-history:size $history) $torn)))
        ($_ (view:append $history (str:byte-slice $torn 0 8)))
        ($verified (getHistory))
        ($_ (nt:require torn-completed-and-shown (nt:contains $verified "TORN_COMPLETED_TEXT")))
        ($_ (nt:require torn-written-once (== (view:count (fs:read-text $history) "TORN_COMPLETED_TEXT") 1)))
        ; Not readable, or not verifiable: Omega is held before it reads.
        ($_ (fs:write (view:intent-path) "{not json"))
        ($_ (nt:require unreadable-holds-omega (!= (omega-host:history-ready) True)))
        ; During a turn the view ends where it last ended verified...
        ($_ (nt:require unreadable-keeps-verified-end (== (omega-host:history-view $history 600) $verified)))
        ; ... and with no verified end in this process, says so.
        ($_ (change-state! &omega-history-end none))
        ($_ (nt:require unreadable-without-verified-end
              (str:starts-with (omega-host:history-view $history 600) "[The agent history is not shown this turn")))
        ($_ (nt:require unchanged-history-appends
              (== (omega-host:append-history "(\"e\" \"\n\" \"OWN_AFTER_VERIFIED\" \"\n\")") "APPEND-FILE-SUCCESS")))
        ($_ (nt:require own-append-keeps-writable (== (omega-host:append-safe) True)))
        ($_ (view:append $history "(\"f\" \"\n\" \"WRITTEN_SINCE\" \"\n\")\n"))
        ($_ (nt:require changed-history-waits (!= (omega-host:append-safe) True)))
        ($_ (fs:write (view:intent-path) (view:bare "pending" (mode-history:size $history))))
        ($_ (nt:require unverifiable-does-not-hold-omega (== (omega-host:history-ready) True))))
   (println! UNIT_OMEGA_VIEW_PENDING_OK))

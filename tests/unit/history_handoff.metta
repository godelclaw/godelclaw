!(import! &self ../native_test_support.metta)
!(import! &self ../../modes/omega/native/bootstrap.metta)
; An older mode's torn append, then a later writer, then the older one's
; recovery: the later writer completes the older payload from its record
; before its own, the bytes stand in order, the owner's recovery verifies
; them, and nothing is written twice. An append that can never complete
; (other bytes at its place, a place past the end, a payload that fails its
; digest) is written by no one, its owner included, and holds no writer.
; A completion that cannot be confirmed is not reported as one.
(= (handoff:append $path $text)
   (let $owner (fs:open-write $path ("create" "append") 384)
     (let* (($_ (fs:write-all $owner $text)) ($_ (fs:close $owner))) True)))
(= (handoff:intent-path) (path:join (path:join (path:dirname (mode-host:root)) "iter") "agent-history-intent.json"))
(= (handoff:intent $offset $payload)
   (case (mode-host:file-info (mode-history:path))
     (((ModeFileV1 $_ $device $inode)
       (sf:replace (handoff:intent-path)
         (turn:json (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "pending") (JsonPair "path" (mode-history:path))
                                 (JsonPair "payload" $payload) (JsonPair "sha256" (str:sha256 $payload))
                                 (JsonPair "offset" $offset) (JsonPair "device" $device) (JsonPair "inode" $inode)
                                 (JsonPair "from" 0) (JsonPair "frontier" 1) (JsonPair "lineage" "unit"))))
         384 True)))))
(= (handoff:intent-at $path $offset $payload $digest)
   (case (mode-host:file-info (mode-history:path))
     (((ModeFileV1 $_ $device $inode)
       (sf:replace $path
         (turn:json (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "pending") (JsonPair "path" (mode-history:path))
                                 (JsonPair "payload" $payload) (JsonPair "sha256" $digest)
                                 (JsonPair "offset" $offset) (JsonPair "device" $device) (JsonPair "inode" $inode))))
         384 True)))))
(= (handoff:identity)
   (case (mode-host:file-info (mode-history:path)) (((ModeFileV1 $size $device $inode) ($size $device $inode)))))
!(let* (($history (mode-history:path))
        ($root (path:dirname (mode-host:root)))
        ($_ (fs:make-directories (mode-host:root) 448))
        ($_ (fs:make-directories (path:join $root "iter") 448))
        ($base "(\"t0\" \"\n\" \"BASE_RECORD\" \"\n\")\n")
        ($_ (fs:write $history $base))
        ($older "(\"t1\" \"\n\" \"OLDER_ITER_RECORD\" \"\n\")\n")
        ($_ (nt:require older-intent-durable (== (handoff:intent (str:byte-length $base) $older) True)))
        ($_ (handoff:append $history (str:byte-slice $older 0 9)))
        ($later "(\"t2\" \"\n\" \"LATER_OMEGA_RECORD\" \"\n\")")
        ($_ (nt:require later-writer-appends (== (omega-host:append-history $later) "APPEND-FILE-SUCCESS")))
        ($bytes (fs:read-text $history))
        ($_ (nt:require bytes-in-order (== $bytes (str:join "" ($base $older $later "\n")))))
        ($_ (nt:require owner-recovery-verifies (== (mode-host:resume-append (sf:native-object (handoff:intent-path))) True)))
        ($_ (nt:require nothing-written-twice (== (fs:read-text $history) $bytes)))
        (($size $device $inode) (handoff:identity))
        ($_ (nt:require whole-append-reads-as-settled (== (history-pending:end $root $history $device $inode $size) $size)))
        ; Still pending though whole: synced, then a writer goes on.
        ($_ (nt:require whole-pending-synced-for-writers (== (history-pending:complete $root $history $device $inode) True)))
        ; Other bytes where a recorded append should continue: that append was
        ; lost before and can never complete. Nothing is written for it, a
        ; writer goes on, and readers stop before the unverified bytes.
        ($_ (nt:require second-intent-durable (== (handoff:intent $size "(\"t3\" \"\n\" \"NEVER_WRITTEN\" \"\n\")\n") True)))
        ($_ (handoff:append $history "XXXX"))
        ($_ (nt:require foreign-bytes-do-not-block-writers (== (history-pending:complete $root $history $device $inode) True)))
        ($_ (nt:require foreign-bytes-not-completed (== (fs:read-text $history) (str:concat $bytes "XXXX"))))
        ($_ (nt:require readers-stop-before-unverified-bytes
              (== (history-pending:end $root $history $device $inode (+ $size 4)) $size)))
        ; A payload that does not match its recorded digest can never be
        ; completed: nothing is written from it and history stays as it is;
        ; writers go on; readers stop at it until its owner abandons it.
        ($at (+ $size 4))
        ($unsure "(\"t4\" \"\n\" \"DIGEST_MISMATCH_RECORD\" \"\n\")\n")
        ($_ (fs:remove (handoff:intent-path)))
        ($_ (nt:require mismatch-intent-durable
              (== (handoff:intent-at (mode-history:intent-path) $at $unsure
                    "0000000000000000000000000000000000000000000000000000000000000000") True)))
        ($_ (handoff:append $history (str:byte-slice $unsure 0 6)))
        ($before (fs:read-text $history))
        ($_ (nt:require mismatch-does-not-block-writers (== (history-pending:complete $root $history $device $inode) True)))
        ($_ (nt:require mismatch-not-completed (== (fs:read-text $history) $before)))
        ($_ (nt:require mismatch-stops-readers
              (== (history-pending:end $root $history $device $inode (str:byte-length $before)) $at)))
        ; Its owner writes nothing from it either, and abandons it.
        ($owned (mode-history:complete (sf:native-object (mode-history:intent-path))))
        ($_ (nt:require mismatch-owner-writes-nothing
              (and (== $owned ModeIntentAbandonedV1) (== (fs:read-text $history) $before))))
        ($_ (nt:require abandoned-mismatch-does-not-stop-readers
              (== (history-pending:end $root $history $device $inode (str:byte-length $before)) (str:byte-length $before))))
        ; A place past the file's end: lost before; writers go on, nothing written.
        ($past (+ (str:byte-length $before) 100))
        ($_ (nt:require past-end-intent-durable (== (handoff:intent $past "(\"t5\" \"\n\" \"PAST_END\" \"\n\")\n") True)))
        ($_ (nt:require past-end-does-not-block-writers (== (history-pending:complete $root $history $device $inode) True)))
        ($_ (nt:require past-end-not-written (== (fs:read-text $history) $before)))
        ; The history now ends inside a line, in the abandoned fragment: the
        ; next record starts on a line of its own, the fragment as it was.
        ($_ (nt:require record-after-fragment-written
              (== (omega-host:append-history "(\"t6\" \"\n\" \"AFTER_FRAGMENT\" \"\n\")") "APPEND-FILE-SUCCESS")))
        ($_ (nt:require record-after-fragment-on-own-line
              (str:starts-with (fs:read-text $history) (str:concat $before "\n(\"t6\"")))))
   (println! UNIT_HISTORY_HANDOFF_OK))
; A loop that reads the history first completes a torn foreign append, and
; shows it: Omega's own HISTORY.
!(let* (($history (mode-history:path))
        ($_ (fs:write $history "(\"u0\" \"\n\" \"VIEW_BASE\" \"\n\")\n"))
        ($older "(\"u1\" \"\n\" \"TORN_THEN_COMPLETED\" \"\n\")\n")
        ($_ (handoff:intent (mode-history:size $history) $older))
        ($_ (handoff:append $history (str:byte-slice $older 0 11)))
        ($_ (omega-host:configure maxHistory 4000))
        ($_ (change-state! &omega-history-end none))
        ($view (getHistory))
        ($_ (nt:require view-completes-and-shows (nt:contains $view "TORN_THEN_COMPLETED")))
        ($_ (nt:require file-completed-once
              (== (fs:read-text $history) (str:concat "(\"u0\" \"\n\" \"VIEW_BASE\" \"\n\")\n" $older)))))
   (println! UNIT_HISTORY_VIEW_COMPLETES_OK))
; A completion is reported only once every byte is written and synced, after
; one attempt: a write that fails (/dev/full) and a sync that fails (/dev/null)
; each answer incomplete rather than send recovery round again.
(= (handoff:device-intent $root $device-path $payload)
   (case (fs:metadata $device-path follow-observed)
     (((fs:info-v2 $_ $_ (fs:identity $device $inode) $_ $_ $_ $_)
       (let* (($_ (fs:make-directories (path:join $root "iter") 448))
              ($_ (sf:replace (path:join (path:join $root "iter") "agent-history-intent.json")
                    (turn:json (JsonObject ((JsonPair "schema" 1) (JsonPair "status" "pending") (JsonPair "path" $device-path)
                      (JsonPair "payload" $payload) (JsonPair "sha256" (str:sha256 $payload))
                      (JsonPair "offset" 0) (JsonPair "device" $device) (JsonPair "inode" $inode))))
                    384 True)))
         ($device $inode))))))
(= (handoff:error? $value) (case $value (((Error $_ $_) True) ($other False))))
!(let* (($payload "(\"v1\" \"\n\" \"FAULT_RECORD\" \"\n\")\n")
        ($full-root (path:join (path:dirname (mode-host:root)) "faults-full"))
        (($full-device $full-inode) (handoff:device-intent $full-root "/dev/full" $payload))
        ($_ (nt:require failed-write-answers-incomplete
              (handoff:error? (history-pending:complete $full-root "/dev/full" $full-device $full-inode))))
        ($null-root (path:join (path:dirname (mode-host:root)) "faults-null"))
        (($null-device $null-inode) (handoff:device-intent $null-root "/dev/null" $payload))
        ($_ (nt:require failed-sync-answers-incomplete
              (handoff:error? (history-pending:complete $null-root "/dev/null" $null-device $null-inode))))
        ($_ (nt:require append-reports-failed-write (handoff:error? (history-pending:append "/dev/full" "abc"))))
        ($_ (nt:require append-reports-failed-sync (handoff:error? (history-pending:append "/dev/null" "abc"))))
        ($_ (nt:require sync-reports-failure (handoff:error? (history-pending:sync "/dev/null"))))
        ($_ (nt:require sync-confirms-file (== (history-pending:sync (mode-history:path)) True)))
        ; A new record's line: a break first only when the file ends inside one.
        ($lines (path:join (path:dirname (mode-host:root)) "lines.metta"))
        ($_ (nt:require missing-file-takes-payload (== (history-pending:on-own-line $lines "R\n") "R\n")))
        ($_ (fs:write $lines ""))
        ($_ (nt:require empty-file-takes-payload (== (history-pending:on-own-line $lines "R\n") "R\n")))
        ($_ (fs:write $lines "(\"a\")\n"))
        ($_ (nt:require line-end-takes-payload (== (history-pending:on-own-line $lines "R\n") "R\n")))
        ($_ (fs:write $lines "(\"a\")\n(\"frag"))
        ($_ (nt:require mid-line-gets-line-break (== (history-pending:on-own-line $lines "R\n") "\nR\n"))))
   (println! UNIT_HISTORY_APPEND_FAULTS_OK))

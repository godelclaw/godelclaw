; The agent's history file (METTACLAW_HISTORY_PATH) is written by every loop:
; this framework loop's turns, and a supervised mode's (Omega, Iter) while this
; loop is not running. This loop's prompt is built from the ledger's
; conversation, so what another loop wrote there enters that conversation: at
; the start of a turn, the bytes after this loop's cursor become one settled
; request, before the turn's own input.
;
; The cursor (ledger meta agent_history_cursor) is the file's identity and the
; size after the last byte this loop has accounted for: its own record, or an
; import. Bytes are taken by position, never by content, so equal records are
; each kept. An import's request is named by its first byte: a repeated import
; finds its own request and moves the cursor to the end that request recorded,
; so nothing is shown twice and a failed write is retried at the next turn.
!(import! &self ./state_ledger.metta)
!(bind! &agent-history-cursor (new-state unread))

(= (agent-history:failed? $value)
   (case $value (((Error $_ $_) True) ((fs:failed $_ $_ $_ $_ $_ $_) True) ($other False))))
(= (agent-history:enabled?)
   (and (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1") (state:native-ledger)))
(= (agent-history:path) (system:getenv-or-default "METTACLAW_HISTORY_PATH" "./memory/history.metta"))
(= (agent-history:key) "agent_history_cursor")
; Of a long gap, the newest bytes are shown; the rest stays in the file.
(= (agent-history:limit) 30000)

(= (agent-history:file)
   (case (fs:metadata (agent-history:path) follow-observed)
     (((fs:info-v2 regular $size (fs:identity $device $inode) $_ $_ $_ $_)
       (AgentHistoryFileV1 (repr $device) (repr $inode) $size))
      ($missing AgentHistoryMissingV1))))
(= (agent-history:record (AgentHistoryFileV1 $device $inode $size))
   (state:encoded (JsonObject ((JsonPair "path" (JsonString (agent-history:path)))
                               (JsonPair "device" (JsonString $device)) (JsonPair "inode" (JsonString $inode))
                               (JsonPair "size" (JsonNumber (repr $size)))))))
; The cursor as (AgentHistoryCursorV1 <raw text> <device> <inode> <size>), or
; none when there is none for this file.
(= (agent-history:number $value)
   (case $value (((JsonNumber $text) (str:string->number $text)) ($other -1))))
(= (agent-history:parse $raw)
   (case (catch (json-parse $raw))
     (((JsonObject $pairs)
       (if (== (state:json-member $pairs "path") (JsonString (agent-history:path)))
         (AgentHistoryCursorV1 $raw (state:text (state:json-member $pairs "device"))
           (state:text (state:json-member $pairs "inode")) (agent-history:number (state:json-member $pairs "size")))
         none))
      ($bad none))))
(= (agent-history:cursor-raw)
   (let $cached (get-state &agent-history-cursor)
     (if (!= $cached unread) $cached
       (let $raw (state:meta-text (agent-history:key))
         (if (agent-history:failed? $raw) $raw
           (let $_ (change-state! &agent-history-cursor $raw) $raw))))))
; Moves the cursor from <raw> (none: absent) to <file>'s identity at <size>.
(= (agent-history:move $raw $file $size)
   (let* (((AgentHistoryFileV1 $device $inode $_) $file)
          ($next (agent-history:record (AgentHistoryFileV1 $device $inode $size))))
     (case (state:ledger "meta-cas" ((agent-history:key) $raw $next (state:request-id)))
       ((published (let $_ (change-state! &agent-history-cursor $next) True))
        ; Another writer moved it: read it again next time.
        ($other (let $_ (change-state! &agent-history-cursor unread) (Error agent-history:move $other)))))))

(= (agent-history:report $failure)
   (let $_ (system:write stderr (str:concat "[loop] agent history catch-up: " (str:concat (repr $failure) "\n")))
     $failure))

; At a turn's start: what other loops wrote since this loop's cursor joins the
; conversation. Never fails the turn; a failure is retried at the next turn.
(= (agent-history:catch-up)
   (if (not (agent-history:enabled?)) True
     (case (catch (agent-history:catch-up-now))
       ((True True) ($failure (let $_ (agent-history:report $failure) True))))))
(= (agent-history:catch-up-now)
   (let $file (agent-history:file)
     (if (== $file AgentHistoryMissingV1) True
       (let $raw (agent-history:cursor-raw)
         (if (agent-history:failed? $raw) $raw
           (let (AgentHistoryFileV1 $device $inode $size) $file
             (case (if (== $raw none) none (agent-history:parse $raw))
               ; A first run starts at the end: earlier records are the file's.
               ((none (agent-history:move $raw $file $size))
                ((AgentHistoryCursorV1 $_ $d $i $at)
                  (if (or (or (!= $d $device) (!= $i $inode)) (> $at $size))
                    ; Another file, or a shorter one: its old positions mean nothing.
                    (agent-history:move $raw $file $size)
                    (if (== $at $size) True (agent-history:import $raw $file $at $size))))))))))))

(= (agent-history:id (AgentHistoryFileV1 $device $inode $size) $from)
   (str:join ":" ("agent-history" (str:byte-slice (str:sha256 (str:join "\n" ((agent-history:path) $device $inode))) 0 16) (repr $from))))
(= (agent-history:header $from $shown $to)
   (str:join "" ("[AGENT HISTORY bytes " (repr $shown) "-" (repr $to) " of " (agent-history:path)
     ": what the agent's other modes recorded in its shared history after this loop's last turn, oldest first."
     " Those turns already happened and their actions already ran; this is their record, not a new message."
     " A request in it may still be unanswered."
     (if (== $shown $from) ""
       (str:join "" (" Bytes " (repr $from) "-" (repr $shown) " are older and not shown here; read them from that file.")))
     "]\n")))
; The bytes [<from>, <to>) of the history, or of a long gap its newest bytes
; from the first record that starts in them.
(= (agent-history:text $from $to)
   (if (<= (- $to $from) (agent-history:limit))
     (let $text (agent-history:read-at (agent-history:path) $from (- $to $from))
       (if (agent-history:failed? $text) $text (AgentHistoryTextV1 $from $text)))
     (agent-history:tail $to)))
(= (agent-history:tail $to)
   (case (fs:tail-text (agent-history:path) (agent-history:limit))
     (((fs:tail $size $_ $text)
       (if (!= $size $to) (Error agent-history:tail (Incomplete changed-while-read))
         (let $aligned (case (str:find $text "\n(\"")
                         (((TextNotFound $_) $text)
                          ($at (str:byte-slice $text (+ $at 1) (str:byte-length $text)))))
           (AgentHistoryTextV1 (- $to (str:byte-length $aligned)) $aligned))))
      ($bad (Error agent-history:tail $bad)))))
(= (agent-history:read-at $path $offset $bytes)
   (case (fs:open-read $path)
     (((NativeHandle $kind $id)
       (let* (($owner (NativeHandle $kind $id))
              ($read (agent-history:read-owned $owner $offset $bytes ()))
              ($_ (fs:close $owner)))
         $read))
      ($bad (Error agent-history:read $bad)))))
(= (agent-history:read-owned $owner $offset $left $reverse)
   (if (== $left 0) (str:join "" (list:reverse $reverse))
     (case (fs:read-chunk $owner (min 65536 $left) $offset)
       (((fs:chunk $bytes $_)
         (let $count (str:byte-length $bytes)
           (if (or (== $count 0) (> $count $left)) (Error agent-history:read (Incomplete short-read))
             (agent-history:read-owned $owner (+ $offset $count) (- $left $count) (cons-atom $bytes $reverse)))))
        ($bad (Error agent-history:read $bad))))))

; The import of [<from>, <to>) as a settled notification, then the cursor.
(= (agent-history:import $raw $file $from $to)
   (case (agent-history:text $from $to)
     (((AgentHistoryTextV1 $shown $text)
       (let* (($id (agent-history:id $file $from))
              ($content (str:concat (agent-history:header $from $shown $to) $text))
              ($answer (state:ledger "request-accept"
                         ($id $content "operator" "notification"
                          (str:char-slice $content 0 (min 180 (str:char-length $content)))
                          (str:sha256 $content) (state:request-id)))))
         (case $answer
           ((created (agent-history:settle $raw $file $id $to))
            (exists (agent-history:settle $raw $file $id $to))
            ; An earlier attempt imported from <from> to another end.
            (conflict (agent-history:resume $raw $file $id $from))
            ($bad (Error agent-history:import $bad))))))
      ($bad $bad))))
(= (agent-history:settle $raw $file $id $to)
   (let $settled (state:ledger "request-settle" ($id (system:wall-time) (state:request-id)))
     (if (agent-history:failed? $settled) (Error agent-history:settle $settled)
       (agent-history:move $raw $file $to))))
(= (agent-history:resume $raw $file $id $from)
   (case (state:ledger "request-get" ($id text))
     (((got $_ $_ $_ $_ $_ $_ $content)
       (case (agent-history:recorded-end $content)
         ((none (Error agent-history:resume unreadable-header))
          ($end
            (let (AgentHistoryFileV1 $_ $_ $size) $file
              (if (or (<= $end $from) (> $end $size)) (Error agent-history:resume (recorded-end-out-of-range $from $end $size))
                (let $moved (agent-history:settle $raw $file $id $end)
                  ; The cursor moved forward; what follows joins now.
                  (if (== $moved True) (agent-history:catch-up-now) $moved))))))))
      ($bad (Error agent-history:resume $bad)))))
; The end an import's header names: "[AGENT HISTORY bytes <shown>-<end> of ...".
(= (agent-history:recorded-end $content)
   (let $words (str:split " " (str:byte-slice $content 0 (min 200 (str:byte-length $content))))
     (if (< (size-atom $words) 4) none
       (let $range (str:split "-" (index-atom $words 3))
         (if (!= (size-atom $range) 2) none
           (case (catch (str:string->number (index-atom $range 1)))
             (((Error $_ $_) none) ($end (if (== (get-type $end) Number) $end none)))))))))

; This loop's own record: written, then the cursor follows it when it stood
; where the record began. Otherwise another loop's bytes lie before it, and the
; next catch-up shows them with this record after them.
(= (agent-history:append-own $text)
   (if (not (agent-history:enabled?)) (loop-host:history-append $text)
     (let* (($before (agent-history:file))
            ($written (loop-host:history-append $text)))
       (if (!= $written 1) $written
         ; collapse: a step that yields nothing must not take the receipt with it.
         (let $_ (collapse (catch (agent-history:own-appended $before))) 1)))))
(= (agent-history:own-appended $before)
   (case (agent-history:file)
     (((AgentHistoryFileV1 $device $inode $end)
       ; Where the record began: 0 when this record created the file.
       (let $start (case $before (((AgentHistoryFileV1 $device $inode $at) $at) (AgentHistoryMissingV1 0) ($other -1)))
         (if (< $start 0) True
           (let $raw (agent-history:cursor-raw)
             (if (agent-history:failed? $raw) $raw
               (case (if (== $raw none) none (agent-history:parse $raw))
                 ((none (agent-history:move $raw (AgentHistoryFileV1 $device $inode $end) $end))
                  ((AgentHistoryCursorV1 $_ $device $inode $start)
                    (agent-history:move $raw (AgentHistoryFileV1 $device $inode $end) $end))
                  ($elsewhere True))))))))
      ($missing True))))

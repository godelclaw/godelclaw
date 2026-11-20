; Omega's append history keeps a recoverable native intent. Supervised, it is
; the agent's own history, shared with the framework loop and Iter.
!(import! &self ../../../src/unicode_casefold_cetta.metta)
!(import! &self ../../history_cetta.metta)
(= (omega-host:history-path)
   (if (mode-history:shared?) (mode-history:path)
     (path:join (path:join (omega-host:project-root) "memory") "history.metta")))
;; In the agent's history, another mode's torn append is completed from its
;; record first (mode-history:complete-others). Omega takes a turn's input only
;; once that has succeeded (omega-host:await-history). Should a mode's record
;; become unreadable during the turn: the modes run one at a time, so nothing
;; can have torn the history since, and Omega appends when the file is the one,
;; at the size, it last verified; otherwise it waits as before a turn.
(= (omega-host:append-history $text)
   (let* (($ready (omega-host:append-ready))
          ($written (if (!= $ready True) $ready
                      (mode-host:append-checkpoint (omega-host:history-path)
                        (history-pending:on-own-line (omega-host:history-path) (str:concat $text "\n"))))))
     (if (!= $written True) (mode-host:stop-uncommitted $written)
       (let* (($_ (omega-host:note-writable)) ($_ (mode-host:record "checkpoint" ())))
         "APPEND-FILE-SUCCESS"))))
(= (omega-host:append-ready)
   (if (== (omega-host:append-safe) True) True (omega-host:await-history)))
(= (omega-host:append-safe)
   (let $ready (omega-host:history-ready)
     (if (== $ready True) True
       (if (omega-host:unchanged-since-verified) (let $_ (mode-history:report $ready) True) $ready))))
;; The history as Omega last found it writable: (OmegaWritableV1 <device>
;; <inode> <size>), noted when every mode's record was verified and after each
;; of Omega's own appends; none before.
!(change-state! &omega-history-writable none)
(= (omega-host:note-writable)
   (case (mode-host:file-info (omega-host:history-path))
     (((ModeFileV1 $size $device $inode) (change-state! &omega-history-writable (OmegaWritableV1 $device $inode $size)))
      ($other (change-state! &omega-history-writable none)))))
(= (omega-host:unchanged-since-verified)
   (case (get-state &omega-history-writable)
     (((OmegaWritableV1 $device $inode $size)
       (case (mode-host:file-info (omega-host:history-path))
         (((ModeFileV1 $now $now-device $now-inode)
           (and (== $now $size) (and (== $now-device $device) (== $now-inode $inode))))
          ($other False))))
      ($none False))))
(= (omega-host:write $path $text $append)
   (let $owner (fs:open-write $path (if $append ("create" "append") ("create" "truncate")) 438)
     (case $owner
       (((NativeHandle $_ $_)
         (let* (($written (fs:write-all $owner $text))
                ($synced (if (sf:failed? $written) $written (fs:sync $owner)))
                ($_ (fs:close $owner)))
           (if (sf:failed? $synced) (Error write-file (Incomplete $synced))
             (case (mode-host:file-info $path)
               (((ModeFileV1 $size $_ $_) (str:join "" ("verified " $path ": " (repr $size) " bytes")))
                ($bad $bad))))))
        ($bad (Error write-file (Fault $bad)))))))
(= (omega-host:remember $text)
   (let* (($row (JsonObject ((JsonPair "time" (system:wall-time)) (JsonPair "text" $text))))
          ($encoded (str:concat (turn:json $row) "\n"))
          ($written (omega-host:write (path:join (mode-host:root) "remembered.jsonl") $encoded True)))
     (if (turn:failed? $written) $written "REMEMBER-SUCCESS")))
(= (omega-host:query $needle)
   (let $path (path:join (mode-host:root) "remembered.jsonl")
     (if (not (fs:exists $path)) ""
       (case (fs:read-text-result $path 0 "replace")
         ((("ok" $text)
           (let* (($key (unicode:casefold $needle))
                  ($lines (filter-atom (str:split "\n" $text) $line
                    (case (str:find (unicode:casefold $line) $key) (((TextNotFound $_) False) ($found True)))))
                  ($joined (str:join "\n" $lines)) ($length (str:char-length $joined)))
             (str:char-slice $joined (max 0 (- $length 5000)) $length)))
          ($bad (Error omega-memory (Fault $bad))))))))
(= (omega-host:episodes $offered $count)
   (mode-history:episodes (omega-host:history-path) $offered $count))

; Results. Each executed call's outcome is a record of its own, on one line
; right after the command record it answers, in that record's durable append.
; This keeps results as raw adjacent lines: a reader takes a result line with
; the command record right before it in the same append. Readers do not yet
; verify or join them; the digest only keeps a result from being taken for a
; different record (byte-identical records it cannot tell apart, which their
; place in the file does):
;   (HistoryResultV1 omega <time> (turn <boot> <n>) (of <digest>) <index>
;                    <disposition> <availability> <text>)
; <turn> names this process and its count of result-bearing records; <index>
; the call's place in the record, so equal calls stay distinct occurrences;
; <digest> the first 16 hex digits of the record's SHA-256, so a result is
; never taken for another record's, even an equal one. <disposition> is how
; the call ended as Omega observed it: returned, raised, or unknown, which does
; not say it did not run. <availability> is how much of its text is kept: full
; (a returned "" is full and empty), (clipped <characters>) with the first
; result-cap characters, or none. The text is escaped onto the one line.
; Omega's own HISTORY leaves these lines out, as upstream keeps results out of
; history and Omega sees its latest results as tool messages; so it does not
; see other loops' results there either. The framework loop and Iter import
; them with the rest of the history: recorded history, never input to run.
!(change-state! &omega-results ())
!(change-state! &omega-turn 0)
!(change-state! &omega-boot (str:char-slice (system:uuid4-hex) 0 16))
(= (omega-host:result-cap) 4000)
(= (omega-host:note-result $result)
   (change-state! &omega-results (cons-atom $result (get-state &omega-results))))
; What the loop made of a call: "SUCCESS, RETURN: <text>" or "ALERT_FAILED<error>".
(= (omega-host:outcome $result)
   (if (!= (get-type $result) String) (unknown (repr $result))
     (if (str:starts-with $result "SUCCESS, RETURN: ")
       (returned (str:byte-slice $result 17 (str:byte-length $result)))
       (if (str:starts-with $result "ALERT_FAILED")
         (raised (str:byte-slice $result 12 (str:byte-length $result)))
         (unknown $result)))))
(= (omega-host:result-line $head $index $disposition $availability $text)
   (str:join "" ("(HistoryResultV1 omega " $head " " (repr $index) " " (repr $disposition) " "
                 $availability " \"" (str:escape metta $text) "\")")))
(= (omega-host:kept-line $head $index $outcome)
   (let ($disposition $text) $outcome
     (let $length (str:char-length $text)
       (if (> $length (omega-host:result-cap))
         (omega-host:result-line $head $index $disposition (repr (clipped $length))
           (str:char-slice $text 0 (omega-host:result-cap)))
         (omega-host:result-line $head $index $disposition "full" $text)))))
; One line per call: by the noted outcomes in order; a call with none noted
; has no result kept.
(= (omega-host:result-lines $head $index $outcomes $count)
   (if (>= $index $count) ()
     (let* (($line (if (== $outcomes ()) (omega-host:result-line $head $index unknown "none" "")
                     (omega-host:kept-line $head $index (omega-host:outcome (car-atom $outcomes)))))
            ($rest (omega-host:result-lines $head (+ $index 1)
                     (if (== $outcomes ()) () (cdr-atom $outcomes)) $count)))
       (cons-atom $line $rest))))
(= (omega-host:call-count $commands)
   (if (== (get-metatype $commands) Expression) (size-atom $commands) 0))
; The record's text with its calls' result lines after it, for one append.
(= (omega-host:with-results $record $commands)
   (let* (($outcomes (list:reverse (get-state &omega-results)))
          ($_ (change-state! &omega-results ()))
          ($count (max (size-atom $outcomes) (omega-host:call-count $commands))))
     (if (== $count 0) $record
       (let* (($n (+ 1 (get-state &omega-turn)))
              ($_ (change-state! &omega-turn $n))
              ($head (str:join "" ((repr (omega-host:timestamp)) " " (repr (turn (get-state &omega-boot) $n))
                                   " (of " (repr (str:byte-slice (str:sha256 $record) 0 16)) ")"))))
         (str:join "\n" (cons-atom $record (omega-host:result-lines $head 0 $outcomes $count)))))))
(= (omega-host:append-record $addition $commands)
   (omega-host:append-history (omega-host:with-results (repr $addition) $commands)))

;; Before Omega reads its history, and so before it takes input, asks a model
;; or runs a tool, the history must be one it can write after: another mode's
;; torn append is completed from its record; while a mode's record cannot be
;; read or verified, Omega waits at this boundary, reporting the cause once.
!(change-state! &omega-history-blocked none)
(= (omega-host:history-ready)
   (if (not (mode-history:shared?)) True
     (let $ready (mode-history:complete-others (omega-host:history-path))
       (if (== $ready True) (let $_ (omega-host:note-writable) True) $ready))))
(= (omega-host:await-history)
   (let $ready (omega-host:history-ready)
       (if (== $ready True) (let $_ (change-state! &omega-history-blocked none) True)
         (let* (($_ (if (== (get-state &omega-history-blocked) $ready) True
                      (let $_ (change-state! &omega-history-blocked $ready)
                        (mode-history:report (Error omega-history (waiting-for $ready))))))
                ($slept (mode-host:sleep 1)))
           (if (!= $slept True) $slept (omega-host:await-history))))))

;; Omega's HISTORY: the newest <budget> bytes of the history without result
;; lines, up to what the other loops have settled. Result lines are left out
;; before the budget applies, so they never displace the command and message
;; bytes Omega has always been shown. A window that starts inside a line drops
;; that fragment; a larger one is read until the budget is filled, the file is
;; whole, or the window reaches 64 budgets.
(= (omega-host:history-view $path $budget)
   (let $end (omega-host:history-end $path)
     (if (== $end unknown)
       "[The agent history is not shown this turn: a mode's record of its history writes could not be read.]"
       (omega-host:history-window $path $end $budget (* 2 $budget)))))
;; Another mode's unsettled append may be torn: the view ends before it. When
;; a mode's record cannot be read, the view ends where it last ended verified,
;; in this process and in this file; before any such end, the view says the
;; history is unavailable rather than show bytes that may be torn.
!(change-state! &omega-history-end none)
(= (omega-host:history-end $path)
   (let* (($_ (if (mode-history:shared?) (collapse (catch (mode-history:complete-others $path))) ()))
          ($size (mode-history:size $path)))
     (if (not (mode-history:shared?)) $size
       (case (mode-history:settled-size $path)
         ((unknown
            (case (get-state &omega-history-end)
              (((OmegaHistoryEndV1 $identity $end)
                 (if (and (== $identity (mode-history:identity)) (<= $end $size)) $end unknown))
               ($none unknown))))
          ($end (let $_ (change-state! &omega-history-end (OmegaHistoryEndV1 (mode-history:identity) $end)) $end)))))))
(= (omega-host:history-window $path $end $budget $window)
   (let* (($start (max 0 (- $end $window)))
          ($text (if (<= $end 0) "" (mode-host:read-at $path $start (- $end $start)))))
     (if (mode-host:read-failed? $text) (Error omega-history (Fault $text))
       (let* (($whole (== $start 0))
              ($lines (str:split "\n" $text))
              ($complete (if $whole $lines (cdr-atom $lines)))
              ($kept (str:join "\n" (filter-atom $complete $line (not (str:starts-with $line "(HistoryResultV1 ")))))
              ($length (str:byte-length $kept)))
         (if (or (or $whole (>= $length $budget)) (>= $window (* 64 $budget)))
           (history-pending:as-tail-text (str:byte-slice $kept (max 0 (- $length $budget)) $length))
           (omega-host:history-window $path $end $budget (* 2 $window)))))))

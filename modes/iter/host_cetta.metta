; Native mutable JSON values. Lists use occurrence indices, so append is constant work.
!(import! &self ../runtime_host_cetta.metta)
!(import! &self ../python_tool_cetta.metta)
!(import! &self ../history_cetta.metta)
; Occurrence numbers of the experience's messages, by identity; the next
; number; and the frontier: every message numbered at or below it is in the
; agent's history.
!(bind! &ih-numbers (new-state ()))
!(bind! &ih-next-number (new-state 1))
!(bind! &ih-frontier (new-state 0))
; The numbering the frontier counts in, and the history file it holds for.
!(bind! &ih-lineage (new-state ""))
!(bind! &ih-frontier-file (new-state none))
; The queue: the record text of the messages taken at their first save and
; not yet placed in the history; the occurrences it holds, those numbered
; after its start up to its top (the highest number taken); and the identity
; of the abandoned record already queued again, if any.
!(bind! &ih-queued (new-state ""))
!(bind! &ih-queued-from (new-state 0))
!(bind! &ih-queued-top (new-state 0))
!(bind! &ih-requeued (new-state ""))
(= (ih:list)
   (let* (($space (new-space)) ($length (new-space))
          ($_ (add-atom $length (IterLengthV1 0))))
     (IterListV1 $space $length)))
(= (ih:object)
   (let $space (new-space) (IterObjectV1 $space)))
(= (ih:box $value)
   (case $value
     (((JsonArray $items)
       (let $list (ih:list)
         (let $_ (ih:append-values $list (map-atom $items $item (ih:box $item))) $list)))
      ((JsonObject $pairs)
        (let $object (ih:object)
          (let $_ (ih:object-values $object $pairs) $object)))
      ((JsonString $text) $text)
      ((JsonNumber $text) (str:string->number $text))
      ((JsonBool $flag) $flag)
      ($leaf $leaf))))
(= (ih:object-values $object $pairs)
   (if (== $pairs ()) True
     (let* (((JsonPair $key $value) (car-atom $pairs))
            ($_ (ih:put $object $key (ih:box $value))))
       (ih:object-values $object (cdr-atom $pairs)))))
(= (ih:one $answers $default)
   (case $answers ((() $default) (($one) $one) ($many (Error iter-json (Fault duplicate-field))))))
(= (ih:optional $object $key)
   (case $object
     (((IterObjectV1 $space) (ih:one (collapse (match $space (IterFieldV1 $key $value) $value)) JsonNull))
      ((IterListV1 $space $length) (ih:one (collapse (match $space (IterItemV1 $key $value) $value)) JsonNull))
      ((JsonObject $_) (sf:get $object $key JsonNull))
      ($other JsonNull))))
(= (ih:get $object $key)
   (let $value (ih:optional $object $key)
     (if (== $value JsonNull) JsonNull $value)))
(= (ih:size $value)
   (case $value
     (((IterListV1 $space $length) (ih:one (collapse (match $length (IterLengthV1 $count) $count)) 0))
      ((IterObjectV1 $space) (size-atom (collapse (match $space (IterFieldV1 $key $_) $key))))
      ((JsonArray $items) (size-atom $items))
      ($text (if (== (get-type $text) String) (str:char-length $text) 0)))))
(= (ih:put (IterObjectV1 $space) $key $value)
   (let* (($_ (collapse (match $space (IterFieldV1 $key $old) (remove-atom $space (IterFieldV1 $key $old)))))
          ($_ (add-atom $space (IterFieldV1 $key $value))))
     (IterObjectV1 $space)))
(= (ih:remove (IterObjectV1 $space) $key)
   (let $_ (collapse (match $space (IterFieldV1 $key $old) (remove-atom $space (IterFieldV1 $key $old)))) True))
(= (ih:append (IterListV1 $space $length) $item)
   (let* (($count (ih:size (IterListV1 $space $length))) ($next (+ $count 1))
          ($_ (add-atom $space (IterItemV1 $count $item)))
          ($_ (remove-atom $length (IterLengthV1 $count)))
          ($_ (add-atom $length (IterLengthV1 $next))))
     (IterListV1 $space $length)))
(= (ih:append-values $list $items)
   (if (== $items ()) $list
     (let $_ (ih:append $list (car-atom $items)) (ih:append-values $list (cdr-atom $items)))))
(= (ih:values $list) (ih:values-at $list 0 (ih:size $list) ()))
(= (ih:values-at $list $index $end $reverse)
   (if (>= $index $end) (list:reverse $reverse)
     (ih:values-at $list (+ $index 1) $end (cons-atom (ih:get $list $index) $reverse))))
(= (ih:extend $list $items) (ih:append-values $list (ih:values $items)))
(= (ih:range $list $offset $count)
   (let $copy (ih:list)
     (let $_ (ih:copy-range $copy $list $offset (min (ih:size $list) (+ $offset $count))) $copy)))
(= (ih:copy-range $copy $original $index $end)
   (if (>= $index $end) True
     (let $_ (ih:append $copy (ih:get $original $index)) (ih:copy-range $copy $original (+ $index 1) $end))))
(= (ih:take $list $count) (ih:range $list 0 (max 0 $count)))
(= (ih:tail $list $count) (ih:range $list (max 0 (- (ih:size $list) $count)) $count))
(= (ih:drop-head $list) (ih:range $list 1 (max 0 (- (ih:size $list) 1))))
(= (ih:json $value) (ih:json-seen $value ()))
(= (ih:json-seen $value $seen)
   (case $value
     (((IterListV1 $space $length)
       (if (erv:contains $seen $space) (Error iter-json (Unsolvable circular-value))
         (JsonArray (map-atom (ih:values $value) $item (ih:json-seen $item (cons-atom $space $seen))))))
      ((IterObjectV1 $space)
       (if (erv:contains $seen $space) (Error iter-json (Unsolvable circular-value))
         (JsonObject (map-atom (collapse (match $space (IterFieldV1 $key $item) ($key $item))) $pair
           (let ($key $item) $pair (JsonPair $key (ih:json-seen $item (cons-atom $space $seen))))))))
      ($leaf $leaf))))
(= (ih:truth $value)
   (case $value
     ((JsonNull False) (False False) ("" False) (0 False)
      ((IterListV1 $_ $_) (> (ih:size $value) 0))
      ((IterObjectV1 $_) (> (ih:size $value) 0))
      ($other True))))
(= (ih:flag $value $key) (ih:truth (ih:get $value $key)))
(= (ih:equal $left $right) (== (turn:json (ih:json $left)) (turn:json (ih:json $right))))
(= (ih:not-equal $left $right) (not (ih:equal $left $right)))
(= (ih:empty-list) (ih:list))
(= (ih:message $role $content)
   (let $message (ih:object)
     (let* (($_ (ih:put $message "role" $role)) ($_ (ih:put $message "content" $content))) $message)))
(= (ih:step $text)
   (str:join "" ("Step " (system:format-time "%Y-%m-%d %H:%M:%S" (math:floor (system:wall-time)) "local") ": " (ih:stringify $text))))
(= (ih:stringify $value)
   (let $json (ih:json $value)
     (if (== (get-type $json) String) $json (erv:str $json))))
(= (ih:concat $left $right) (str:concat (ih:stringify $left) (ih:stringify $right)))
(= (ih:clip $value)
   (if (> (str:char-length $value) 5000) (str:concat (str:char-slice $value 0 5000) " [TRUNCATED]") $value))
(= (ih:sleep $seconds) (mode-host:sleep $seconds))
(= (ih:receive) (mode-host:receive))
; A mode that cannot boot or read its experience says why and stops, as Omega
; does; it never starts a loop over an error value.
(= (ih:initialize)
   (let* (($root (mode-host:root)) ($_ (fs:make-directories "memory" 448)) ($_ (fs:make-directories "transformations" 448))
          ($ready (mode-host:boot))
          ($booted (if (== $ready True) True (mode-host:stop-uncommitted $ready)))
          ($text (if (fs:exists "experience.json") (fs:read-text "experience.json") "[]"))
          ($history (catch (json:parse $text))))
     (case $history
       (((JsonArray $_)
         (let* (($_ (mode-host:sleep 10)) ($boxed (ih:box $history))
                ($_ (ih:restore-numbers $boxed $text)) ($_ (ih:catch-up $boxed))) $boxed))
        ($bad (mode-host:stop-uncommitted (Error iter:initialize (Fault invalid-experience $bad))))))))
; The agent's history since this mode last ran enters as one message, before
; the first step; it is the record of the other loops, never mirrored back.
; A record of this mode's that a death left pending is completed first.
(= (ih:catch-up-header)
   "[AGENT HISTORY: what happened in the agent's other modes since Iter last ran, oldest first, as recorded in the agent's shared history]\n")
(= (ih:catch-up $experience)
   (let* (($_ (ih:resolve))
          ($caught (mode-history:catch-up-to 30000)))
     (case $caught
       ; The cursor moves to the end that was read, which an append that is
       ; not whole yet lies past; nothing to show, or nothing readable: it stays.
       (((ModeCatchUpV1 $text $end)
         (if (== $text "") (ih:place)
           (let* (($_ (ih:append $experience (ih:message "user" (str:concat (ih:catch-up-header) $text))))
                  ($numbers (ih:numbers $experience))
                  ($_ (ih:capture $experience $numbers))
                  ($_ (ih:checkpoint $experience $numbers))
                  ($_ (mode-history:mark-at $end)))
             (ih:place))))
        ($unavailable (ih:place))))))
; Each saved message joins the agent's history once, in the framework's record
; form: input as HUMAN_MESSAGE, a step's calls as commands, then its results.
; A message keeps the occurrence number it was first given, by identity, so
; equal messages are each recorded and a message pruned in place is not
; recorded again.
; Its record text is taken once, at its first save, into the queue, which the
; checkpoint saves with the experience. The queue is then placed as one durable
; intent (mode-history:place-own) once no earlier one is pending, and its
; settlement moves the frontier. What the experience held unwritten is thus in
; the queue or in the placed intent, never only in the prompt: the experience
; is cut freely (iter:clean), and the queue waits, durable, for as long as the
; history cannot be written.
(= (ih:number-of $message $known)
   (case (filter-atom $known $pair (== (index-atom $pair 0) $message))
     ((() none) ($found (index-atom (car-atom $found) 1)))))
(= (ih:number-all $values $known $next $out)
   (if (== $values ()) (IterNumbersV1 (list:reverse $out) $next)
     (let* (($message (car-atom $values)) ($number (ih:number-of $message $known)))
       (if (== $number none)
         (ih:number-all (cdr-atom $values) $known (+ $next 1) (cons-atom ($message $next) $out))
         (ih:number-all (cdr-atom $values) $known $next (cons-atom ($message $number) $out))))))
; The experience's numbers, in order; a message seen first now takes the next.
(= (ih:numbers $experience)
   (let (IterNumbersV1 $pairs $next)
        (ih:number-all (ih:values $experience) (get-state &ih-numbers) (get-state &ih-next-number) ())
     (let* (($_ (change-state! &ih-numbers $pairs)) ($_ (change-state! &ih-next-number $next)))
       (map-atom $pairs $pair (index-atom $pair 1)))))
; At a start: the numbers, lineage, frontier and queue the checkpoint saved
; with this very experience; the frontier holds only for the file it was saved
; for. An experience saved without them is in the history, numbered anew,
; except the newest messages an earlier version's failed write left owed.
(= (ih:restore-numbers $experience $text)
   (let* (($record (mode-host:record-read))
          ($values (ih:values $experience))
          ($saved (case $record (((JsonObject $_) (mode-host:array $record "mirror_sequences")) ($other ()))))
          ($matches (case $record
                      (((JsonObject $_) (and (and (== (sf:get $record "sha256" "") (str:sha256 $text))
                                                  (== (size-atom $saved) (size-atom $values)))
                                             (!= (sf:get $record "mirror_lineage" "") "")))
                       ($other False))))
          ($numbers (if $matches (map-atom $saved $n (ih:integer $n)) (ih:count-up 1 (size-atom $values) ())))
          ($lineage (if $matches (sf:get $record "mirror_lineage" "") (system:uuid4-hex)))
          ($file (if $matches
                   (if (== (sf:get $record "mirror_device" "") "") none
                     ((sf:get $record "mirror_device" "") (sf:get $record "mirror_inode" "")))
                   (case (mode-history:identity) ((unknown none) ($known $known)))))
          ($frontier (if $matches (ih:integer (sf:get $record "mirror_frontier" 0))
                       (max 0 (- (size-atom $values) (ih:legacy-owed)))))
          ($queued (if $matches (sf:get $record "mirror_queued" "") ""))
          ($queued-top (if $matches (max $frontier (ih:integer (sf:get $record "mirror_queued_top" $frontier))) $frontier))
          ($queued-from (if $matches (ih:integer (sf:get $record "mirror_queued_from" (if (== $queued "") $queued-top $frontier)))
                          $queued-top))
          ($requeued (if $matches (sf:get $record "mirror_requeued" "") ""))
          ($top (foldl-atom $numbers $queued-top $m $n (max $m $n)))
          ($_ (change-state! &ih-numbers (ih:zip $values $numbers)))
          ($_ (change-state! &ih-next-number (+ $top 1)))
          ($_ (change-state! &ih-lineage $lineage))
          ($_ (change-state! &ih-frontier-file $file))
          ($_ (change-state! &ih-queued (if (== (get-type $queued) String) $queued "")))
          ($_ (change-state! &ih-queued-from $queued-from))
          ($_ (change-state! &ih-requeued (if (== (get-type $requeued) String) $requeued "")))
          ($_ (change-state! &ih-queued-top $queued-top)))
     (change-state! &ih-frontier $frontier)))
; How many of its newest saved messages an earlier version's cursor counted as
; owed after a failed write.
; A cursor that cannot be read says nothing of what is owed: the mode stops
; rather than count those messages written or write them twice.
(= (ih:legacy-owed)
   (case (mode-history:object-at (mode-history:cursor-path))
     ((none 0)
      (unknown (mode-host:stop-uncommitted (Error iter:restore (Incomplete legacy-cursor-unreadable))))
      ($record (if (== (sf:get $record "path" "") (mode-history:path))
                 (let $owed (sf:get $record "owed" 0) (if (== (get-type $owed) Number) (max 0 $owed) 0)) 0)))))
(= (ih:file-part $index)
   (case (get-state &ih-frontier-file) ((none "") ($pair (index-atom $pair $index)))))
(= (ih:integer $value)
   (case $value (((JsonNumber $text) (str:string->number $text)) ($number $number))))
(= (ih:count-up $from $count $out)
   (if (<= $count 0) (list:reverse $out) (ih:count-up (+ $from 1) (- $count 1) (cons-atom $from $out))))
(= (ih:zip $left $right)
   (if (or (== $left ()) (== $right ())) ()
     (cons-atom ((car-atom $left) (car-atom $right)) (ih:zip (cdr-atom $left) (cdr-atom $right)))))
; The record text of the messages first seen at this save joins the queue.
(= (ih:capture $experience $numbers)
   (let* (($top (get-state &ih-queued-top))
          ($pairs (filter-atom (ih:zip (ih:values $experience) $numbers) $pair (> (index-atom $pair 1) $top)))
          ($text (ih:history-text (map-atom $pairs $pair (index-atom $pair 0))))
          ($last (foldl-atom $pairs $top $m $pair (max $m (index-atom $pair 1))))
          ($_ (change-state! &ih-queued (str:concat (get-state &ih-queued) $text))))
     (change-state! &ih-queued-top $last)))
; The last placed record resolved: True when none is pending, False while one
; is, or while the history file or the record itself cannot be read: the queue
; then waits, durable, and the record is not replaced.
; - pending: completed once, by offset and prefix; a record whose place in the
;   file is gone (abandoned) is queued again, ahead of the rest;
; - settled: its frontier holds, in its file;
; - a record placed from this very queue (a death between its placement and
;   the next checkpoint) holds the queue's occurrences, and leaves the queue
;   empty: nothing is placed twice.
; Another history file in place of the one the frontier is for holds none of
; this mode's records: those the experience still has go to it again, ahead
; of everything queued, and the frontier starts again in it.
(= (ih:resolve)
   (let* (($current (mode-history:identity)) ($found (mode-history:intent)))
     (if (or (== $current unknown) (== $found unknown)) False
       (let* (($record (if (== $found none) (JsonObject ()) $found))
              ($status (sf:get $record "status" "none"))
              ($own (== (sf:get $record "lineage" "") (get-state &ih-lineage)))
              ($there (and (!= $status "none")
                           (== ((sf:get $record "device" "") (sf:get $record "inode" "")) $current)))
              ($clear (case $status
                        (("pending"
                           (case (mode-history:complete $record)
                             ((True (ih:settled $record $own))
                              (ModeIntentAbandonedV1 (ih:requeue $record))
                              ($failed (let $_ (ih:covered $record) False)))))
                         ("settled" (if $there (ih:settled $record $own) (let $_ (ih:covered $record) True)))
                         ("abandoned" (ih:requeue $record))
                         ($none True)))))
         (if (not $clear) False
           (let $_ (if (and $own $there) (change-state! &ih-frontier-file $current)
                     (if (or (== (get-state &ih-frontier-file) none) (== (get-state &ih-frontier-file) $current))
                       (change-state! &ih-frontier-file $current)
                       (ih:rewrite-for $current)))
             True))))))
(= (ih:settled $record $own)
   (let $_ (if $own (change-state! &ih-frontier (max (get-state &ih-frontier) (ih:integer (sf:get $record "frontier" 0))))
             True)
     (let $_ (ih:covered $record) True)))
; Whether a record of this numbering holds every occurrence of the queue.
(= (ih:holds-queue? $record)
   (and (== (sf:get $record "lineage" "") (get-state &ih-lineage))
        (and (<= (ih:integer (sf:get $record "from" (get-state &ih-queued-from))) (get-state &ih-queued-from))
             (>= (ih:integer (sf:get $record "frontier" 0)) (get-state &ih-queued-top)))))
(= (ih:covered $record)
   (if (ih:holds-queue? $record)
     (let $_ (change-state! &ih-queued "") (change-state! &ih-queued-from (get-state &ih-queued-top))) True))
; A record whose place is gone, queued again ahead of the rest, once: it is
; known by its numbering, occurrences, place and digest, never by its bytes,
; for two occurrences can render alike. A record that holds the whole queue
; takes its place.
(= (ih:record-identity $record)
   (str:join "|" (map-atom ("lineage" "from" "frontier" "offset" "device" "inode" "sha256") $key
                   (format-args "{}" ((sf:get $record $key ""))))))
(= (ih:requeue $record)
   (let $identity (ih:record-identity $record)
     (if (== $identity (get-state &ih-requeued)) True
       (let* (($payload (ih:payload-for $record))
              ($own (== (sf:get $record "lineage" "") (get-state &ih-lineage)))
              ($from (ih:integer (sf:get $record "from" (get-state &ih-queued-from))))
              ($_ (if (ih:holds-queue? $record)
                    (let $_ (change-state! &ih-queued $payload) (change-state! &ih-queued-from $from))
                    (let $_ (change-state! &ih-queued (str:concat $payload (get-state &ih-queued)))
                      (if $own (change-state! &ih-queued-from (min $from (get-state &ih-queued-from))) True)))))
         (let $_ (change-state! &ih-requeued $identity) True)))))
; What a record queued again holds: its payload when that matches its digest.
; A payload that fails its digest is never written from: the occurrences the
; record held are rendered again from the experience, and those the
; experience no longer holds are reported missing.
(= (ih:payload-for $record)
   (let $payload (sf:get $record "payload" "")
     (if (== (str:sha256 $payload) (sf:get $record "sha256" "")) $payload
       (let* (($pairs (ih:occurrences-of $record))
              ($missing (ih:occurrences-missing $record))
              ($_ (if (> $missing 0)
                    (mode-history:report (Error mode-history
                      (missing-occurrences (after (ih:integer (sf:get $record "from" 0)))
                                           (through (ih:integer (sf:get $record "frontier" 0))) (count $missing))))
                    True)))
         (ih:history-text (map-atom $pairs $pair (index-atom $pair 0)))))))
; The experience's messages numbered in a record's range, in its numbering.
(= (ih:occurrences-of $record)
   (if (!= (sf:get $record "lineage" "") (get-state &ih-lineage)) ()
     (let* (($from (ih:integer (sf:get $record "from" 0))) ($to (ih:integer (sf:get $record "frontier" 0))))
       (filter-atom (get-state &ih-numbers) $pair
         (and (> (index-atom $pair 1) $from) (<= (index-atom $pair 1) $to))))))
(= (ih:occurrences-missing $record)
   (let* (($from (ih:integer (sf:get $record "from" 0))) ($to (ih:integer (sf:get $record "frontier" 0))))
     (- (max 0 (- $to $from)) (size-atom (ih:occurrences-of $record)))))
(= (ih:rewrite-for $current)
   (let* (($frontier (get-state &ih-frontier))
          ($pairs (filter-atom (get-state &ih-numbers) $pair (<= (index-atom $pair 1) $frontier)))
          ($text (ih:history-text (map-atom $pairs $pair (index-atom $pair 0))))
          ($_ (change-state! &ih-queued (str:concat $text (get-state &ih-queued))))
          ($_ (change-state! &ih-queued-from 0))
          ($_ (change-state! &ih-frontier 0)))
     (change-state! &ih-frontier-file $current)))
; The queue placed as the mode's record, once nothing earlier is pending; it
; leaves the queue as soon as its intent is durable.
(= (ih:place)
   (if (not (mode-history:shared?)) True
     (if (not (ih:resolve)) True
       (let $queued (get-state &ih-queued)
         (if (== $queued "") True
           (case (mode-history:place-own $queued (get-state &ih-queued-from) (get-state &ih-queued-top) (get-state &ih-lineage))
             (((ModePlacedV1 $record)
               (let* (($_ (change-state! &ih-queued "")) ($_ (change-state! &ih-requeued ""))
                      ($_ (change-state! &ih-queued-from (get-state &ih-queued-top))))
                 (case (mode-history:complete $record)
                   ((True (let $_ (change-state! &ih-frontier (max (get-state &ih-frontier) (get-state &ih-queued-top)))
                            (change-state! &ih-frontier-file ((sf:get $record "device" "") (sf:get $record "inode" "")))))
                    (ModeIntentAbandonedV1 (ih:requeue $record))
                    ($pending True)))))
              ($unplaced True))))))))
(= (ih:history-text $messages)
   (if (== $messages ()) ""
     (let* (($message (car-atom $messages)) ($role (ih:get $message "role")))
       (if (== $role "user")
         (let* (($content (ih:stringify (ih:get $message "content")))
                ($rest (ih:history-text (cdr-atom $messages))))
           (if (str:starts-with $content "[AGENT HISTORY:") $rest
             (str:concat (repr ((mode-history:stamp) "\n" "HUMAN_MESSAGE: " $content "\n")) (str:concat "\n" $rest))))
         (if (== $role "assistant")
           (let* (($tools (ih:tool-results (cdr-atom $messages)))
                  ($after (list:drop (size-atom $tools) (cdr-atom $messages)))
                  ($calls (map-atom (ih:values-or-empty (ih:get $message "tool_calls")) $call (ih:command $call)))
                  ($results (map-atom $tools $tool (ih:stringify (ih:get $tool "content"))))
                  ($rest (ih:history-text $after)))
             (str:concat (repr ((mode-history:stamp) "\n" $calls "\n" RESULTS: $results "\n")) (str:concat "\n" $rest)))
           (ih:history-text (cdr-atom $messages)))))))
(= (ih:tool-results $messages)
   (if (== $messages ()) ()
     (if (== (ih:get (car-atom $messages) "role") "tool")
       (cons-atom (car-atom $messages) (ih:tool-results (cdr-atom $messages))) ())))
(= (ih:values-or-empty $list)
   (case $list (((IterListV1 $_ $_) (ih:values $list)) ($other ()))))
(= (ih:command $call)
   (let* (($function (ih:get $call "function")) ($name (ih:stringify (ih:get $function "name")))
          ($decoded (ih:decode (ih:get $function "arguments")))
          ($values (if (iter:true (ih:flag $decoded "object"))
                     (map-atom (ih:field-values (ih:get $decoded "value")) $value (ih:stringify $value)) ())))
     (cons-atom (str:string->symbol $name) $values)))
(= (ih:field-values $object)
   (case $object
     (((IterObjectV1 $space) (collapse (match $space (IterFieldV1 $_ $value) $value)))
      ($other ()))))
(= (ih:observe $experience $post $steps $burst $pending $bucket)
   (let* (($_ (health:settle)) ($_ (mode-host:record "boundary" ()))) (mode-host:gate)))
(= (ih:checkpoint $experience $numbers)
   (let $written (mode-host:checkpoint-with (path:join (mode-host:root) "experience.json") (turn:json (ih:json $experience))
                   ((JsonPair "mirror_sequences" (JsonArray $numbers)) (JsonPair "mirror_frontier" (get-state &ih-frontier))
                    (JsonPair "mirror_lineage" (get-state &ih-lineage))
                    (JsonPair "mirror_device" (ih:file-part 0)) (JsonPair "mirror_inode" (ih:file-part 1))
                    (JsonPair "mirror_queued" (get-state &ih-queued)) (JsonPair "mirror_queued_from" (get-state &ih-queued-from))
                    (JsonPair "mirror_queued_top" (get-state &ih-queued-top)) (JsonPair "mirror_requeued" (get-state &ih-requeued))))
     (if (!= $written True) (mode-host:stop-uncommitted $written)
       (mode-host:record "checkpoint" ((JsonPair "messages" (ih:size $experience)))))))
(= (ih:save $experience)
   (let* (($numbers (ih:numbers $experience))
          ($_ (ih:capture $experience $numbers))
          ($_ (ih:checkpoint $experience $numbers))
          ($_ (ih:place))) True))
(= (ih:cleanup-message $message)
   (let* (($_ (if (and (== (ih:get $message "role") "tool") (ih:truth (ih:get $message "content")))
                     (ih:put $message "content" " [TRUNCATED]") True))
          ($_ (ih:remove $message "reasoning")) ($_ (ih:remove $message "reasoning_details"))
          ($_ (ih:remove $message "reasoning_content"))) True))
(= (ih:tools)
   (let* (($snapshot (component:capture-tools))
          ($loaded (ih:object))
          ($_ (ih:put $loaded "snapshot" $snapshot))
          ($_ (ih:put $loaded "schemas" (ih:box (JsonArray (component:schemas $snapshot)))))
          ($_ (ih:put $loaded "omitted" (component:omitted $snapshot)))
          ($_ (ih:put $loaded "errors" (component:errors $snapshot))))
     $loaded))
(= (ih:tool-limit $count)
   (format-args "[TOOL LIMIT REACHED: {} tools are currently omitted. Consolidate or remove tools if they are needed.]" ($count)))
(= (ih:memory)
   (let* (($rows (component:memory "memory")) ($length (foldl-atom $rows 0 $total $row (+ $total (str:char-length (index-atom $row 1)))))
          ($result (ih:object)) ($_ (ih:put $result "contents" $rows)) ($_ (ih:put $result "length" $length)))
     $result))
(= (ih:memory-surface $memory $contents)
   (let* (($rows (ih:get $memory "contents")) ($length (ih:get $memory "length")))
     (if $contents
       (str:join "" ("[" (repr (- 3000 $length)) " CHARACTERS BELOW MAXIMUM]\n./memory/:\n"
         (str:join "\n\n" (map-atom $rows $row (str:join "" ((index-atom $row 0) ":\n" (index-atom $row 1)))))))
       (str:concat "./memory/:\n" (str:join "\n" (map-atom $rows $row (index-atom $row 0)))))))
(= (ih:memory-overflow $length)
   (format-args "[MEMORY FOLDER TOTAL CHARACTER CAPACITY BY FILES NOT BEGINNING WITH _ EXCEEDED BY {} CHARS. FIX THIS FIRST.]" ((- $length 3000))))
(= (ih:request-plan $experience $temporary $loaded $surface)
   (let* (($prompt (str:trim (fs:read-text "prompt.txt")))
          ($transforms (component:capture-transformations))
          ($system (str:join "" ("prompt.txt:\n" $prompt "\n\n./transformations/:\n"
                                  (component:descriptions $transforms) "\n\n" $surface)))
          ((JsonArray $history-items) (ih:json $experience))
          ((JsonArray $temporary-items) (ih:json $temporary))
          ($tail (list:append $history-items $temporary-items))
          ($items (cons-atom (JsonObject ((JsonPair "role" "system") (JsonPair "content" $system))) $tail))
          ($messages (JsonArray $items))
          ($tools (ih:json (ih:get $loaded "schemas")))
          ((ComponentRequestV1 $after-messages $after-tools $errors) (component:transform $transforms $messages $tools))
          ($routed (if (mode-host:standalone?) $after-messages
                     (let (JsonArray $after-items) $after-messages
                       (let* (($context (mode-host:route-context))
                              ($route-message (JsonObject ((JsonPair "role" "system") (JsonPair "content" $context))))
                              ($routed-items (cons-atom $route-message $after-items)))
                         (JsonArray $routed-items)))))
          ($final-messages (if (== $errors "") $routed
            (let (JsonArray $routed-items) $routed
              (let* (($notice (JsonObject ((JsonPair "role" "user") (JsonPair "content" $errors))))
                     ($with-notice (list:append $routed-items ($notice))))
                (JsonArray $with-notice)))))
          ($selected (model:current (system:getenv-or-default "LLM_MODEL" "mlx-community/gemma-4-26b-a4b-it-4bit")))
          ; What decides a refusal: everything sent but the loop's own notices,
          ; whose step stamps change every second.
          ($key (str:sha256 (str:join "\n" ($selected (ih:reasoning-effort) $system
                  (turn:json (JsonArray $history-items)) (turn:json $tools) (repr $transforms))))))
     (IterRequestV1 (ih:request-body $selected $final-messages $after-tools) $key)))
; The provider request, in the fields the native request contract admits.
; Upstream Iter asks its provider to think through extra_body.enable_thinking,
; which the contract refuses before sending, for the agents' ChatGPT models
; among others. The contract carries thinking as a reasoning effort: the
; agent's own, or medium as Omega uses.
(= (ih:request-body $model $messages $tools)
   (JsonObject ((JsonPair "model" $model) (JsonPair "messages" $messages)
     (JsonPair "tools" $tools) (JsonPair "tool_choice" "required")
     (JsonPair "max_tokens" 2524) (JsonPair "reasoning" (JsonObject ((JsonPair "effort" (ih:reasoning-effort))))))))
(= (ih:reasoning-effort)
   (let $effort (system:getenv-or-default "METTACLAW_REASONING_EFFORT" "")
     (if (or (== $effort "") (== $effort "default")) "medium" $effort)))
; A failed call ends the step, as upstream's exception does: the loop reports
; it and waits before the next step (iter:loop). A request that the provider
; or the request contract refused would be refused again unchanged, so it is
; not sent again until it changes: new input, memory, tools or prompt.
!(bind! &iter-refused (new-state ""))
(= (ih:provider $experience $temporary $tools $surface)
   (case (collapse (catch (ih:request-plan $experience $temporary $tools $surface)))
     ((((IterRequestV1 (JsonObject $pairs) $key))
       (if (== $key (get-state &iter-refused))
         (Error iter-provider (Unsolvable "the refused request is unchanged; it is not sent again"))
         (case (collapse (catch (llm:run (llm:structured-plan (JsonObject $pairs) "coding"))))
           ((((ProviderReplyV1 $reply)) (ih:box (state:canonical $reply)))
            ($failure (ih:provider-failed $failure $key))))))
      ($failure (mode-host:stop-uncommitted (Error iter-provider (Fault (request-preparation $failure))))))))
(= (ih:provider-failed $failure $key)
   (let $error (case $failure ((((Error $where $why)) (Error $where $why))
                               ($other (Error iter-provider (Fault (provider-result $other))))))
     (if (ih:refusal? $error)
       (let $_ (change-state! &iter-refused $key)
         (Error iter-provider (Unsolvable (str:concat "the request was refused; it is not sent again unchanged: "
                                                      (diagnostic:text $error 400)))))
       $error)))
; A failure that the same request would meet again, as the provider call
; reports it (host-native:structured-failed): the provider rejected the request
; (a 4xx other than a timeout, a conflict or a rate limit); the request
; contract or the call's configuration refused it before sending (ValueError);
; or a completion came back without its local receipt (CompletionConflict),
; whose resending could repeat a paid call. Anything else may pass later.
(= (ih:refusal? $error)
   (case $error
     (((Error provider (Fault $text))
       (if (== (get-type $text) String)
         (or (str:starts-with $text "native provider rejected the request:")
             (erv:contains ("native provider failed: ValueError" "native provider failed: CompletionConflict") $text))
         False))
      ($other False))))
(= (ih:retry-no-tool $content)
   (str:join "" ("[YOUR PREVIOUS RESPONSE CONTAINED NO TOOL CALL AND WAS NOT DELIVERED. CALL AT LEAST ONE TOOL NOW. IF YOU INTENDED THIS CONTENT AS COMMUNICATION, USE send: " (repr (ih:json $content)) "]")))
(= (ih:decode $arguments)
   (case (catch (json:parse $arguments))
     (((Error $_ $_) (ih:box (JsonObject ((JsonPair "ok" False) (JsonPair "error" "Invalid tool arguments from model")))))
      ($value (ih:box (JsonObject ((JsonPair "ok" True) (JsonPair "value" $value)
                   (JsonPair "object" (case $value (((JsonObject $_) True) ($other False)))))))))))
(= (ih:has-tool $loaded $name) (component:has? (ih:get $loaded "snapshot") $name))
(= (ih:unknown $name) (str:concat "Unknown tool: " (repr $name)))
(= (ih:invoke $loaded $name $arguments)
   (component:invoke (ih:get $loaded "snapshot") $name (ih:json $arguments)))
(= (ih:tool-message $call $output)
   (ih:box (JsonObject ((JsonPair "role" "tool") (JsonPair "tool_call_id" (ih:get $call "id"))
                       (JsonPair "content" (ih:step $output))))))

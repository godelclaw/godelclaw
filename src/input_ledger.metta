!(import! &self (library db))
!(import! &self (library str))
!(import! &self (library list))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library system))
!(import! &self ./llm_ledger)
!(import! &self ./notice_key)

; Input ledger in the native library db: collection `inputs` holds two kinds
; of record, ported from the tables of input_ack.py.
;
;   (processed-input "<id>" <seconds>)                      processed_inputs
;   (input-notice <task> <update-id> <lane> <send-key> <created> <message-id>
;                 <processed> <delete-key> <state> <checked>)   input_notices
;
; The notice fields are the SQLite row's ten columns in order: task, update-id,
; lane, send-key and delete-key are strings; created and checked are seconds;
; processed is 0 or 1; message-id and delete-key are the symbol `none` while
; the column is NULL (a string or integer otherwise); state is one of the
; symbols registered, submitted, withdrawing, deleted, expired. Both kinds share one
; collection because a db transaction covers a single collection, and
; register / mark-processed must read and write both atomically.
;
; Verbs: ensure, mark-processed, committed (processed ids); checkpoint and
; working-set (the turn checkpoint); request-accept, request-settle,
; request-disposition, request-cancel, request-finish, request-answered,
; request-retire (writes) and request-get, request-record, request-page,
; request-protected, request-rows (reads) of the requests; register,
; submitted, receipt (notices); due and check (the store half of pump); send-key
; and delete-key (the key derivations); import, census and lookup (migration).
; Every verb that writes answers its own value or an Error atom: see
; input-ledger:failure.
;
; The turn checkpoint (see "the turn checkpoint" below) and the requests (see "the
; requests" below) add three more kinds to the same collection, so that one
; transaction can store everything a turn settles:
;
;   (working-set "current" "<text>")       meta 'working_set': ONE atom, the JSON text
;   (request <id> <seq> <kind> <checker> <status> <settled> <note> <summary> <digest>)
;                                          requests + request_context, without the content
;   (request-text <id> <content>)          requests.content, ONE atom per request
;   (request-prev <id> <previous id>)      the request accepted before it ("" for the first)
;   (request-tail "tail" <n> <id>)         the number of requests (the last seq) and the newest one
;
; Indexes: processed-input, request-text, request-prev by their first argument (arity 3,
; position 1) and request-tail by its key (arity 4, position 1);
; input-notice (arity 11) by task (1), update-id (2), send-key (4) and
; delete-key (8); request (arity 10) by id (1) and status (5). The
; layout of a collection is fixed when it is first ensured: the engine refuses
; another one with (Error db:ensure index-layout-mismatch), so a store made by
; the earlier layout (no request atoms) must be made again (migrate_input_ledger.py).
;
; One store, two processes: every verb works on a db:open handle and on a
; db:connect handle (the native db server) alike. A connect handle runs the
; transaction body in the caller and publishes inert edits, which the server
; accepts only if the store has not advanced since the caller's snapshot, so a
; write can lose to another writer: (Error db:transaction (StoreRefused
; conflict)). Nothing was published then and the body is run again, up to
; input-ledger:attempts times. A reply can also be lost after the commit:
; (db:uncertain ...). Every write therefore carries a request id built from
; what the write does (see "writes" below), so that running it again replays
; the answer of the first commit instead of applying twice.

; Both collections of the store: `inputs` here, `provider` in src/llm_ledger.metta.
(= (input-ledger:ensure $db)
   (case (input-ledger:ensure-inputs $db)
     ((True (llm-ledger:ensure $db))
      ($other $other))))

(= (input-ledger:ensure-inputs $db)
   (db:ensure $db inputs ((SpaceIndexV1 3 0) (SpaceIndexV1 4 0) (SpaceIndexV1 5 0) (SpaceIndexV1 6 0)
                          (SpaceIndexV1 8 0) (SpaceIndexV1 9 0) (SpaceIndexV1 10 0) (SpaceIndexV1 11 0)
                          (SpaceIndexV1 3 1) (SpaceIndexV1 11 1) (SpaceIndexV1 11 2)
                          (SpaceIndexV1 11 4) (SpaceIndexV1 11 8)
                          (SpaceIndexV1 10 1) (SpaceIndexV1 10 5) (SpaceIndexV1 4 1)
                          (SpaceIndexV1 5 1) (SpaceIndexV1 9 1) (SpaceIndexV1 9 2)
                          (SpaceIndexV1 9 6) (SpaceIndexV1 9 7) (SpaceIndexV1 6 1) (SpaceIndexV1 8 1))))

; An id keeps its first time (INSERT OR IGNORE); the empty id is skipped.
; Matches inside a transaction see its own edits, so a repeated id in one
; batch is also recorded once.
(= (input-ledger:record $space $id $now)
   (if (== $id "")
       done
       (let* (($_ (if (== (collapse (match $space (processed-input $id $time) $time)) ())
                      (add-atom $space (processed-input $id $now))
                      done))
              ($_ (input-ledger:process-notices $space $id)))
         done)))

(= (input-ledger:record-all $space $ids $now)
   (if (== $ids ())
       done
       (let* ((($head $tail) (decons-atom $ids))
              ($_ (input-ledger:record $space $head $now)))
         (input-ledger:record-all $space $tail $now))))

; ---- writes: request ids, retries ----

; One request id per write, the same for every retry of that write:
;   il1.<verb>.<sha256 of the framed fields>
; The fields are the verb and every input the write depends on, each as the
; text of repr (a string keeps its quotes and escapes, a number its digits)
; preceded by its byte length and a colon, so no two different field lists give
; the same text, whatever quotes, colons, non-ASCII or length they hold. The id
; is 64 hex digits plus a short readable prefix, however long the batch is.
(= (input-ledger:attempts) 32)

(= (input-ledger:field $value)
   (let $text (repr $value)
     (str:concat (repr (str:byte-length $text)) (str:concat ":" $text))))

(= (input-ledger:fields $values)
   (if (== $values ())
       ""
       (let* ((($head $tail) (decons-atom $values)))
         (str:concat (input-ledger:field $head) (input-ledger:fields $tail)))))

(= (input-ledger:request $verb $fields)
   (str:concat "il1." (str:concat (repr $verb) (str:concat "."
     (str:sha256 (str:concat (input-ledger:field $verb) $fields))))))

; The batch of mark-processed as a set: its ids sorted by their text, so a
; retry that lists the same ids in another order is the same write.
(= (input-ledger:text-first $left $right) (<= (str:compare $left $right) 0))

(= (input-ledger:batch-fields $ids)
   (input-ledger:fields (list:sort-by input-ledger:text-first (map-atom $ids $id (repr $id)))))

; Run $body once per attempt through db:transaction-result; the operation is
; the verb. The outcome is returned as the engine gives it, except that a
; committed or replayed answer is its value (or values), and that a lost race
; (conflict) or a lost reply (uncertain) is tried again with the same request
; id, at most input-ledger:attempts times in all; the last of them is then
; returned as it is, to be normalized by the verb like any other failure.
; $prior is the uncertain outcome of an earlier attempt, or `none`: once an
; attempt may have committed, a later error does not say it did not (a handle
; whose reply was lost is dead, and answers only unavailable), so the verb
; keeps reporting uncertain and a retry by a new connection resolves it.
(= (input-ledger:write $db $verb $request $body $left $prior)
   (input-ledger:settle $db $verb $request $body $left $prior
     (db:transaction-result $db inputs $request $verb $body)))

(= (input-ledger:settle $db $verb $request $body $left $prior $outcome)
   (case $outcome
     (((db:committed $request-id $values) (superpose $values))
      ((db:replayed $request-id $values) (superpose $values))
      ((Error db:transaction (StoreRefused conflict))
       (input-ledger:again $db $verb $request $body $left $prior $outcome))
      ((db:uncertain $request-id $collection $operation)
       (input-ledger:again $db $verb $request $body $left $outcome $outcome))
      ((Error $where $why) (input-ledger:remember $prior $outcome))
      ($other $other))))

(= (input-ledger:again $db $verb $request $body $left $prior $last)
   (if (> $left 1)
       (input-ledger:write $db $verb $request $body (- $left 1) $prior)
       (input-ledger:remember $prior $last)))

(= (input-ledger:remember $prior $outcome)
   (if (== $prior none) $outcome $prior))

; A body that changed nothing and whose answer the verb already knows gives up
; (empty), so that no receipt records that answer: it could be stale at the
; next call with the same inputs. Such an abort is that known answer.
(= (input-ledger:unchanged $outcome $answer)
   (case $outcome
     (((db:aborted $request-id) $answer)
      ($other $other))))

; One failure shape for every verb that writes. An Error atom from the engine
; is returned unchanged; anything else that is not the verb's own answer (an
; uncertain outcome such as (db:uncertain <request> <collection> <operation>),
; or an unexpected value) becomes (Error <verb> <the outcome>). An aborted
; transaction body gives no answer at all and still gives none.
(= (input-ledger:failure $verb $outcome)
   (case $outcome
     (((Error $where $why) $outcome)
      ((db:aborted $request-id) (empty))
      ($other (Error $verb $other)))))

; True only when the transaction committed: its body answers `done`.
(= (input-ledger:done-answer $verb $outcome)
   (case $outcome
     ((done True)
      ($other (input-ledger:failure $verb $other)))))

(= (input-ledger:mark-processed $db $ids $now)
   (input-ledger:done-answer input-ledger:mark-processed
     (input-ledger:write $db input-ledger:mark-processed
       (input-ledger:request input-ledger:mark-processed
         (str:concat (input-ledger:field $now) (input-ledger:batch-fields $ids)))
       (|-> ($space) (input-ledger:record-all $space $ids $now))
       (input-ledger:attempts) none)))

(= (input-ledger:recorded $view $id)
   (if (== $id "")
       True
       (not (== (collapse (match $view (processed-input $id $time) $time)) ()))))

(= (input-ledger:all-recorded $view $ids)
   (if (== $ids ())
       True
       (let* ((($head $tail) (decons-atom $ids)))
         (if (input-ledger:recorded $view $head)
             (input-ledger:all-recorded $view $tail)
             False))))

; An unavailable snapshot returns its error: it must not mean "not processed".
(= (input-ledger:committed $db $ids)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer (input-ledger:all-recorded $view $ids))
                    ($_ (db:close $view)))
               $answer)))))

; ---- the turn checkpoint (storage half of observed_strategy.Ledger.checkpoint) ----

; ONE transaction stores what a cognitive turn settles: the working set, the
; processed-input records (as mark-processed: INSERT OR IGNORE, first time kept,
; the empty id skipped) with the processed flag of their notices, and the
; processed status of the request. The unit is the transaction: a reader sees
; all of it or none of it, a retry replays all of it.
;
; The working set is carried as the JSON text Python made of it, one string,
; and given back byte for byte by input-ledger:working-set: the store never
; looks inside. The atom is replaced, never added to: at most one exists.
(= (input-ledger:forget-working $space $old)
   (if (== $old ())
       done
       (let* ((($head $tail) (decons-atom $old))
              ($_ (remove-atom $space $head)))
         (input-ledger:forget-working $space $tail))))

(= (input-ledger:store-working $space $working)
   (let* (($_ (input-ledger:forget-working $space
                (collapse (match $space (working-set "current" $text) (working-set "current" $text)))))
          ($_ (add-atom $space (working-set "current" $working))))
     done))

; The processed status of the request is the settle rule of the requests (see
; input-ledger:settle-in): decided inside this transaction on the request's own
; record, which the same store holds.

; The operation of the write carries a digest of every argument, so a request id
; used again with other arguments is not replayed as if it were the same write:
; the engine refuses it (receipt-collision), and the verb answers that Error.
(= (input-ledger:checkpoint-operation $working $ids $request $now)
   (input-ledger:checkpoint-digest
     (str:sha256 (str:concat (input-ledger:field $working)
                   (str:concat (input-ledger:batch-fields $ids)
                     (str:concat (input-ledger:field $request) (input-ledger:field $now)))))))

; True when the transaction committed (or replayed: the same request id and the
; same arguments), else a failure shaped like mark-processed's. $request-id is
; the caller's: one per checkpoint, the same for every retry of it.
(= (input-ledger:checkpoint $db $working $ids $request $now $request-id)
   (input-ledger:done-answer input-ledger:checkpoint
     (input-ledger:write $db (input-ledger:checkpoint-operation $working $ids $request $now) $request-id
       (|-> ($space) (let* (($_ (input-ledger:store-working $space $working))
                            ($_ (input-ledger:record-all $space $ids $now))
                            ($_ (input-ledger:settle-in $space $request $now)))
                       done))
       (input-ledger:attempts) none)))

; The working set text of the last checkpoint, or the symbol none when no
; checkpoint has stored one. An unavailable store answers its Error: it must
; not look like an empty working set.
(= (input-ledger:working-set $db)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($found (collapse (match $view (working-set "current" $text) $text)))
                    ($_ (db:close $view)))
               (case $found
                 ((() none)
                  (($text) $text)
                  ($other (Error input-ledger:working-set (several-atoms (size-atom $other)))))))))))

; ---- the requests (storage half of observed_strategy.Ledger's request methods) ----

; The tables requests and request_context of the SQLite ledger, in the same
; collection: one record per request (its columns but the content), one atom for
; its content, and a counter, so that a status change, a settle by the turn
; checkpoint and every reader work on ONE store and one transaction.
;
;   (request <id> <seq> <kind> <checker> <status> <settled> <note> <summary> <digest>)
;   (request-text <id> <content>)
;   (request-prev <id> <previous id>)
;   (request-tail "tail" <n> <id>)
;
; id, kind, checker, status, note, summary, digest and content are strings, byte
; for byte as Python gave them. seq is the rowid of the SQLite row: 1, 2, ... in
; the order of acceptance, no gaps (a request is never removed); the newest first
; is seq descending. kind is message, notification, task, goal or autonomous;
; status is active, processed, waiting-user, deferred, addressed, achieved,
; cancelled or covered. settled is the symbol `none` while request_context.processed
; is 0, else the seconds of the checkpoint (or processed call) that settled it:
; Python sees processed = 1. summary is the first 180 characters of the content
; and digest the sha256 of its JSON text, both made by Python once, when the
; request is accepted (the content never changes), so that a listing does not
; carry the contents. The content is its own atom so that a status change does
; not rewrite it.
;
; Order: a lookup by an integer position of a record is a scan of the whole
; collection in this engine (measured: 150 ms per lookup at 1200 atoms; a lookup by a
; string at an indexed position is instant), so "the newest first" is a chain of
; ids, not a search by seq: request-tail names the newest request, request-prev the
; one before each. A page or the newest active requests are walked from the tail,
; one indexed lookup per step. seq stays in the record for the order of whole listings
; (and is what SQLite's rowid is in the oracle).
;
; Every write is one transaction with a request id made by the caller ONCE per
; call (rq1.<uuid>): a retry of the call (a lost reply, a restarted worker) carries
; the same id and replays the first answer; a new call, even with the same arguments,
; is a new transition decided on the state it finds (as the SQLite methods do).
; The id is not derived from the arguments because their answer depends on the
; state: accepting twice answers created, then exists; a disposition refused before
; a checkpoint is accepted after it. The operation of the write carries a digest of
; every argument (as the checkpoint does), so that an id used again with other
; arguments is refused (receipt-collision) instead of replaying another call's
; answer.
;
; Task 5 stored the settled status of a request as (request-processed "<request>"
; <seconds>) atoms. They are not read any more, and a store made with that layout cannot
; be opened by this module at all (index-layout-mismatch, see above): it is made again from
; SQLite by scripts/migrate_input_ledger.py, which never wrote such an atom. For a store
; that holds one anyway (an import), accepting a request removes the atom of its id, so
; that a stale atom cannot make a new request born settled; the census counts the rest as
; other atoms.

(= (input-ledger:heads $space $id)
   (collapse (match $space (request $id $q $k $c $st $p $n $sm $dg)
                    (request $id $q $k $c $st $p $n $sm $dg))))

(= (input-ledger:heads-with-status $space $status)
   (collapse (match $space (request $id $q $k $c $status $p $n $sm $dg)
                    (request $id $q $k $c $status $p $n $sm $dg))))

(= (input-ledger:all-heads $space)
   (collapse (match $space (request $id $q $k $c $st $p $n $sm $dg)
                    (request $id $q $k $c $st $p $n $sm $dg))))

(= (input-ledger:texts $space $id)
   (collapse (match $space (request-text $id $text) $text)))

; Rows in the order of a key each row has once, a number or a text: the (key row) pairs
; sorted natively, in the order str:compare and < give.
(= (input-ledger:sorted-by $key $rows)
   (map-atom (sort-atom (map-atom $rows $row (let $k ($key $row) ($k $row)))) $pair (input-ledger:second $pair)))
(= (input-ledger:second ($k $row)) $row)
(= (input-ledger:seq-key (request $i $q $k $c $s $p $n $m $d)) $q)

(= (input-ledger:seq-last (request $i1 $q1 $k1 $c1 $s1 $p1 $n1 $m1 $d1)
                          (request $i2 $q2 $k2 $c2 $s2 $p2 $n2 $m2 $d2))
   (>= $q1 $q2))

; The next seq: the counter atom, replaced.
; The tail atom: the number of requests and the newest one; one atom, so that an
; acceptance removes (and so rewrites) only one: a removal costs time in proportion to the
; size of the whole collection in this engine.
(= (input-ledger:tail-of $space)
   (case (collapse (match $space (request-tail "tail" $n $id) $id))
     ((() "") (($id) $id))))

(= (input-ledger:counter $space)
   (case (collapse (match $space (request-tail "tail" $n $id) $n))
     ((() 0) (($n) $n))))

(= (input-ledger:prev-of $space $id)
   (case (collapse (match $space (request-prev $id $before) $before))
     ((() "") (($before) $before))))

; The seq of the new request, and the tail moved to it.
(= (input-ledger:take-seq $space $id)
   (let* (($old (collapse (match $space (request-tail "tail" $n $last) (request-tail "tail" $n $last))))
          ($next (+ (input-ledger:counter $space) 1))
          ($_ (input-ledger:forget-working $space $old))
          ($_ (add-atom $space (request-tail "tail" $next $id))))
     $next))

(= (input-ledger:forget-legacy $space $id)
   (input-ledger:forget-working $space
     (collapse (match $space (request-processed $id $time) (request-processed $id $time)))))

; Ledger.accept: created for a new request, exists for the same content and
; checker, conflict for the same id with another (INSERT only when absent).
(= (input-ledger:accept-in $space $id $content $checker $kind $summary $digest)
   (let $found (input-ledger:heads $space $id)
     (if (== $found ())
         (let* (($before (input-ledger:tail-of $space))
                ($seq (input-ledger:take-seq $space $id))
                ($_ (add-atom $space (request $id $seq $kind $checker "active" none "" $summary $digest)))
                ($_ (add-atom $space (request-text $id $content)))
                ($_ (add-atom $space (request-prev $id $before)))
                ($_ (input-ledger:forget-legacy $space $id))
                ($position (input-ledger:tick $space))
                ($_ (add-atom $space (ledger:arrival $id $position)))
                ($_ (input-ledger:advance $space)))
           created)
         (let (request $i $q $k $c $st $p $n $sm $dg) (car-atom $found)
           (if (and (== $c $checker) (== (input-ledger:texts $space $id) ($content)))
               exists
               conflict)))))

; Ledger._processed: the request is settled once (first time kept), when it
; is a message or notification or has an input: id; a message or notification that
; was active becomes processed. True when it settled the request, else False
; (unknown, another kind, already settled). The empty id is no request.
(= (input-ledger:settle-head $space (request $id $q $k $c $st $p $n $sm $dg) $now)
   (if (and (or (or (== $k "message") (== $k "notification")) (str:starts-with $id "input:"))
            (== $p none))
       (let* (($status (if (and (or (== $k "message") (== $k "notification")) (== $st "active")) "processed" $st))
              ($_ (input-ledger:replace $space (request $id $q $k $c $st $p $n $sm $dg)
                                                (request $id $q $k $c $status $now $n $sm $dg))))
         True)
       False))

(= (input-ledger:settle-in $space $id $now)
   (if (== $id "")
       False
       (let $found (input-ledger:heads $space $id)
         (if (== $found ())
             False
             (input-ledger:settle-head $space (car-atom $found) $now)))))

; The refusal rules of Ledger.disposition, in its order; "" when there is none.
(= (input-ledger:refusal $disposition (request $id $q $k $c $st $p $n $sm $dg))
   (if (or (== $st "achieved") (== $st "cancelled"))
       (str:concat "the request is already " $st)
       (if (== $k "goal")
           "goal cycles are not disposed; retire the goal with goal-drop"
           (if (== $k "autonomous")
               "autonomous work has no disposition"
               (if (and (== $disposition "addressed") (not (== $c "operator")))
                   "this request has its own checker and completes when the check passes"
                   "")))))

(= (input-ledger:disposition-status $disposition)
   (case $disposition
     (("keep" "active") ("waiting-user" "waiting-user") ("defer" "deferred")
      ("addressed" "addressed") ("reopen" "active"))))

(= (input-ledger:disposition-head $space (request $id $q $k $c $st $p $n $sm $dg) $disposition $note)
   (let $reason (input-ledger:refusal $disposition (request $id $q $k $c $st $p $n $sm $dg))
     (if (not (== $reason ""))
         (refused $reason)
         (if (and (== $st "covered") (not (or (== $disposition "keep") (== $disposition "reopen"))))
             (refused "input batch is covered; reopen it to create independent work")
             (let* (($kind (if (or (== $disposition "keep") (== $st "covered")) "task" $k))
                    ($_ (input-ledger:replace $space (request $id $q $k $c $st $p $n $sm $dg)
                            (request $id $q $kind $c (input-ledger:disposition-status $disposition) $p $note $sm $dg)))
                    ($_ (input-ledger:advance $space)))
               ok)))))

(= (input-ledger:disposition-in $space $id $disposition $note)
   (let $found (input-ledger:heads $space $id)
     (if (== $found ())
         (refused "unknown request ID")
         (case (input-ledger:disposition-head $space (car-atom $found) $disposition $note)
           ((ok (let* (($now (system:wall-time))
                       ($_ (input-ledger:settle-in $space $id $now))
                       ($_ (input-ledger:record-all $space (input-ledger:inputs-of $space $id) $now))) ok))
            ($refused $refused))))))

; UPDATE requests SET status=$to WHERE id=$id AND status IN (<$from>): True when
; the request had one of those statuses.
(= (input-ledger:member $item $items)
   (if (== $items ())
       False
       (let* ((($head $tail) (decons-atom $items)))
         (if (== $head $item) True (input-ledger:member $item $tail)))))

(= (input-ledger:move-in $space $id $from $to)
   (let $found (input-ledger:heads $space $id)
     (if (== $found ())
         False
         (let (request $i $q $k $c $st $p $n $sm $dg) (car-atom $found)
           (if (not (input-ledger:member $st $from))
               False
               (let $_ (input-ledger:replace $space (request $i $q $k $c $st $p $n $sm $dg)
                                                    (request $i $q $k $c $to $p $n $sm $dg))
                 True))))))

; Ledger.retire: every request whose id starts with the prefix and is active,
; deferred or waiting-user becomes cancelled with the note; their ids (see retirable for the order).
(= (input-ledger:retire-each $space $heads $note)
   (if (== $heads ())
       ()
       (let* ((($head $tail) (decons-atom $heads))
              ((request $i $q $k $c $st $p $n $sm $dg) $head)
              ($_ (input-ledger:replace $space $head (request $i $q $k $c "cancelled" $p $note $sm $dg))))
         (cons-atom $i (input-ledger:retire-each $space $tail $note)))))

(= (input-ledger:retirable-with $space $prefix $status)
   (input-ledger:sorted-by input-ledger:seq-key
     (collapse (match $space (request $id $q $k $c $status $p $n $sm $dg)
                      (if (str:starts-with $id $prefix) (request $id $q $k $c $status $p $n $sm $dg) (empty))))))

; In the order SQLite's plan gives (the index on status, then rowid): active, then deferred,
; then waiting-user, each oldest first. No caller depends on the order, only on the ids.
(= (input-ledger:retirable $space $prefix)
   (union-atom (input-ledger:retirable-with $space $prefix "active")
               (union-atom (input-ledger:retirable-with $space $prefix "deferred")
                           (input-ledger:retirable-with $space $prefix "waiting-user"))))

(= (input-ledger:retire-in $space $prefix $note)
   (let* (($ids (input-ledger:retire-each $space (input-ledger:retirable $space $prefix) $note))
          ($_ (if (== $ids ()) done (input-ledger:advance $space))))
     (retired $ids)))

; One write of the requests: the same shape as the checkpoint's. $fields are the
; arguments in the framing of input-ledger:field; $body answers.
(= (input-ledger:request-write $db $verb $fields $request-id $body)
   (input-ledger:write $db
     (input-ledger:request-digest $verb (str:sha256 (str:concat (input-ledger:field $verb) $fields)))
     $request-id $body (input-ledger:attempts) none))

(= (input-ledger:created-answer $verb $outcome)
   (case $outcome
     ((created created)
      (exists exists)
      (conflict conflict)
      ($other (input-ledger:failure $verb $other)))))

(= (input-ledger:flag-answer $verb $outcome)
   (case $outcome
     ((True True)
      (False False)
      ($other (input-ledger:failure $verb $other)))))

(= (input-ledger:request-accept $db $id $content $checker $kind $summary $digest $request-id)
   (input-ledger:created-answer input-ledger:request-accept
     (input-ledger:request-write $db input-ledger:request-accept
       (str:concat (input-ledger:field $id) (str:concat (input-ledger:field $content)
         (str:concat (input-ledger:field $checker) (str:concat (input-ledger:field $kind)
           (str:concat (input-ledger:field $summary) (input-ledger:field $digest))))))
       $request-id
       (|-> ($space) (input-ledger:accept-in $space $id $content $checker $kind $summary $digest)))))

(= (input-ledger:request-settle $db $id $now $request-id)
   (input-ledger:flag-answer input-ledger:request-settle
     (input-ledger:request-write $db input-ledger:request-settle
       (str:concat (input-ledger:field $id) (input-ledger:field $now)) $request-id
       (|-> ($space) (input-ledger:settle-in $space $id $now)))))

(= (input-ledger:request-disposition $db $id $disposition $note $request-id)
   (input-ledger:disposition-answer
     (input-ledger:request-write $db input-ledger:request-disposition
       (str:concat (input-ledger:field $id) (str:concat (input-ledger:field $disposition) (input-ledger:field $note)))
       $request-id
       (|-> ($space) (input-ledger:disposition-in $space $id $disposition $note)))))

(= (input-ledger:disposition-answer $outcome)
   (case $outcome
     ((ok ok)
      ((refused $reason)
       (if (== (get-type $reason) String)
           $outcome
           (input-ledger:failure input-ledger:request-disposition $outcome)))
      ($other (input-ledger:failure input-ledger:request-disposition $other)))))

; The three single moves: cancel (active -> cancelled), finish (active -> achieved),
; answered (active or processed -> addressed).
(= (input-ledger:request-move $db $verb $id $from $to $request-id)
   (input-ledger:flag-answer $verb
     (input-ledger:request-write $db $verb (input-ledger:field $id) $request-id
       (|-> ($space)
         (let $moved (input-ledger:move-in $space $id $from $to)
           (if (and $moved (== $to "addressed"))
             (let* (($now (system:wall-time)) ($_ (input-ledger:settle-in $space $id $now))
                    ($_ (input-ledger:record-all $space (input-ledger:inputs-of $space $id) $now))) $moved)
             $moved))))))

(= (input-ledger:request-retire $db $prefix $note $request-id)
   (input-ledger:retire-answer
     (input-ledger:request-write $db input-ledger:request-retire
       (str:concat (input-ledger:field $prefix) (input-ledger:field $note)) $request-id
       (|-> ($space) (input-ledger:retire-in $space $prefix $note)))))

(= (input-ledger:retire-answer $outcome)
   (case $outcome
     (((retired $ids) $outcome)
      ($other (input-ledger:failure input-ledger:request-retire $other)))))

; ---- reads of the requests ----

; The whole record of one request from one snapshot, or the symbol none:
;   (got <id> <kind> <checker> <status> <processed 0|1> <note> <text>)
; <text> is the content when $with is the symbol text, else "".
(= (input-ledger:processed-flag $settled) (if (== $settled none) 0 1))

(= (input-ledger:got $view $head $with)
   (let (request $id $q $k $c $st $p $n $sm $dg) $head
     (let* (($flag (input-ledger:processed-flag $p))
            ($text (if (== $with text) (input-ledger:text-one $view $id) "")))
       (got $id $k $c $st $flag $n $text))))

(= (input-ledger:text-one $view $id)
   (case (input-ledger:texts $view $id)
     ((($text) $text)
      ($other (Error input-ledger:request-text (not-one-content $id (size-atom $other)))))))

(= (input-ledger:request-get $db $id $with)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($found (input-ledger:heads $view $id))
                    ($answer (if (== $found ()) none (input-ledger:got $view (car-atom $found) $with)))
                    ($_ (db:close $view)))
               $answer)))))

; The slice of a request's content at an offset, in characters, and its length:
;   (slice <text-slice> <length>), or none for an unknown request.
(= (input-ledger:slice-of $view $id $offset)
   (case (input-ledger:texts $view $id)
     ((() none)
      (($text) (slice (str:char-slice $text $offset (+ $offset 4000)) (str:char-length $text)))
      ($other (Error input-ledger:request-text (not-one-content $id (size-atom $other)))))))

;   (record <id> <kind> <checker> <status> <processed> <note> <slice> <length> <digest>) or none
(= (input-ledger:request-record $db $id $offset)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($found (input-ledger:heads $view $id))
                    ($answer (if (== $found ())
                                 none
                                 (let (request $i $q $k $c $st $p $n $sm $dg) (car-atom $found)
                                   (let $flag (input-ledger:processed-flag $p)
                                     (case (input-ledger:slice-of $view $id $offset)
                                       (((slice $text $length)
                                         (record $i $k $c $st $flag $n $text $length $dg))
                                        ($other $other)))))))
                    ($_ (db:close $view)))
               $answer)))))

; The newest requests first, $limit of them from $offset, and how many there are:
;   (page <total> ((<id> <status> <checker> <kind> <processed> <summary> <note>) ...))
(= (input-ledger:page-row (request $id $q $k $c $st $p $n $sm $dg))
   (let $flag (input-ledger:processed-flag $p)
     ($id $st $c $k $flag $sm $n)))

; The id $steps requests before $id along the chain ("" when the chain ends first).
(= (input-ledger:skip $view $id $steps)
   (if (or (<= $steps 0) (== $id ""))
       $id
       (input-ledger:skip $view (input-ledger:prev-of $view $id) (- $steps 1))))

(= (input-ledger:page-rows $view $id $count)
   (if (or (<= $count 0) (== $id ""))
       ()
       (let $found (input-ledger:heads $view $id)
         (if (== $found ())
             (Error input-ledger:request-page (broken-chain $id))
             (let $row (input-ledger:page-row (car-atom $found))
               (cons-atom $row (input-ledger:page-rows $view (input-ledger:prev-of $view $id) (- $count 1))))))))

(= (input-ledger:request-page $db $offset $limit)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($total (input-ledger:counter $view))
                    ($start (input-ledger:skip $view (input-ledger:tail-of $view) $offset))
                    ($rows (input-ledger:page-rows $view $start $limit))
                    ($_ (db:close $view)))
               (case $rows
                 (((Error $where $why) (Error $where $why))
                  ($other (page $total $rows)))))))))

; Everything Ledger.protected reads of the requests, from one snapshot:
;   (protected <counts> <total> <unfinished> <focus>)
; counts: ((<status> <n>) ...) for the statuses that occur; total: the number of
; requests (Python checks that the counts add up to it); unfinished: at most 8
; ((<id> <summary> <digest> <checker>) ...), the focus first, then the newest first;
; focus: (slice <first 4000 characters> <length>) or none.
(= (input-ledger:statuses)
   ("active" "processed" "waiting-user" "deferred" "addressed" "achieved" "cancelled" "covered"))

(= (input-ledger:count-with-status $view $status)
   (size-atom (collapse (match $view (request $id $q $k $c $status $p $n $sm $dg) $id))))

(= (input-ledger:count-statuses $view $statuses)
   (if (== $statuses ())
       ()
       (let* ((($status $tail) (decons-atom $statuses))
              ($count (input-ledger:count-with-status $view $status))
              ($rest (input-ledger:count-statuses $view $tail)))
         (if (== $count 0) $rest (cons-atom ($status $count) $rest)))))

(= (input-ledger:count-in $counts $status)
   (if (== $counts ())
       0
       (let* ((($head $tail) (decons-atom $counts))
              (($name $count) $head))
         (if (== $name $status) $count (input-ledger:count-in $tail $status)))))

; The newest active requests along the chain from $id, at most $count, not $skip.
(= (input-ledger:newest-active $view $id $count $skip)
   (if (or (<= $count 0) (== $id ""))
       ()
       (let* (($found (input-ledger:heads $view $id))
              ((request $i $q $k $c $st $p $n $sm $dg) (car-atom $found))
              ($before (input-ledger:prev-of $view $id)))
         (if (and (== $st "active") (not (== $i $skip)))
             (cons-atom (car-atom $found) (input-ledger:newest-active $view $before (- $count 1) $skip))
             (input-ledger:newest-active $view $before $count $skip)))))

; ORDER BY (id=focus) DESC, rowid DESC LIMIT 8 among the active requests: the focus if
; it is active, then the newest. $active is how many requests are active in all.
(= (input-ledger:unfinished $view $focus $active)
   (let* (($own (let $found (input-ledger:heads $view $focus)
                  (if (== $found ())
                      ()
                      (let (request $i $q $k $c $st $p $n $sm $dg) (car-atom $found)
                        (if (== $st "active") $found ())))))
          ($room (- 8 (size-atom $own)))
          ($others (input-ledger:newest-active $view (input-ledger:tail-of $view)
                                               (min $room (- $active (size-atom $own))) $focus)))
     (union-atom $own $others)))

(= (input-ledger:unfinished-row (request $id $q $k $c $st $p $n $sm $dg))
   ($id $sm $dg $c))

(= (input-ledger:request-protected $db $focus)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($counts (input-ledger:count-statuses $view (input-ledger:statuses)))
                    ($open (input-ledger:count-in $counts "active"))
                    ($active (input-ledger:unfinished $view $focus $open))
                    ($unfinished (map-atom $active $h (input-ledger:unfinished-row $h)))
                    ($total (input-ledger:counter $view))
                    ($slice (input-ledger:slice-of $view $focus 0))
                    ($_ (db:close $view)))
               (case $slice
                 (((Error $where $why) (Error $where $why))
                  ($other (protected $counts $total $unfinished $slice)))))))))

; Requests whole, oldest first: ((row <id> <content> <status> <checker> <kind> <processed> <note>) ...)
; for $which = all, active, or an expression of ids (each at most once).
(= (input-ledger:row-of $view (request $id $q $k $c $st $p $n $sm $dg))
   (let* (($text (input-ledger:text-one $view $id))
          ($flag (input-ledger:processed-flag $p)))
     (row $id $text $st $c $k $flag $n)))

(= (input-ledger:heads-of-ids $view $ids)
   (if (== $ids ())
       ()
       (let* ((($head $tail) (decons-atom $ids)))
         (union-atom (input-ledger:heads $view $head) (input-ledger:heads-of-ids $view $tail)))))

(= (input-ledger:request-rows $db $which)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($heads (case $which
                              ((all (input-ledger:all-heads $view))
                               (active (input-ledger:heads-with-status $view "active"))
                               ($ids (input-ledger:heads-of-ids $view $ids)))))
                    ($rows (map-atom (input-ledger:sorted-by input-ledger:seq-key $heads) $h (input-ledger:row-of $view $h)))
                    ($_ (db:close $view)))
               $rows)))))

; ---- input notices (storage half of input_ack.key / submitted / receipt) ----

; The notice of a task (at most one: the task is unique), as whole atoms.
(= (input-ledger:notices-of-task $space $task)
   (collapse (match $space (input-notice $task $u $l $s $c $m $p $d $st $ch)
                    (input-notice $task $u $l $s $c $m $p $d $st $ch))))

(= (input-ledger:notices-of-update $space $update)
   (collapse (match $space (input-notice $t $update $l $s $c $m $p $d $st $ch)
                    (input-notice $t $update $l $s $c $m $p $d $st $ch))))

(= (input-ledger:notices-of-send-key $space $key)
   (collapse (match $space (input-notice $t $u $l $key $c $m $p $d $st $ch)
                    (input-notice $t $u $l $key $c $m $p $d $st $ch))))

(= (input-ledger:notices-of-delete-key $space $key)
   (collapse (match $space (input-notice $t $u $l $s $c $m $p $key $st $ch)
                    (input-notice $t $u $l $s $c $m $p $key $st $ch))))

(= (input-ledger:replace $space $old $new)
   (let* (($_ (remove-atom $space $old))
          ($_ (add-atom $space $new)))
     done))

; UPDATE input_notices SET processed=1 WHERE update_id=<id>
(= (input-ledger:process-notices $space $id)
   (input-ledger:process-each $space (input-ledger:notices-of-update $space $id)))

(= (input-ledger:process-each $space $notices)
   (if (== $notices ())
       done
       (let* ((($head $tail) (decons-atom $notices))
              ((input-notice $t $u $l $s $c $m $p $d $st $ch) $head)
              ($_ (input-ledger:replace $space $head (input-notice $t $u $l $s $c $m 1 $d $st $ch))))
         (input-ledger:process-each $space $tail))))

(= (input-ledger:stored $space $id)
   (not (== (collapse (match $space (processed-input $id $time) $time)) ())))

; The refusal rule of key(): an existing notice of the task with another lane
; or send key, or already processed.
(= (input-ledger:refused $notices $lane $send-key)
   (if (== $notices ())
       False
       (let (input-notice $t $u $l $s $c $m $p $d $st $ch) (car-atom $notices)
         (or (not (== $l $lane)) (or (not (== $s $send-key)) (not (== $p 0)))))))

; INSERT OR IGNORE with task and send_key both unique.
(= (input-ledger:insert-notice $space $task $update $lane $send-key $now)
   (if (and (== (input-ledger:notices-of-task $space $task) ())
            (== (input-ledger:notices-of-send-key $space $send-key) ()))
       (add-atom $space (input-notice $task $update $lane $send-key $now none 0 none registered 0))
       done))

(= (input-ledger:register-in $space $task $update $lane $send-key $now)
   (if (input-ledger:stored $space $update)
       ""
       (if (input-ledger:refused (input-ledger:notices-of-task $space $task) $lane $send-key)
           ""
           (let $_ (input-ledger:insert-notice $space $task $update $lane $send-key $now)
             $send-key))))

; The answer of register: the send key or "", else a failure.
(= (input-ledger:register-answer $outcome $send-key)
   (if (or (== $outcome "") (== $outcome $send-key))
       $outcome
       (input-ledger:failure input-ledger:register $outcome)))

; The send key, or "" when the update is processed or the notice conflicts. Any
; failure is (Error ...): the engine's own Error, or (Error input-ledger:register
; <outcome>) for an uncertain or unexpected outcome.
(= (input-ledger:register $db $task $update-id $lane $send-key $now)
   (input-ledger:register-answer
     (input-ledger:write $db input-ledger:register
       (input-ledger:request input-ledger:register
         (str:concat (input-ledger:field $task)
           (str:concat (input-ledger:field $update-id)
             (str:concat (input-ledger:field $lane)
               (str:concat (input-ledger:field $send-key) (input-ledger:field $now))))))
       (|-> ($space) (input-ledger:register-in $space $task $update-id $lane $send-key $now))
       (input-ledger:attempts) none)
     $send-key))

; UPDATE input_notices SET state='submitted' WHERE task=? AND state='registered'
; A task without a registered notice changes nothing: the body gives up (empty).
(= (input-ledger:submit-in $space $notices)
   (if (== $notices ())
       (empty)
       (let (input-notice $t $u $l $s $c $m $p $d $st $ch) (car-atom $notices)
         (if (== $st registered)
             (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                          (input-notice $t $u $l $s $c $m $p $d submitted $ch))
             (empty)))))

; True, or a failure shaped like mark-processed's.
(= (input-ledger:submitted $db $task)
   (input-ledger:done-answer input-ledger:submitted
     (input-ledger:unchanged
       (input-ledger:write $db input-ledger:submitted
         (input-ledger:request input-ledger:submitted (input-ledger:field $task))
         (|-> ($space) (input-ledger:submit-in $space (input-ledger:notices-of-task $space $task)))
         (input-ledger:attempts) none)
       done)))

; An integer is a Number whose text equals that of its floor (5 yes; 5.0, 1.5,
; True and "5" no), as Python's `type(x) is int` in receipt().
(= (input-ledger:integer-value $value)
   (if (== (get-type $value) Number)
       (== (repr $value) (repr (floor-math $value)))
       False))

(= (input-ledger:notice-for-key $space $key)
   (let $found (input-ledger:notices-of-send-key $space $key)
     (if (== $found ())
         (input-ledger:notices-of-delete-key $space $key)
         $found)))

; Same rules as receipt(), after its envelope checks. $outcome is the outcome
; element of the delivery observation as an expression: (delivered <int>) for
; Python's ["delivered", <int>]; any other atom is failed or uncertain evidence.
(= (input-ledger:receipt-in $space $key $outcome)
   (let $found (input-ledger:notice-for-key $space $key)
     (if (== $found ())
         (empty)
         (case $outcome
           (((delivered $id)
             (if (input-ledger:integer-value $id)
                 (input-ledger:receipt-row $space $key (car-atom $found) $id)
                 1))
            ($_ 1))))))

(= (input-ledger:receipt-row $space $key
                             (input-notice $t $u $l $s $c $m $p $d $st $ch) $id)
   (if (and (== $key $s) (> $id 0))
       (if (or (== $m none) (== $m $id))
           (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                (input-notice $t $u $l $s $c $id $p $d $st $ch))
             1)
           (empty))
       (if (and (== $key $d) (and (not (== $p 0)) (or (== $id 0) (== $id $m))))
           (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                (input-notice $t $u $l $s $c $m $p $d deleted $ch))
             1)
           (empty))))

; The answer of receipt: the number 1 or 0, else a failure. An Error is told
; apart first: get-type of an Error atom is not a single type.
(= (input-ledger:receipt-answer $outcome)
   (case $outcome
     (((Error $where $why) $outcome)
      ($other (input-ledger:receipt-number $other)))))

(= (input-ledger:receipt-number $outcome)
   (if (and (== (get-type $outcome) Number)
            (or (== (repr $outcome) "1") (== (repr $outcome) "0")))
       $outcome
       (input-ledger:failure input-ledger:receipt $outcome)))

; 1, or 0 for a refused receipt (a body that gives up (empty): see unchanged).
; Any failure is (Error ...): the engine's own Error, or (Error
; input-ledger:receipt <outcome>) for an uncertain or unexpected outcome.
(= (input-ledger:receipt $db $key $outcome)
   (input-ledger:receipt-answer
     (input-ledger:unchanged
       (input-ledger:write $db input-ledger:receipt
         (input-ledger:request input-ledger:receipt
           (str:concat (input-ledger:field $key) (input-ledger:field $outcome)))
         (|-> ($space) (input-ledger:receipt-in $space $key $outcome))
         (input-ledger:attempts) none)
       0)))

; ---- the receipt-driven cleanup of channels/telegram_notice_cleanup.metta ----
; The live cleanup's own rules, one verb per statement of its SQL, apart from receipt, due
; and check (input_ack.pump's): an operator control's notice is withdrawn and then deleted
; unprocessed; a delete key is made per attempt, an existing one adopted, and cleared after a
; definite non-delivery. Each write carries the caller's nonce in its request: a lost reply is
; repeated with it, a later call is evaluated anew, as each SQL statement is.

(= (input-ledger:open-notice $state)
   (or (== $state registered) (or (== $state submitted) (== $state withdrawing))))

; UPDATE input_notices SET state='withdrawing' WHERE task=? AND state IN ('registered','submitted')
(= (input-ledger:withdraw-in $space $task)
   (let $found (input-ledger:notices-of-task $space $task)
     (if (== $found ()) True (input-ledger:withdraw-row $space (car-atom $found)))))

(= (input-ledger:withdraw-row $space (input-notice $t $u $l $s $c $m $p $d $st $ch))
   (if (or (== $st registered) (== $st submitted))
       (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                            (input-notice $t $u $l $s $c $m $p $d withdrawing $ch))
         True)
       True))

(= (input-ledger:notice-withdraw $db $task $nonce)
   (input-ledger:write $db input-ledger:notice-withdraw
     (input-ledger:request input-ledger:notice-withdraw (str:concat (input-ledger:field $task) (input-ledger:field $nonce)))
     (|-> ($space) (input-ledger:withdraw-in $space $task))
     (input-ledger:attempts) none))

; A delivery receipt for <key>, the one notice whose send or delete key it is (none or two:
; False). <outcome>: (delivered <message-id>), not-sent, or other.
;   send key, delivered id > 0, no message id or the same: record the message id;
;   delete key, delivered 0 or the message id, processed or withdrawing: deleted;
;   delete key, not-sent: the delete key is cleared, so the next attempt makes a new one.
(= (input-ledger:delivery-in $space $key $outcome)
   (let $rows (unique-atom (union-atom (input-ledger:notices-of-send-key $space $key)
                                       (input-ledger:notices-of-delete-key $space $key)))
     (if (== (size-atom $rows) 1) (input-ledger:delivery-row $space $key $outcome (car-atom $rows)) False)))

(= (input-ledger:delivery-row $space $key $outcome (input-notice $t $u $l $s $c $m $p $d $st $ch))
   (case $outcome
     (((delivered $id)
       (if (== $key $s)
           (if (and (> $id 0) (or (== $m none) (== $m $id)))
               (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                    (input-notice $t $u $l $s $c $id $p $d $st $ch))
                 True)
               False)
           (if (and (or (== $p 1) (== $st withdrawing)) (or (== $id 0) (== $id $m)))
               (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                    (input-notice $t $u $l $s $c $m $p $d deleted $ch))
                 True)
               False)))
      (not-sent
       (if (== $key $d)
           (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                (input-notice $t $u $l $s $c $m $p none $st $ch))
             True)
           True))
      ($other True))))

(= (input-ledger:notice-delivery $db $key $outcome $nonce)
   (input-ledger:write $db input-ledger:notice-delivery
     (input-ledger:request input-ledger:notice-delivery
       (str:concat (input-ledger:field $key) (str:concat (input-ledger:field $outcome) (input-ledger:field $nonce))))
     (|-> ($space) (input-ledger:delivery-in $space $key $outcome))
     (input-ledger:attempts) none))

; UPDATE input_notices SET delete_key=COALESCE(delete_key,?) WHERE task=? AND state IN
; ('registered','submitted','withdrawing'), then the stored delete key (none: no notice, or
; none stored).
(= (input-ledger:cleanup-key-in $space $task $candidate)
   (let $found (input-ledger:notices-of-task $space $task)
     (if (== $found ()) none (input-ledger:cleanup-key-row $space (car-atom $found) $candidate))))

(= (input-ledger:cleanup-key-row $space (input-notice $t $u $l $s $c $m $p $d $st $ch) $candidate)
   (if (and (== $d none) (input-ledger:open-notice $st))
       (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                            (input-notice $t $u $l $s $c $m $p $candidate $st $ch))
         $candidate)
       $d))

(= (input-ledger:notice-cleanup-key $db $task $candidate $nonce)
   (input-ledger:write $db input-ledger:notice-cleanup-key
     (input-ledger:request input-ledger:notice-cleanup-key
       (str:concat (input-ledger:field $task) (str:concat (input-ledger:field $candidate) (input-ledger:field $nonce))))
     (|-> ($space) (input-ledger:cleanup-key-in $space $task $candidate))
     (input-ledger:attempts) none))

; The open notices with a message that is processed or withdrawing, oldest first, each as
; (task lane message-id delete-key created): the delete key already stored (none when there
; is none), so a caller asks for one only when it lacks it, and the creation time, so a caller
; can tell a notice too old to delete.
(= (input-ledger:deletable-key ($created $row)) $created)
(= (input-ledger:deletable-entry (input-notice $t $u $l $s $c $m $p $d $st $ch))
   (if (and (input-ledger:open-notice $st) (and (not (== $m none)) (or (== $p 1) (== $st withdrawing))))
       ($c ($t $l $m $d $c))
       (empty)))

(= (input-ledger:deletable-of $view)
   (let $entries (collapse (match $view (input-notice $t $u $l $s $c $m $p $d $st $ch)
                             (input-ledger:deletable-entry (input-notice $t $u $l $s $c $m $p $d $st $ch))))
     (map-atom (sort-atom $entries) $entry (input-ledger:second $entry))))

; ---- topics, historical model jobs and provider notices ----
;   (ledger:topic <name> <revision> <notes>)                  topic_contexts; the active topic is
;                                                             the meta key active_topic (JSON text)
;   (ledger:model-job <id> <topic> <state> <result> <order>)  model_jobs, read only (nothing writes
;                                                             them; <order> is the row's rowid)
;   (ledger:provider-notice <episode> <intent> <queued>)      provider_notices (<queued> 0 or 1)
; As the SQLite path makes them on first use, the topic conversation and active_topic
; "conversation" exist before anything is stored: reads supply them, writes add them.

(= (input-ledger:topic-write $db $verb $fields $body)
   (input-ledger:write $db $verb (input-ledger:request $verb (input-ledger:fields $fields)) $body
     (input-ledger:attempts) none))

(= (input-ledger:topic-of $view $name)
   (case (collapse (match $view (ledger:topic $name $r $n) ($r $n)))
     (((($revision $notes)) ($name $revision $notes))
      ($other (if (== $name "conversation") ($name 0 "") none)))))

(= (input-ledger:active-topic-of $view)
   (case (collapse (match $view (ledger:meta "active_topic" $text) $text))
     ((($text) (case (catch (json-parse $text)) (((JsonString $name) $name) ($other none))))
      ($other "conversation"))))

(= (input-ledger:set-meta-in $space $key $new)
   (case (collapse (match $space (ledger:meta $key $text) $text))
     ((() (add-atom $space (ledger:meta $key $new)))
      (($text) (if (== $text $new) done (input-ledger:replace $space (ledger:meta $key $text) (ledger:meta $key $new))))
      ($other (Error input-ledger:meta (several $key))))))

; INSERT OR IGNORE INTO topic_contexts VALUES ('conversation',0,'');
; INSERT OR IGNORE INTO meta VALUES ('active_topic','"conversation"')
(= (input-ledger:topic-init-in $space)
   (let* (($_ (if (== (collapse (match $space (ledger:topic "conversation" $r $n) $r)) ())
                  (add-atom $space (ledger:topic "conversation" 0 "")) done))
          ($_ (if (== (collapse (match $space (ledger:meta "active_topic" $t) $t)) ())
                  (add-atom $space (ledger:meta "active_topic" "\"conversation\"")) done)))
     done))

; topic-select: the topic is added (revision 0, no notes) unless it exists, and made active.
(= (input-ledger:topic-select-in $space $name)
   (let* (($_ (input-ledger:topic-init-in $space))
          ($_ (if (== (collapse (match $space (ledger:topic $name $r $n) $r)) ())
                  (add-atom $space (ledger:topic $name 0 "")) done))
          ($_ (input-ledger:set-meta-in $space "active_topic" (json-stringify (JsonString $name)))))
     True))

; topic-note: the active topic's notes replaced and its revision raised; its name, or none.
(= (input-ledger:topic-note-in $space $text)
   (let* (($_ (input-ledger:topic-init-in $space))
          ($name (input-ledger:active-topic-of $space)))
     (case (collapse (match $space (ledger:topic $name $r $n) ($r $n)))
       (((($revision $notes))
         (let $_ (input-ledger:replace $space (ledger:topic $name $revision $notes) (ledger:topic $name (+ $revision 1) $text))
           $name))
        ($other none)))))

; (ledger:topics <active name> ((<name> <revision>) ...)), names in order. The head is the
; ledger's own: the agent's (topics) is a function, which an answer (topics ...) would call.
(= (input-ledger:topics-of $view)
   (let* (($stored (collapse (match $view (ledger:topic $name $r $n) ($name $r))))
          ($all (if (== (collapse (match $view (ledger:topic "conversation" $r $n) $r)) ())
                    (cons-atom ("conversation" 0) $stored) $stored)))
     (ledger:topics (input-ledger:active-topic-of $view) (sort-atom $all))))

; The active topic's (<name> <notes>), or none.
(= (input-ledger:topic-context-of $view)
   (case (input-ledger:topic-of $view (input-ledger:active-topic-of $view))
     ((($name $r $n) ($name $n)) ($other none))))

; ((<id> <topic> <state>) ...), newest first; one job's (<id> <topic> <state> <result>), or none.
(= (input-ledger:job-order ($order $row)) $order)
(= (input-ledger:model-jobs-of $view)
   (let $rows (collapse (match $view (ledger:model-job $id $t $st $res $o) ($o ($id $t $st))))
     (list:reverse (map-atom (sort-atom $rows) $row (input-ledger:second $row)))))
(= (input-ledger:model-job-of $view $id)
   (case (collapse (match $view (ledger:model-job $id $t $st $res $o) ($id $t $st $res)))
     ((($row) $row) ($other none))))

; (families <topics> <model jobs> <provider notices> <active topic records>): how many are stored.
(= (input-ledger:families-census-of $view)
   (families (size-atom (collapse (match $view (ledger:topic $n $r $t) $n)))
             (size-atom (collapse (match $view (ledger:model-job $i $t $st $res $o) $i)))
             (size-atom (collapse (match $view (ledger:provider-notice $e $i $q) $e)))
             (size-atom (collapse (match $view (ledger:meta "active_topic" $text) $text)))))

; (<intent> <queued>) of an episode's notice, or none.
(= (input-ledger:provider-notice-of $view $episode)
   (case (collapse (match $view (ledger:provider-notice $episode $i $q) ($i $q)))
     ((($row) $row) ($other none))))

; The episode's intent is written once: the stored one is answered, else this one is stored.
(= (input-ledger:provider-reserve-in $space $episode $intent)
   (case (input-ledger:provider-notice-of $space $episode)
     ((none (let $_ (add-atom $space (ledger:provider-notice $episode $intent 0)) ($intent 0)))
      ($row $row))))

(= (input-ledger:provider-queued-in $space $episode)
   (case (collapse (match $space (ledger:provider-notice $episode $i $q) ($i $q)))
     (((($i 0)) (let $_ (input-ledger:replace $space (ledger:provider-notice $episode $i 0) (ledger:provider-notice $episode $i 1)) True))
      ($other True))))

; ---- the store half of input_ack.pump() ----

; Notices in state registered or submitted, ordered by processed descending,
; then checked, then created (as ORDER BY processed DESC,checked,created); a
; tie on all three is broken by task, which SQLite leaves unspecified.
(= (input-ledger:due-first (input-notice $t1 $u1 $l1 $s1 $c1 $m1 $p1 $d1 $st1 $ch1)
                           (input-notice $t2 $u2 $l2 $s2 $c2 $m2 $p2 $d2 $st2 $ch2))
   (if (> $p1 $p2) True
     (if (< $p1 $p2) False
       (if (< $ch1 $ch2) True
         (if (> $ch1 $ch2) False
           (if (< $c1 $c2) True
             (if (> $c1 $c2) False
                 (<= (str:compare $t1 $t2) 0))))))))

(= (input-ledger:due-notices $view)
   (collapse (superpose
     ((match $view (input-notice $t $u $l $s $c $m $p $d registered $ch)
              (input-notice $t $u $l $s $c $m $p $d registered $ch))
      (match $view (input-notice $t $u $l $s $c $m $p $d submitted $ch)
              (input-notice $t $u $l $s $c $m $p $d submitted $ch))))))

; At most $limit notices, read from one snapshot, as an expression of whole
; input-notice records (see the top of this file): ((input-notice ...) ...), ()
; when there are none. A snapshot failure answers the snapshot's Error atom
; itself (Error db:snapshot ...), not an expression of notices. A limit of 0 or
; less gives () (SQLite's LIMIT -1 would mean no limit).
(= (input-ledger:due $db $limit)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($rows (list:take $limit (list:sort-by input-ledger:due-first
                                                           (input-ledger:due-notices $view))))
                    ($_ (db:close $view)))
               $rows)))))

; One pump step for one task, in one transaction (pump runs three: checked,
; expiry, delete key). checked is set to $now first; then
;   $now - created > $max-age        -> state expired, answer expired
;   no message id, or not processed  -> answer wait
;   otherwise                        -> delete_key stored, answer
;                                       (delete <delete-key> <message-id>)
; The state of the notice is not looked at, as in pump: its UPDATEs name only
; the task. A task without a notice changes nothing and answers wait.
(= (input-ledger:check-row $space
                           (input-notice $t $u $l $s $c $m $p $d $st $ch) $now $max-age)
   (if (> (- $now $c) $max-age)
       (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                            (input-notice $t $u $l $s $c $m $p $d expired $now))
         expired)
       (if (or (== $m none) (== $p 0))
           (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                (input-notice $t $u $l $s $c $m $p $d $st $now))
             wait)
           (let $key (input-ledger:delete-key $l $s)
             (let $_ (input-ledger:replace $space (input-notice $t $u $l $s $c $m $p $d $st $ch)
                                                  (input-notice $t $u $l $s $c $m $p $key $st $now))
               (delete $key $m))))))

(= (input-ledger:check-in $space $task $now $max-age)
   (let $found (input-ledger:notices-of-task $space $task)
     (if (== $found ())
         (empty)
         (input-ledger:check-row $space (car-atom $found) $now $max-age))))

; The answer of check: expired, wait or (delete <string> <message-id>), else a
; failure.
(= (input-ledger:check-answer $outcome)
   (case $outcome
     ((expired expired)
      (wait wait)
      ((delete $key $id)
       (if (== (get-type $key) String)
           $outcome
           (input-ledger:failure input-ledger:check $outcome)))
      ($other (input-ledger:failure input-ledger:check $other)))))

(= (input-ledger:check $db $task $now $max-age)
   (input-ledger:check-answer
     (input-ledger:unchanged
       (input-ledger:write $db input-ledger:check
         (input-ledger:request input-ledger:check
           (str:concat (input-ledger:field $task)
             (str:concat (input-ledger:field $now) (input-ledger:field $max-age))))
         (|-> ($space) (input-ledger:check-in $space $task $now $max-age))
         (input-ledger:attempts) none)
       wait)))

; ---- migration: copy rows into an empty store, read them back ----

; Used by scripts/migrate_input_ledger.py, never by input_ack.py. A row is a
; whole record as the top of this file describes it: (processed-input ...) or
; (input-notice ...), built by the caller from the SQLite row.

; True when the collection holds no atom at all.
(= (input-ledger:vacant $space)
   (== (collapse (match $space $atom $atom)) ()))

(= (input-ledger:add-all $space $rows)
   (if (== $rows ())
       done
       (let* ((($head $tail) (decons-atom $rows))
              ($_ (add-atom $space $head)))
         (input-ledger:add-all $space $tail))))

; With $expect = empty the batch is added only to a collection without any atom
; (the check and the writes are one transaction); with any it is added to
; whatever is there. The same batch, repeated, is the same write (its request
; id is built from the batch). A refused batch changes nothing.
(= (input-ledger:import-in $space $expect $rows)
   (if (and (== $expect empty) (not (input-ledger:vacant $space)))
       (empty)
       (input-ledger:add-all $space $rows)))

(= (input-ledger:import-answer $outcome)
   (case $outcome
     ((done True)
      (refused refused)
      ($other (input-ledger:failure input-ledger:import $other)))))

; True when committed, refused when the store was not empty, else a failure.
(= (input-ledger:import $db $expect $rows)
   (input-ledger:import-answer
     (input-ledger:unchanged
       (input-ledger:write $db input-ledger:import
         (input-ledger:request input-ledger:import
           (str:concat (input-ledger:field $expect) (input-ledger:field $rows)))
         (|-> ($space) (input-ledger:import-in $space $expect $rows))
         (input-ledger:attempts) none)
       refused)))

; (census <processed ids> <notices> <requests> <atoms of any other kind>), from one snapshot.
(= (input-ledger:census $db)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($processed (size-atom (collapse (match $view (processed-input $id $time) $id))))
                    ($notices (size-atom (collapse (match $view (input-notice $a $b $c $d $e $f $g $h $i $j) $a))))
                    ($requests (size-atom (input-ledger:all-heads $view)))
                    ($all (size-atom (collapse (match $view $atom $atom))))
                    ($_ (db:close $view)))
               (census $processed $notices $requests (- $all (+ $processed (+ $notices $requests)))))))))

(= (input-ledger:lookup-processed $view $ids)
   (if (== $ids ())
       ()
       (let* ((($head $tail) (decons-atom $ids)))
         (union-atom (collapse (match $view (processed-input $head $time) (processed-input $head $time)))
                     (input-ledger:lookup-processed $view $tail)))))

; A request: its record and its content, as whole atoms.
(= (input-ledger:lookup-requests $view $ids)
   (if (== $ids ())
       ()
       (let* ((($head $tail) (decons-atom $ids)))
         (union-atom (union-atom (union-atom (input-ledger:heads $view $head)
                                             (collapse (match $view (request-text $head $text) (request-text $head $text))))
                                 (collapse (match $view (request-prev $head $before) (request-prev $head $before))))
                     (input-ledger:lookup-requests $view $tail)))))

(= (input-ledger:lookup-notices $view $tasks)
   (if (== $tasks ())
       ()
       (let* ((($head $tail) (decons-atom $tasks)))
         (union-atom (input-ledger:notices-of-task $view $head)
                     (input-ledger:lookup-notices $view $tail)))))

; Every record stored under one of the ids (kind processed), tasks (kind
; notice) or request ids (kind request: the record, its content and its link), as an
; expression of whole records, from one snapshot.
(= (input-ledger:lookup $db $kind $keys)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($found (if (== $kind processed)
                                (input-ledger:lookup-processed $view $keys)
                                (if (== $kind request)
                                    (input-ledger:lookup-requests $view $keys)
                                    (input-ledger:lookup-notices $view $keys))))
                    ($_ (db:close $view)))
               $found)))))

; ---- the entry of the worker that input_ack.py keeps ----

; input_ack.py runs this module in a persistent cetta-value-worker (module
; src/input_ledger.metta, entry input-ledger:run) and sends one request per
; ledger call:
;
;   (ledger "<socket>" "<verb>" (<argument> ...))
;
; with the verbs and arguments committed ($ids), mark-processed ($now $ids),
; checkpoint ($working $ids $request $now $request-id), working-set (none),
; register ($task $update-id $lane $send-key $now), submitted ($task), receipt
; ($key $outcome), due ($limit), check ($task $now $max-age) and ensure; and, for
; the migration script, import ($expect $rows), census and lookup ($kind $keys).
; The reply is (answer <value>) with the verb's own value, or an Error atom as
; the verb returns it as the value.
;
; Every attempt opens its own connection with db:connect, runs input-ledger:ensure
; and then the verb, and closes the connection. A dead connection is never
; reused: when a connection is lost (an unavailable answer) or a reply is lost
; (an uncertain outcome), the whole request is run again on a NEW connection,
; with the very same arguments, so the same $now and so the same request ids:
; a write that did commit is replayed, not applied twice. Up to
; input-ledger:sessions attempts in all. A failed connect (nobody listens) is
; answered at once, as is any other failure.
(= (input-ledger:sessions) 3)

(= (input-ledger:lost $outcome)
   (case $outcome
     (((Error $where unavailable) True)
      ((Error $where uncertain) True)
      ((Error $where (db:uncertain $request-id $collection $operation)) True)
      ($other False))))

; A read's answer in pages, for answers larger than one wire value carries (65536 nodes): a
; list answer is cut to its rows [start, start + count); a record's head and scalar fields are
; the same on every page and each of its expression fields is cut so. (page more <value>) has
; rows after the page, (page last <value>) none. Only reads are recomputed for each page.
(= (input-ledger:pageable $verb)
   (case $verb
     (("request-rows" True) ("actions" True) ("attempts" True) ("task-view" True) ("task-rows" True)
      ("conversation-rows" True) ("conversation-history" True) ("turn-dump" True) ("meta-prefix" True)
      ($other (llm-ledger:pageable $other)))))

(= (input-ledger:rows-answer $answer)
   (if (== $answer ()) True (== (get-metatype (car-atom $answer)) Expression)))

(= (input-ledger:page-cut $rows $start $count)
   (let $rest (list:drop $start $rows)
     ((list:take $count $rest) (> (size-atom $rest) $count))))

(= (input-ledger:cut-fields $fields $start $count $out $more)
   (if (== $fields ()) ((list:reverse $out) $more)
     (let* ((($field $rest) (decons-atom $fields)))
       (if (== (get-metatype $field) Expression)
           (let ($kept $beyond) (input-ledger:page-cut $field $start $count)
             (input-ledger:cut-fields $rest $start $count (cons-atom $kept $out) (or $more $beyond)))
           (input-ledger:cut-fields $rest $start $count (cons-atom $field $out) $more)))))

(= (input-ledger:page-of $answer $start $count)
   (if (== (get-metatype $answer) Expression)
       (if (input-ledger:rows-answer $answer)
           (let ($kept $beyond) (input-ledger:page-cut $answer $start $count)
             (page (if $beyond more last) $kept))
           (case $answer
             (((Error $where $why) $answer)
              ($record (let* ((($head $fields) (decons-atom $record))
                              (($kept $beyond) (input-ledger:cut-fields $fields $start $count () False)))
                         (page (if $beyond more last) (cons-atom $head $kept)))))))
       (page last $answer)))

!(change-state! input-ledger:page-cache ())
!(change-state! input-ledger:page-computations 0)
!(change-state! input-ledger:page-cache-hits 0)

(= (input-ledger:page-cache-max-entries) 4)
(= (input-ledger:page-cache-ttl-ns) 60000000000)

(= (input-ledger:store-version $db)
   (case (store:snapshot $db)
     (((StoreSnapshot $view $epoch $revision)
       (let $_ (store:close $view) ($epoch $revision)))
      ($other none))))

(= (input-ledger:cache-live $entries $now $out)
   (if (== $entries ()) (list:reverse $out)
     (let ((PageCacheEntryV1 $rid $ver $v $args $exp $ans) $rest) (decons-atom $entries)
       (if (<= $now $exp)
           (input-ledger:cache-live $rest $now (cons-atom (PageCacheEntryV1 $rid $ver $v $args $exp $ans) $out))
           (input-ledger:cache-live $rest $now $out)))))

(= (input-ledger:cache-without $entries $read-id $version $verb $arguments $out)
   (if (== $entries ()) (list:reverse $out)
     (let ((PageCacheEntryV1 $rid $ver $v $args $exp $ans) $rest) (decons-atom $entries)
       (if (and (and (== $v $verb) (== $args $arguments))
                (or (and (!= $read-id none) (== $rid $read-id))
                    (and (== $rid none) (== $ver $version))))
           (input-ledger:cache-without $rest $read-id $version $verb $arguments $out)
           (input-ledger:cache-without $rest $read-id $version $verb $arguments
             (cons-atom (PageCacheEntryV1 $rid $ver $v $args $exp $ans) $out))))))

(= (input-ledger:cache-put $read-id $version $verb $arguments $answer)
   (let* (($now (system:monotonic-ns))
          ($live (input-ledger:cache-live (get-state input-ledger:page-cache) $now ()))
          ($filtered (input-ledger:cache-without $live $read-id $version $verb $arguments ()))
          ($entry (PageCacheEntryV1 $read-id $version $verb $arguments
                    (+ $now (input-ledger:page-cache-ttl-ns)) $answer))
          ($bounded (list:take (input-ledger:page-cache-max-entries) (cons-atom $entry $filtered)))
          ($_ (change-state! input-ledger:page-cache $bounded)))
     $answer))

(= (input-ledger:cache-find $entries $read-id $version $verb $arguments)
   (if (== $entries ()) none
     (let ((PageCacheEntryV1 $rid $ver $v $args $exp $ans) $rest) (decons-atom $entries)
       (if (and (== $v $verb) (== $args $arguments))
           (if (and (!= $read-id none) (== $rid $read-id))
               (some $rid $ver $ans)
               (if (and (!= $version none) (== $ver $version))
                   (some $rid $ver $ans)
                   (input-ledger:cache-find $rest $read-id $version $verb $arguments)))
           (input-ledger:cache-find $rest $read-id $version $verb $arguments)))))

(= (input-ledger:compute-pageable $db $read-id $version $verb $arguments)
   (let* (($_ (change-state! input-ledger:page-computations (+ (get-state input-ledger:page-computations) 1)))
          ($answer (input-ledger:perform $db $verb $arguments)))
     (input-ledger:cache-put $read-id $version $verb $arguments $answer)))

(= (input-ledger:cached-answer $db $read-id $verb $arguments)
   (let* (($now (system:monotonic-ns))
          ($version (input-ledger:store-version $db))
          ($live (input-ledger:cache-live (get-state input-ledger:page-cache) $now ()))
          ($_ (change-state! input-ledger:page-cache $live)))
     (case (input-ledger:cache-find $live $read-id $version $verb $arguments)
       (((some $rid $ver $answer)
         (let* (($_ (change-state! input-ledger:page-cache-hits (+ (get-state input-ledger:page-cache-hits) 1)))
                ($_ (if (and (!= $read-id none) (== $rid none))
                        (input-ledger:cache-put $read-id $ver $verb $arguments $answer)
                        $answer)))
           $answer))
        ($miss (input-ledger:compute-pageable $db $read-id $version $verb $arguments))))))

(= (input-ledger:call $db $verb $arguments)
   (if (input-ledger:pageable $verb)
       (input-ledger:compute-pageable $db none (input-ledger:store-version $db) $verb $arguments)
       (input-ledger:perform $db $verb $arguments)))

(= (input-ledger:fields-remaining $fields $start $best)
   (if (== $fields ()) $best
     (let ($field $rest) (decons-atom $fields)
       (if (== (get-metatype $field) Expression)
           (let* (($rem (- (size-atom $field) $start))
                  ($next (if (> $rem $best) $rem $best)))
             (input-ledger:fields-remaining $rest $start $next))
           (input-ledger:fields-remaining $rest $start $best)))))

(= (input-ledger:remaining-rows $answer $start)
   (if (== (get-metatype $answer) Expression)
       (if (input-ledger:rows-answer $answer)
           (let $rem (- (size-atom $answer) $start) (if (> $rem 0) $rem 0))
           (case $answer
             (((Error $where $why) 0)
              ($record (let ($head $fields) (decons-atom $record)
                         (input-ledger:fields-remaining $fields $start 0))))))
       0))

(= (input-ledger:idiv $a $b)
   (let $rem (% $a $b)
     (/ (- $a $rem) $b)))

(= (input-ledger:page-fits $answer $start $take $limit)
   (case (store:encode (input-ledger:page-of $answer $start $take))
     (((StoreEncoded $bytes) (<= (str:byte-length $bytes) $limit))
      ($other False))))

(= (input-ledger:fit-range $answer $start $best $low $high $limit)
   (if (> $low $high)
       (input-ledger:page-of $answer $start $best)
       (let $mid (input-ledger:idiv (+ $low $high) 2)
         (if (input-ledger:page-fits $answer $start $mid $limit)
             (input-ledger:fit-range $answer $start $mid (+ $mid 1) $high $limit)
             (input-ledger:fit-range $answer $start $best $low (- $mid 1) $limit)))))

(= (input-ledger:page-budgeted $answer $start $count $budget)
   (let* (($avail (input-ledger:remaining-rows $answer $start))
          ($upper (if (<= $avail 0) 1 (if (< $avail $count) $avail $count)))
          ($full (input-ledger:page-of $answer $start $upper)))
     (if (or (<= $budget 0) (<= $upper 1))
         $full
         (let $limit (if (> $budget 256) (- $budget 256) $budget)
           (case (store:encode $full)
             (((StoreEncoded $bytes)
               (let $total (str:byte-length $bytes)
                 (if (<= $total $limit)
                     $full
                     (let* (($raw (input-ledger:idiv (* $upper $limit) $total))
                            ($guess (if (< $raw 1) 1 (if (>= $raw $upper) (- $upper 1) $raw))))
                       (if (input-ledger:page-fits $answer $start $guess $limit)
                           (input-ledger:fit-range $answer $start $guess (+ $guess 1) (- $upper 1) $limit)
                           (input-ledger:fit-range $answer $start 1 1 (- $guess 1) $limit))))))
              ($other $full)))))))

(= (input-ledger:page-read $db $verb $arguments $start $count $budget $read-id)
   (if (input-ledger:pageable $verb)
       (input-ledger:page-budgeted (input-ledger:cached-answer $db $read-id $verb $arguments) $start $count $budget)
       (Error input-ledger:page (not-a-read $verb))))

(= (input-ledger:page $db $verb $arguments $start $count)
   (input-ledger:page-read $db $verb $arguments $start $count 0 none))

(= (input-ledger:perform $db $verb $arguments)
   (case ($verb $arguments)
     ((("page-cache-stats" ())
       (page-cache-stats (get-state input-ledger:page-computations)
                         (get-state input-ledger:page-cache-hits)
                         (size-atom (get-state input-ledger:page-cache))))
      (("committed" ($ids)) (input-ledger:committed $db $ids))
      (("mark-processed" ($now $ids)) (input-ledger:mark-processed $db $ids $now))
      (("register" ($task $update-id $lane $send-key $now))
       (input-ledger:register $db $task $update-id $lane $send-key $now))
      (("checkpoint" ($working $ids $request $now $request-id))
       (input-ledger:checkpoint $db $working $ids $request $now $request-id))
      (("working-set" ()) (input-ledger:working-set $db))
      (("request-accept" ($id $content $checker $kind $summary $digest $request-id))
       (input-ledger:request-accept $db $id $content $checker $kind $summary $digest $request-id))
      (("request-settle" ($id $now $request-id)) (input-ledger:request-settle $db $id $now $request-id))
      (("request-disposition" ($id $disposition $note $request-id))
       (input-ledger:request-disposition $db $id $disposition $note $request-id))
      (("request-cancel" ($id $request-id))
       (input-ledger:request-move $db input-ledger:request-cancel $id ("active") "cancelled" $request-id))
      (("request-finish" ($id $request-id))
       (input-ledger:request-move $db input-ledger:request-finish $id ("active") "achieved" $request-id))
      (("request-answered" ($id $request-id))
       (input-ledger:request-move $db input-ledger:request-answered $id ("active" "processed") "addressed" $request-id))
      (("request-retire" ($prefix $note $request-id)) (input-ledger:request-retire $db $prefix $note $request-id))
      (("request-get" ($id $with)) (input-ledger:request-get $db $id $with))
      (("request-record" ($id $offset)) (input-ledger:request-record $db $id $offset))
      (("request-head-observation" ($id))
       (input-ledger:task-read $db input-ledger:request-head-observation
         (|-> ($view)
           (let* (($owner (input-ledger:state-of $view "owner"))
                  ($heads (input-ledger:heads $view $id))
                  ($head (if (== $heads ()) none (car-atom $heads))))
             (RequestHeadObservationV1 $owner $head)))))
      (("request-page" ($offset $limit)) (input-ledger:request-page $db $offset $limit))
      (("request-protected" ($focus)) (input-ledger:request-protected $db $focus))
      (("request-rows" ($which)) (input-ledger:request-rows $db $which))
      (("submitted" ($task)) (input-ledger:submitted $db $task))
      (("receipt" ($key $outcome)) (input-ledger:receipt $db $key $outcome))
      (("notice-withdraw" ($task $nonce)) (input-ledger:notice-withdraw $db $task $nonce))
      (("notice-delivery" ($key $outcome $nonce)) (input-ledger:notice-delivery $db $key $outcome $nonce))
      (("notice-cleanup-key" ($task $candidate $nonce)) (input-ledger:notice-cleanup-key $db $task $candidate $nonce))
      (("topic-select" ($name $nonce)) (input-ledger:topic-write $db input-ledger:topic-select ($name $nonce)
                                         (|-> ($space) (input-ledger:topic-select-in $space $name))))
      (("topic-note" ($text $nonce)) (input-ledger:topic-write $db input-ledger:topic-note ($text $nonce)
                                       (|-> ($space) (input-ledger:topic-note-in $space $text))))
      (("topics" ()) (input-ledger:task-read $db input-ledger:topics (|-> ($view) (input-ledger:topics-of $view))))
      (("topic-context" ()) (input-ledger:task-read $db input-ledger:topic-context
                              (|-> ($view) (input-ledger:topic-context-of $view))))
      (("families-census" ()) (input-ledger:task-read $db input-ledger:families-census
                                (|-> ($view) (input-ledger:families-census-of $view))))
      (("model-jobs" ()) (input-ledger:task-read $db input-ledger:model-jobs (|-> ($view) (input-ledger:model-jobs-of $view))))
      (("model-job" ($id)) (input-ledger:task-read $db input-ledger:model-job (|-> ($view) (input-ledger:model-job-of $view $id))))
      (("provider-notice" ($episode)) (input-ledger:task-read $db input-ledger:provider-notice
                                        (|-> ($view) (input-ledger:provider-notice-of $view $episode))))
      (("provider-notice-reserve" ($episode $intent $nonce))
       (input-ledger:topic-write $db input-ledger:provider-notice-reserve ($episode $intent $nonce)
         (|-> ($space) (input-ledger:provider-reserve-in $space $episode $intent))))
      (("provider-notice-queued" ($episode $nonce))
       (input-ledger:topic-write $db input-ledger:provider-notice-queued ($episode $nonce)
         (|-> ($space) (input-ledger:provider-queued-in $space $episode))))
      (("notice-deletable" ())
       (input-ledger:task-read $db input-ledger:notice-deletable (|-> ($view) (input-ledger:deletable-of $view))))
      (("due" ($limit)) (input-ledger:due $db $limit))
      (("check" ($task $now $max-age)) (input-ledger:check $db $task $now $max-age))
      (("ensure" ()) True)
      (("import" ($expect $rows)) (input-ledger:import $db $expect $rows))
      (("census" ()) (input-ledger:census $db))
      (("lookup" ($kind $keys)) (input-ledger:lookup $db $kind $keys))
      (("state" ()) (input-ledger:state-read $db))
      (("turn-dump" ()) (input-ledger:turn-dump $db))
      (("control" ($mode $paused $router $request-id))
       (input-ledger:turn-write $db input-ledger:control
         (input-ledger:fields ($mode $paused $router)) $request-id False
         (|-> ($space) (input-ledger:control-in $space $mode $paused $router))))
      (("restore" ($request-id))
       (input-ledger:turn-write $db input-ledger:restore "" $request-id True
         (|-> ($space) (input-ledger:restore-in $space))))
      (("capture" ($focus $digest $router $guard $owner $request-id))
       (input-ledger:turn-write $db input-ledger:capture
         (input-ledger:fields ($focus $digest $router $guard $owner)) $request-id paused
         (|-> ($space) (input-ledger:capture-in $space $focus $digest $router $guard $owner))))
      (("decide" ($serial $kind $tool $command $request-id))
       (input-ledger:turn-write $db input-ledger:decide
         (input-ledger:fields ($serial $kind $tool $command)) $request-id ignored
         (|-> ($space) (input-ledger:decide-in $space $serial $kind $tool $command))))
      (("report" ($ticket $payload $disposition $domain $digest $length $request-id))
       (input-ledger:turn-write $db input-ledger:report
         (input-ledger:fields ($ticket $digest $disposition $domain $length)) $request-id False
         (|-> ($space) (input-ledger:report-in $space $ticket $payload $disposition $domain $digest $length))))
      (("presented-receipts" ($calls))
       (input-ledger:task-read $db input-ledger:presented-receipts
         (|-> ($view) (input-ledger:presented-receipts-of $view $calls))))
      (("observe" ($pairs $request-id))
       (input-ledger:turn-write $db input-ledger:observe
         (input-ledger:fields $pairs) $request-id False
         (|-> ($space) (input-ledger:observe-in $space $pairs))))
      (("attest" ($request $ticket $request-id))
       (input-ledger:turn-write $db input-ledger:attest
         (input-ledger:fields ($request $ticket)) $request-id False
         (|-> ($space) (input-ledger:attest-in $space $request $ticket))))
      (("finish" ($request $ticket $request-id))
       (input-ledger:turn-write $db input-ledger:finish
         (input-ledger:fields ($request $ticket)) $request-id False
         (|-> ($space) (input-ledger:finish-in $space $request $ticket))))
      (("attempt-begin" ($ticket $position $command $request-id))
       (input-ledger:turn-write $db input-ledger:attempt-begin
         (input-ledger:fields ($ticket $position $command)) $request-id exists
         (|-> ($space) (input-ledger:attempt-begin-in $space $ticket $position $command))))
      (("attempt-result" ($ticket $position $command $result $request-id))
       (input-ledger:turn-write $db input-ledger:attempt-result
         (input-ledger:fields ($ticket $position $command $result)) $request-id False
         (|-> ($space) (input-ledger:attempt-result-in $space $ticket $position $command $result))))
      (("actions" ($which)) (input-ledger:actions $db $which))
      (("attempts" ($which)) (input-ledger:attempts $db $which))
      (("link-inputs" ($request $inputs $request-id))
       (input-ledger:turn-write $db input-ledger:link-inputs
         (input-ledger:fields ($request $inputs)) $request-id ok
         (|-> ($space) (input-ledger:link-in $space $request $inputs))))
      (("linked" ($input)) (input-ledger:linked $db $input))
      (("task-begin" ($request $trigger $previous $chat $parent $request-id))
       (input-ledger:turn-write $db input-ledger:task-begin
         (input-ledger:fields ($request $trigger $previous $chat $parent)) $request-id ok
         (|-> ($space) (input-ledger:begin-in $space $request $trigger $previous $chat $parent))))
      (("task-focus" ($request $request-id))
       (input-ledger:turn-write $db input-ledger:task-focus (input-ledger:field $request) $request-id ok
         (|-> ($space) (input-ledger:focus-in $space $request))))
      (("task-proposal" ($serial $request $commands $disposition $request-id))
       (input-ledger:turn-write $db input-ledger:task-proposal
         (input-ledger:fields ($serial $request $commands $disposition)) $request-id ok
         (|-> ($space) (input-ledger:proposal-in $space $serial $request $commands $disposition))))
      (("task-note" ($request $text $sources $request-id))
       (input-ledger:turn-write $db input-ledger:task-note
         (input-ledger:fields ($request $text $sources)) $request-id unknown
         (|-> ($space) (input-ledger:note-in $space $request $text $sources))))
      (("task-resume" ($request))
       (input-ledger:task-read $db input-ledger:task-resume (|-> ($view) (input-ledger:resume-walk $view $request ()))))
      (("task-family" ($request))
       (input-ledger:task-read $db input-ledger:task-family (|-> ($view) (input-ledger:family $view $request))))
      (("task-view" ($which)) (input-ledger:task-view $db $which))
      (("meta-get" ($key)) (input-ledger:task-read $db input-ledger:meta-get (|-> ($view) (input-ledger:meta-of $view $key))))
      (("meta-prefix" ($prefix))
       (input-ledger:task-read $db input-ledger:meta-prefix (|-> ($view) (input-ledger:meta-prefix-of $view $prefix))))
      (("meta-cas" ($key $old $new $request-id))
       (input-ledger:turn-write $db input-ledger:meta-cas (input-ledger:fields ($key $old $new)) $request-id changed
         (|-> ($space) (input-ledger:meta-cas-in $space $key $old $new))))
      ; The store's owner, set by the first caller when it has none; every caller reads the same.
      (("owner" ($candidate $request-id))
       (input-ledger:turn-write $db input-ledger:owner (input-ledger:fields ($candidate)) $request-id none
         (|-> ($space) (input-ledger:owner-or $space $candidate))))
      (("meta-put" ($key $new $request-id))
       (input-ledger:turn-write $db input-ledger:meta-put (input-ledger:fields ($key $new)) $request-id ok
         (|-> ($space) (input-ledger:meta-put-in $space $key $new))))
      (("task-rows" ($request))
       (input-ledger:task-read $db input-ledger:task-rows (|-> ($view) (input-ledger:task-rows-of $view $request))))
      (("conversation-rows" ($focus $offset $limit))
       (input-ledger:task-read $db input-ledger:conversation-rows
         (|-> ($view) (input-ledger:conversation-rows-of $view $focus $offset $limit))))
      (("conversation-history" ($offset $count))
       (input-ledger:task-read $db input-ledger:conversation-history
         (|-> ($view) (input-ledger:conversation-history-of $view $offset $count))))
      (("initial-start" ($capacity $ratio))
       (input-ledger:task-read $db input-ledger:initial-start
         (|-> ($view) (input-ledger:initial-start-of $view $capacity $ratio))))
      (("request-context" ($request))
       (input-ledger:task-read $db input-ledger:request-context
         (|-> ($view) (context (case (input-ledger:frame-of $view $request) (((frame $p $t $chat) $chat) ($none none)))
                               (input-ledger:inputs-of $view $request)))))
      (("task-call" ($serial $ticket))
       (input-ledger:task-read $db input-ledger:task-call (|-> ($view) (input-ledger:call-request $view $serial $ticket))))
      ($other (llm-ledger:perform $db $verb $arguments)))))

(= (input-ledger:attempt $socket $verb $arguments)
   (case (db:connect $socket)
     (((Error $where $why) (Error $where $why))
      ($db (let* (($ready (input-ledger:ensure $db))
                  ($answer (case $ready
                             ((True (input-ledger:perform $db $verb $arguments))
                              ($other $ready))))
                  ($_ (db:close $db)))
             $answer)))))

(= (input-ledger:session $socket $verb $arguments $left)
   (let $outcome (input-ledger:attempt $socket $verb $arguments)
     (if (and (> $left 1) (input-ledger:lost $outcome))
         (input-ledger:session $socket $verb $arguments (- $left 1))
         $outcome)))

(= (input-ledger:run $request)
   (case $request
     (((ledger $socket $verb $arguments)
       (let $answers (collapse (input-ledger:session $socket $verb $arguments (input-ledger:sessions)))
         (case $answers
           ((($value) (answer $value))
            ($other (Error input-ledger:run (answers $other)))))))
      ($other (Error input-ledger:run (unknown-request $other))))))

; ---- the turns and their actions (samples, actions, command_attempts and the meta keys) ----

; The tables samples, actions and command_attempts and the meta keys of the SQLite
; ledger, in the same collection as the requests, so that one transaction checks an
; action and moves its request:
;
;   (ledger:state <key> <value>)   one meta key: "owner" "mode" (text), "revision"
;                                  "next_sample" "clock" (integers), "paused" (True or
;                                  False), "pending" (a serial or none), "router_revision"
;                                  (its JSON text, absent until set). A key never written
;                                  has its initial value (input-ledger:initial).
;   (ledger:turn <key> <serial> <status> <snapshot> <position>)
;                                  samples (<key> is the serial's text: an index keys only
;                                  symbols and strings): status "pending", "interrupted" or the kind of
;                                  its reply; <snapshot> is (ledger:snapshot <revision>
;                                  <mode> <owner> <focus> <boundary digest> <router JSON>
;                                  <guard>); <position> is the clock at the capture
;   (ledger:action <ticket> <request> <serial> <tool> <command> <status> <observed> <receipt>)
;                                  actions: the command as its JSON text, <observed> the
;                                  symbol observed or unobserved (0 or 1 to the readers),
;                                  <receipt> none or (ledger:receipt <disposition> <domain>
;                                  <payload digest> <payload length>)
;   (ledger:payload <ticket> <payload>)
;                                  the receipt's payload, ONE atom, written once, so that
;                                  observing or attesting rewrites only the small action
;   (ledger:attempt <ticket> <position> <command> <result> <order>)
;                                  command_attempts: <result> none until recorded, <order>
;                                  its rowid (the "attempts" counter)
;   (ledger:arrival <id> <position>)
;                                  the clock when a request was accepted
;
; The clock replaces the events table, whose live readers wanted only the order of
; the deliveries and the captures. Indexes: the state, payload and arrival by their
; key (arity 3, position 1), the turn by its key and the attempt by ticket (6, 1), the
; action by ticket (9, 1), request (9, 2), status (9, 6) and observed (9, 7). Every
; indexed field holds a string or a symbol: the index treats numbers as wildcards, so an
; integer key would make each lookup read every record of its arity.

(= (input-ledger:initial "revision") 0)
(= (input-ledger:initial "mode") "godelclaw")
(= (input-ledger:initial "paused") False)
(= (input-ledger:initial "next_sample") 0)
(= (input-ledger:initial "pending") none)
(= (input-ledger:initial "clock") 0)
(= (input-ledger:initial "owner") absent)
(= (input-ledger:initial "router_revision") absent)

(= (input-ledger:state-of $space $key)
   (case (collapse (match $space (ledger:state $key $value) $value))
     ((() (input-ledger:initial $key))
      (($value) $value)
      ($other (Error input-ledger:state (several $key (size-atom $other)))))))

(= (input-ledger:state-put $space $key $value)
   (case (collapse (match $space (ledger:state $key $old) (ledger:state $key $old)))
     ((() (let $_ (add-atom $space (ledger:state $key $value)) done))
      (($old) (input-ledger:replace $space $old (ledger:state $key $value)))
      ($other (Error input-ledger:state (several $key (size-atom $other)))))))

; meta 'revision' += 1, in the transaction of the change it counts.
(= (input-ledger:advance $space)
   (let* (($revision (input-ledger:state-of $space "revision"))
          ($next (+ $revision 1)))
     (input-ledger:state-put $space "revision" $next)))

; The next position of the store clock.
(= (input-ledger:tick $space)
   (let* (($clock (input-ledger:state-of $space "clock"))
          ($next (+ $clock 1))
          ($_ (input-ledger:state-put $space "clock" $next)))
     $next))

; The store's owner, which names its tickets: the first caller that needs one (a capture, or a
; reader through the owner verb) sets it, and it never changes.
(= (input-ledger:owner-or $space $candidate)
   (let $kept (input-ledger:state-of $space "owner")
     (if (== $kept absent)
         (let $_ (add-atom $space (ledger:state "owner" $candidate)) $candidate)
         $kept)))

(= (input-ledger:states $view)
   (collapse (match $view (ledger:state $key $value) ($key $value))))

; Ledger.control: mode and paused change when given (not none) and different, the
; router revision (its JSON text) when given and absent or different; a change
; advances the revision. True when anything changed.
; A boolean that arrived over the wire is not == to the literal True or False of a
; source, but it is a truth value: booleans are compared by their truth.
(= (input-ledger:same $left $right)
   (if (and (== (get-type $left) Bool) (== (get-type $right) Bool))
       (== (if $left 1 0) (if $right 1 0))
       (== $left $right)))

(= (input-ledger:changed $space $key $value)
   (if (== $value none)
       False
       (if (input-ledger:same (input-ledger:state-of $space $key) $value)
           False
           (let $_ (input-ledger:state-put $space $key $value) True))))

(= (input-ledger:control-in $space $mode $paused $router)
   (let* (($a (input-ledger:changed $space "mode" $mode))
          ($b (input-ledger:changed $space "paused" $paused))
          ($c (input-ledger:changed $space "router_revision" $router)))
     (if (or $a (or $b $c))
         (let $_ (input-ledger:advance $space) True)
         (empty))))

; Ledger.restore: the pending turn, if any, is interrupted, every issued action becomes
; uncertain (it may have run before the crash: it is never issued again), and the
; revision advances.
(= (input-ledger:key $serial) (repr $serial))

(= (input-ledger:turn-position $space $serial)
   (case (input-ledger:turns-of $space $serial)
     ((((ledger:turn $k $s $st $sn $p)) $p)
      ($other none))))

(= (input-ledger:turns-of $space $serial)
   (let $key (input-ledger:key $serial)
     (collapse (match $space (ledger:turn $key $serial $status $snapshot $position)
                      (ledger:turn $key $serial $status $snapshot $position)))))

(= (input-ledger:interrupt $space $serial)
   (case (input-ledger:turns-of $space $serial)
     ((((ledger:turn $k $s "pending" $snapshot $position))
       (input-ledger:replace $space (ledger:turn $k $s "pending" $snapshot $position)
                                    (ledger:turn $k $s "interrupted" $snapshot $position)))
      ($other done))))

(= (input-ledger:actions-with $space $status)
   (collapse (match $space (ledger:action $t $r $s $tool $c $status $o $rc)
                    (ledger:action $t $r $s $tool $c $status $o $rc))))

(= (input-ledger:uncertain-each $space $actions)
   (if (== $actions ())
       done
       (let* ((($head $tail) (decons-atom $actions))
              ((ledger:action $t $r $s $tool $c $st $o $rc) $head)
              ($_ (input-ledger:replace $space $head (ledger:action $t $r $s $tool $c "uncertain" $o $rc))))
         (input-ledger:uncertain-each $space $tail))))

(= (input-ledger:restore-in $space)
   (let* (($pending (input-ledger:state-of $space "pending"))
          ($_ (if (== $pending none) done (input-ledger:interrupt $space $pending)))
          ($_ (input-ledger:state-put $space "pending" none))
          ($_ (input-ledger:uncertain-each $space (input-ledger:actions-with $space "issued")))
          ($_ (input-ledger:advance $space)))
     True))

; Ledger.capture: nothing while paused; else the next serial is captured pending with
; its snapshot, the counters move and the clock stamps the capture. The answer
; (captured <serial> <revision> <mode> <owner>) completes the caller's snapshot.
(= (input-ledger:capture-in $space $focus $digest $router $guard $candidate)
   (if (input-ledger:state-of $space "paused")
       (empty)
       (let* (($serial (input-ledger:state-of $space "next_sample"))
              ($revision (input-ledger:state-of $space "revision"))
              ($mode (input-ledger:state-of $space "mode"))
              ($owner (input-ledger:owner-or $space $candidate))
              ($position (input-ledger:tick $space))
              ($key (input-ledger:key $serial))
              ($_ (add-atom $space (ledger:turn $key $serial "pending"
                                     (ledger:snapshot $revision $mode $owner $focus $digest $router $guard)
                                     $position)))
              ($next (+ $serial 1))
              ($_ (input-ledger:state-put $space "next_sample" $next))
              ($_ (input-ledger:state-put $space "pending" $serial)))
         (captured $serial $revision $mode $owner))))

; Ledger.decide: ignored unless <serial> is the pending turn; the turn takes the kind
; of its reply, and a command is issued under the ticket <owner>:<serial> for the
; captured focus. Answers (issued <ticket>) or (decided <kind>).
(= (input-ledger:decide-in $space $serial $kind $tool $command)
   (if (not (== (input-ledger:state-of $space "pending") $serial))
       (empty)
       (case (input-ledger:turns-of $space $serial)
         ((((ledger:turn $k $s $st (ledger:snapshot $rv $md $owner $focus $dg $rt $gd) $p))
           (let* (($_ (input-ledger:state-put $space "pending" none))
                  ($_ (input-ledger:replace $space
                        (ledger:turn $k $s $st (ledger:snapshot $rv $md $owner $focus $dg $rt $gd) $p)
                        (ledger:turn $k $s $kind (ledger:snapshot $rv $md $owner $focus $dg $rt $gd) $p)))
                  ($ticket (str:concat $owner (str:concat ":" (repr $serial)))))
             (if (== $kind "command")
                 (let $_ (add-atom $space (ledger:action $ticket $focus $serial $tool $command "issued" unobserved none))
                   (issued $ticket))
                 (decided $kind))))
          ($other (Error input-ledger:decide (turns $serial (size-atom $other))))))))

(= (input-ledger:actions-of $space $ticket)
   (collapse (match $space (ledger:action $ticket $r $s $tool $c $st $o $rc)
                    (ledger:action $ticket $r $s $tool $c $st $o $rc))))

; Ledger.report: once, for an issued or uncertain action without a receipt: the
; receipt and its payload are stored, the action is returned and the revision advances.
(= (input-ledger:report-in $space $ticket $payload $disposition $domain $digest $length)
   (case (input-ledger:actions-of $space $ticket)
     ((((ledger:action $t $r $s $tool $c $st $o none))
       (if (or (== $st "issued") (== $st "uncertain"))
           (let* (($_ (input-ledger:replace $space (ledger:action $t $r $s $tool $c $st $o none)
                        (ledger:action $t $r $s $tool $c "returned" $o (ledger:receipt $disposition $domain $digest $length))))
                  ($_ (add-atom $space (ledger:payload $t $payload)))
                  ($_ (input-ledger:advance $space)))
             True)
           (empty)))
      ($other (empty)))))

; Ledger.observe: an action whose receipt's payload digest is the presented one is
; observed. <pairs> is ((<ticket> <presented digest>) ...).
; presented-receipts: <calls> are the (ticket position) calls an answered prompt carried with
; their results. Of their actions, those not yet observed whose receipt exists and every
; attempt of which has its result among the carried calls, as (ticket digest) pairs for
; observe (lean/pettaclaw/ObservedReceipts.lean: presents, send).
(= (input-ledger:presented-receipts-of $view $calls)
   (input-ledger:presented-groups $view (sort-atom $calls) none () ()))

; The calls sorted, walked once: each ticket's carried positions are gathered, then checked.
(= (input-ledger:presented-groups $view $calls $ticket $positions $out)
   (if (== $calls ())
       (input-ledger:presented-flush $view $ticket $positions $out)
       (let* ((($head $rest) (decons-atom $calls)) (($t $p) $head))
         (if (== $t $ticket)
             (input-ledger:presented-groups $view $rest $ticket (cons-atom $p $positions) $out)
             (input-ledger:presented-groups $view $rest $t ($p)
               (input-ledger:presented-flush $view $ticket $positions $out))))))

(= (input-ledger:presented-flush $view $ticket $positions $out)
   (if (== $ticket none) $out
     (let $found (input-ledger:presented-receipt $view $ticket $positions)
       (if (== $found none) $out (cons-atom $found $out)))))

(= (input-ledger:presented-receipt $view $ticket $carried)
   (case (input-ledger:actions-of $view $ticket)
     ((((ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $dg $n)))
       (let $attempts (collapse (match $view (ledger:attempt $ticket $p $cmd $result $o) ($p $result)))
         (if (and (not (== $attempts ())) (input-ledger:all-carried $attempts $carried)) ($ticket $dg) none)))
      ($other none))))

(= (input-ledger:all-carried $attempts $carried)
   (if (== $attempts ()) True
     (let* ((($head $rest) (decons-atom $attempts)) (($p $result) $head))
       (if (and (not (== $result none)) (input-ledger:member $p $carried))
           (input-ledger:all-carried $rest $carried) False))))

(= (input-ledger:observe-one $space $ticket $digest)
   (case (input-ledger:actions-of $space $ticket)
     ((((ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $digest $n)))
       (let $_ (input-ledger:replace $space (ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $digest $n))
                                           (ledger:action $t $r $s $tool $c $st observed (ledger:receipt $d $dm $digest $n)))
         1))
      ($other 0))))

(= (input-ledger:observe-each $space $pairs $count)
   (if (== $pairs ())
       $count
       (let* ((($head $tail) (decons-atom $pairs))
              (($ticket $digest) $head)
              ($one (input-ledger:observe-one $space $ticket $digest))
              ($next (+ $count $one)))
         (input-ledger:observe-each $space $tail $next))))

(= (input-ledger:observe-in $space $pairs)
   (let $count (input-ledger:observe-each $space $pairs 0)
     (if (== $count 0) (empty) True)))

; The action <ticket> of <request>, observed, with a returned receipt: its fields, else none.
(= (input-ledger:observed-return $space $request $ticket)
   (case (input-ledger:actions-of $space $ticket)
     ((((ledger:action $t $request $s $tool $c $st observed (ledger:receipt "returned" $dm $dg $n)))
       (ledger:action $t $request $s $tool $c $st observed (ledger:receipt "returned" $dm $dg $n)))
      ($other none))))

(= (input-ledger:active-request $space $request)
   (case (input-ledger:heads $space $request)
     ((((request $id $q $k $c "active" $p $n $sm $dg)) True)
      ($other False))))

; Ledger.attest's write, after the caller's checker accepted the receipt: the request
; is active and the action is its own, observed and returned; the receipt's domain
; becomes success. The receipt is written once, so the checked receipt is this one.
(= (input-ledger:attest-in $space $request $ticket)
   (if (not (input-ledger:active-request $space $request))
       (empty)
       (case (input-ledger:observed-return $space $request $ticket)
         ((none (empty))
          ((ledger:action $t $r $s $tool $c $st $o (ledger:receipt $d $dm $dg $n))
           (if (== $dm "success")
               (empty)
               (let $_ (input-ledger:replace $space (ledger:action $t $r $s $tool $c $st $o (ledger:receipt $d $dm $dg $n))
                                                   (ledger:action $t $r $s $tool $c $st $o (ledger:receipt $d "success" $dg $n)))
                 True)))))))

; Ledger.finish's write, after the caller's checker accepted: the request is active and
; the action is its own, observed, returned and attested success; the request is
; achieved. Only this check achieves a request (TurnLedger achieved_only_by_check).
(= (input-ledger:finish-in $space $request $ticket)
   (if (not (input-ledger:active-request $space $request))
       (empty)
       (case (input-ledger:observed-return $space $request $ticket)
         (((ledger:action $t $r $s $tool $c $st $o (ledger:receipt $d "success" $dg $n))
           (input-ledger:move-in $space $request ("active") "achieved"))
          ($other (empty))))))

; Readers of the actions: (action <ticket> <request> <serial> <tool> <command> <status>
; <observed> <receipt> <payload>), the receipt none or (receipt <disposition> <domain>),
; the payload "" without a receipt, oldest first.
(= (input-ledger:payload-of $view $ticket)
   (case (collapse (match $view (ledger:payload $ticket $text) $text))
     ((($text) $text)
      (() "")
      ($other (Error input-ledger:payload (several $ticket (size-atom $other)))))))

(= (input-ledger:observed-flag observed) 1)
(= (input-ledger:observed-flag unobserved) 0)

(= (input-ledger:action-row $view (ledger:action $t $r $s $tool $c $st $observed $rc))
   (let $o (input-ledger:observed-flag $observed)
     (case $rc
       ((none (action $t $r $s $tool $c $st $o none ""))
        ((ledger:receipt $d $dm $dg $n)
         (let $payload (input-ledger:payload-of $view $t)
           (action $t $r $s $tool $c $st $o (receipt $d $dm) $payload)))))))

(= (input-ledger:action-rows $view $actions)
   (map-atom (input-ledger:sorted-by input-ledger:serial-key $actions) $action (input-ledger:action-row $view $action)))

(= (input-ledger:serial-key (ledger:action $t $r $s $x $c $st $o $rc)) $s)

(= (input-ledger:selected-actions $view $which)
   (case $which
     (("all" (collapse (match $view (ledger:action $t $r $s $tool $c $st $o $rc) (ledger:action $t $r $s $tool $c $st $o $rc))))
      ((ticket $ticket) (input-ledger:actions-of $view $ticket))
      ((status $status) (input-ledger:actions-with $view $status))
      ((request $request) (collapse (match $view (ledger:action $t $request $s $tool $c $st $o $rc)
                                           (ledger:action $t $request $s $tool $c $st $o $rc))))
      ((protected $focus) (input-ledger:protected-actions $view $focus))
      ("unobserved" (collapse (match $view (ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $dg $n))
                                     (ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $dg $n)))))
      ($other (Error input-ledger:actions (unknown-selection $which))))))

; The protected view's receipts, as the SQLite query picks them: of the actions with a
; receipt not yet observed and the focus's newest with a receipt, the first two by serial.
; Only their keys are read until the two are known.
(= (input-ledger:protected-actions $view $focus)
   (let* (($unobserved (collapse (match $view (ledger:action $t $r $s $tool $c $st unobserved (ledger:receipt $d $dm $dg $n))
                                   ($s $t))))
          ($own (sort-atom (collapse (match $view (ledger:action $t $focus $s $tool $c $st $o (ledger:receipt $d $dm $dg $n))
                                       ($s $t)))))
          ($newest (if (== $own ()) () ((index-atom $own (- (size-atom $own) 1)))))
          ($keys (list:take 2 (sort-atom (unique-atom (union-atom $unobserved $newest))))))
     (collapse (let ($s $t) (superpose $keys) (input-ledger:action-of-ticket $view $t)))))

(= (input-ledger:action-of-ticket $view $ticket)
   (match $view (ledger:action $ticket $r $s $tool $c $st $o $rc) (ledger:action $ticket $r $s $tool $c $st $o $rc)))

(= (input-ledger:actions $db $which)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer (input-ledger:action-rows $view (input-ledger:selected-actions $view $which)))
                    ($_ (db:close $view)))
               $answer)))))

; strategy_host's command attempts. A begin of a (ticket, position) already recorded is
; refused (exists), as the SQLite primary key refuses it; a result is recorded once,
; for the same command.
(= (input-ledger:attempts-at $space $ticket $position)
   (collapse (match $space (ledger:attempt $ticket $position $c $r $o) (ledger:attempt $ticket $position $c $r $o))))

(= (input-ledger:attempt-begin-in $space $ticket $position $command)
   (if (== (input-ledger:attempts-at $space $ticket $position) ())
       (let* (($order (input-ledger:count $space "attempts"))
              ($_ (add-atom $space (ledger:attempt $ticket $position $command none $order))))
         created)
       (empty)))

(= (input-ledger:attempt-result-in $space $ticket $position $command $result)
   (case (input-ledger:attempts-at $space $ticket $position)
     ((((ledger:attempt $t $p $command none $o))
       (let $_ (input-ledger:replace $space (ledger:attempt $t $p $command none $o)
                                           (ledger:attempt $t $p $command $result $o))
         True))
      ($other (empty)))))

(= (input-ledger:position-first $left $right)
   (let* (((attempt $t1 $p1 $c1 $r1 $o1) $left) ((attempt $t2 $p2 $c2 $r2 $o2) $right)) (<= $p1 $p2)))

(= (input-ledger:order-key (attempt $t $p $c $r $o)) $o)

;   (attempt <ticket> <position> <command> <result or none> <order>) ...
(= (input-ledger:attempts $db $which)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer
                      (case $which
                        (((ticket $ticket)
                          (list:sort-by input-ledger:position-first
                            (collapse (match $view (ledger:attempt $ticket $p $c $r $o) (attempt $ticket $p $c $r $o)))))
                         ((recent $bound $limit)
                          (list:take $limit (list:reverse (input-ledger:sorted-by input-ledger:order-key
                            (collapse (match $view (ledger:attempt $t $p $c $r $o)
                                        (if (<= $o $bound) (attempt $t $p $c $r $o) (empty))))))))
                         ($other (Error input-ledger:attempts (unknown-selection $which))))))
                    ($_ (db:close $view)))
               $answer)))))

;   (turns (<serial> <status> <snapshot fields ...> <position>) ...) and the meta keys
(= (input-ledger:turn-dump $db)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($turns (collapse (match $view (ledger:turn $k $s $st (ledger:snapshot $rv $md $ow $f $dg $rt $gd) $p)
                                         (turn $s $st $rv $md $ow $f $dg $rt $gd $p))))
                    ($states (input-ledger:states $view))
                    ($arrivals (collapse (match $view (ledger:arrival $id $p) ($id $p))))
                    ($_ (db:close $view)))
               (dump $states $turns $arrivals))))))

(= (input-ledger:state-read $db)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($states (input-ledger:states $view)) ($_ (db:close $view))) $states)))))

; One write of a turn or an action: a request id of the caller and an answer that,
; when the body changed nothing (an abort), is the verb's known answer.
(= (input-ledger:turn-write $db $verb $fields $request-id $unchanged $body)
   (let $outcome (input-ledger:request-write $db $verb $fields $request-id $body)
     (case (input-ledger:unchanged $outcome $unchanged)
       (((Error $where $why) (Error $where $why))
        ((db:uncertain $r $c $o) (Error $verb (db:uncertain $r $c $o)))
        ($answer $answer)))))

; ---- the task context (task_frames, task_proposals, task_notes, input_supersessions, request_inputs) ----

; The task_context tables of the SQLite ledger, in the same collection. A link that is
; read from both ends is two atoms, each indexed by its first field:
;
;   (ledger:frame <request> <parent> <trigger> <chat>)      task_frames (parent none at a root)
;   (ledger:child <parent> <request>)                       the same edge, from the parent
;   (ledger:input-of <request> <input>)                     request_inputs
;   (ledger:requests-of <input> <request> <position>)       the same link from the input; the clock
;                                                           at linking gives the rowid order
;   (ledger:covered <request> <covering>)                   input_supersessions
;   (ledger:covers <covering> <request>)                    the same, from the covering batch
;   (ledger:proposal <key> <serial> <request> <commands> <disposition> <position> <count>)
;                                                           task_proposals: <key> the serial's text,
;                                                           <position> its turn's capture (none
;                                                           without one), <count> its commands
;   (ledger:proposal-of <request> <serial>)                 the same, from the request
;   (ledger:note <request> <sequence> <text> <sources>)     task_notes, <sequence> numbered by "notes"
;   (ledger:state "context_focus" <request or none>)        meta 'context_focus'

(= (input-ledger:initial "context_focus") none)
(= (input-ledger:initial "notes") 0)
(= (input-ledger:initial "attempts") 0)

; The next value of a counter that numbers one table's rows as SQLite numbers them
; (AUTOINCREMENT, rowid): a note's sequence and an attempt's rowid are seen by the agent.
(= (input-ledger:count $space $key)
   (let* (($count (input-ledger:state-of $space $key))
          ($next (+ $count 1))
          ($_ (input-ledger:state-put $space $key $next)))
     $next))

(= (input-ledger:member-all $items $set)
   (if (== $items ())
       True
       (let* ((($head $tail) (decons-atom $items)))
         (if (input-ledger:member $head $set) (input-ledger:member-all $tail $set) False))))

(= (input-ledger:inputs-of $space $request)
   (collapse (match $space (ledger:input-of $request $input) $input)))

; Ledger.link_inputs: an input: envelope keeps the inputs it was formed from (another set is
; a conflict); any request gains the inputs it does not have yet.
(= (input-ledger:link-each $space $request $inputs $prior $added)
   (if (== $inputs ())
       $added
       (let* ((($input $rest) (decons-atom $inputs)))
         (if (input-ledger:member $input $prior)
             (input-ledger:link-each $space $request $rest $prior $added)
             (let* (($position (input-ledger:tick $space))
                    ($_ (add-atom $space (ledger:input-of $request $input)))
                    ($_ (add-atom $space (ledger:requests-of $input $request $position)))
                    ($next (+ $added 1)))
               (input-ledger:link-each $space $request $rest (cons-atom $input $prior) $next))))))

(= (input-ledger:link-in $space $request $inputs)
   (let $prior (input-ledger:inputs-of $space $request)
     (if (and (str:starts-with $request "input:")
              (and (not (== $prior ()))
                   (not (and (== (size-atom $prior) (size-atom $inputs)) (input-ledger:member-all $inputs $prior)))))
         conflict
         (if (== (input-ledger:link-each $space $request $inputs $prior 0) 0) (empty) ok))))

(= (input-ledger:linked-row $view ($request $position))
   ($request (list:sort (input-ledger:inputs-of $view $request))))

(= (input-ledger:position-first-link $left $right)
   (let* ((($r1 $p1) $left) (($r2 $p2) $right)) (<= $p1 $p2)))

;   ((<request> (<input> ...)) ...) for Ledger.requests_with_input, in linking order, inputs sorted
(= (input-ledger:linked $db $input)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($links (list:sort-by input-ledger:position-first-link
                              (collapse (match $view (ledger:requests-of $input $r $p) ($r $p)))))
                    ($answer (map-atom $links $link (input-ledger:linked-row $view $link)))
                    ($_ (db:close $view)))
               $answer)))))

; A request record's fields: (<seq> <kind> <checker> <status>) or none.
(= (input-ledger:request-fields $space $id)
   (case (input-ledger:heads $space $id)
     ((((request $i $q $k $c $st $p $n $sm $dg)) ($q $k $c $st))
      ($other none))))

(= (input-ledger:frame-of $space $request)
   (case (collapse (match $space (ledger:frame $request $parent $trigger $chat) (frame $parent $trigger $chat)))
     ((($frame) $frame)
      ($other none))))

; cover_inputs: a raw input envelope of an operator message or notification with a chat and
; inputs supersedes the older envelopes of the same kind and chat whose inputs are a subset
; of its own; each becomes covered and the revision advances once.
(= (input-ledger:cover-candidate $space $old $seq $kind $chat $mine)
   (case (input-ledger:request-fields $space $old)
     ((($q $k $c $st)
       (and (and (< $q $seq) (str:starts-with $old "input:"))
            (and (and (== $c "operator") (or (== $st "active") (== $st "processed")))
                 (and (== $k $kind)
                      (and (case (input-ledger:frame-of $space $old)
                             (((frame $p $t $ch) (== $ch $chat)) ($none False)))
                           (input-ledger:member-all (input-ledger:inputs-of $space $old) $mine))))))
      ($other False))))

(= (input-ledger:cover-each $space $request $olds)
   (if (== $olds ())
       ()
       (let* ((($old $rest) (decons-atom $olds))
              ($_ (add-atom $space (ledger:covered $old $request)))
              ($_ (add-atom $space (ledger:covers $request $old)))
              ($_ (input-ledger:move-in $space $old ("active" "processed") "covered")))
         (cons-atom $old (input-ledger:cover-each $space $request $rest)))))

(= (input-ledger:cover-in $space $request)
   (case (input-ledger:request-fields $space $request)
     ((($seq $kind $checker $status)
       (let* (($mine (input-ledger:inputs-of $space $request))
              ($chat (case (input-ledger:frame-of $space $request) (((frame $p $t $ch) $ch) ($none "")))))
         (if (and (and (str:starts-with $request "input:") (or (== $status "active") (== $status "processed")))
                  (and (and (== $checker "operator") (or (== $kind "message") (== $kind "notification")))
                       (and (not (== $chat "")) (not (== $mine ())))))
             (let* (($sharing (unique-atom (collapse (let $input (superpose $mine)
                                  (match $space (ledger:requests-of $input $sharer $at) $sharer)))))
                    ($olds (collapse (let $candidate (superpose $sharing)
                              (if (and (not (== $candidate $request))
                                       (input-ledger:cover-candidate $space $candidate $seq $kind $chat $mine))
                                  $candidate (empty)))))
                    ($covered (input-ledger:cover-each $space $request $olds))
                    ($_ (if (== $covered ()) done (input-ledger:advance $space))))
               $covered)
             ())))
      ($other ()))))

(= (input-ledger:selectable $space $request)
   (case (input-ledger:request-fields $space $request)
     ((($q $k $c $st) (or (== $st "active") (and (== $st "processed") (or (== $k "message") (== $k "notification")))))
      ($other False))))

(= (input-ledger:no-parent $parent) (or (== $parent none) (== $parent "")))

; task_context.begin: a frame (kept if the request has one), coverage, and the focus. A
; request that follows one awaiting the user's reply is its child; a named parent must exist.
(= (input-ledger:begin-in $space $request $trigger $previous $chat $parent)
   (let* (($inferred
           (if (and (== $parent none)
                    (and (not (== $previous $request))
                         (and (not (== $previous "autonomous"))
                              (not (and (== (get-type $previous) String) (str:starts-with $previous "goal:"))))))
               (case (input-ledger:request-fields $space $previous)
                 ((($q $k $c "waiting-user") $previous) ($other none)))
               $parent))
          ($own (if (== $inferred $request) none $inferred)))
     (if (and (not (input-ledger:no-parent $own)) (== (input-ledger:request-fields $space $own) none))
         (refused "unknown parent task")
         (let* (($_ (if (== (input-ledger:frame-of $space $request) none)
                        (let* (($_ (add-atom $space (ledger:frame $request $own $trigger $chat)))
                               ($_ (if (input-ledger:no-parent $own) done (add-atom $space (ledger:child $own $request)))))
                          done)
                        done))
                ($_ (input-ledger:cover-in $space $request))
                ($_ (input-ledger:state-put $space "context_focus" $request)))
           ok))))

(= (input-ledger:focus-in $space $request)
   (if (input-ledger:same (input-ledger:state-of $space "context_focus") $request)
       (empty)
       (let $_ (input-ledger:state-put $space "context_focus" $request) ok)))

; task_context.proposal: a serial names one proposal; its disposition may change.
(= (input-ledger:proposal-in $space $serial $request $commands $disposition)
   (let $key (input-ledger:key $serial)
   (case (collapse (match $space (ledger:proposal $key $serial $r $c $d $p $n) (ledger:proposal $key $serial $r $c $d $p $n)))
     ((() (let* (($position (input-ledger:turn-position $space $serial))
                 ($count (size-atom (input-ledger:json-items $commands)))
                 ($_ (add-atom $space (ledger:proposal $key $serial $request $commands $disposition $position $count)))
                 ($_ (add-atom $space (ledger:proposal-of $request $serial))))
            ok))
      (((ledger:proposal $k $s $r $c $d $p $n))
       (if (and (== $r $request) (== $c $commands))
           (if (== $d $disposition)
               (empty)
               (let $_ (input-ledger:replace $space (ledger:proposal $k $s $r $c $d $p $n) (ledger:proposal $k $s $r $c $disposition $p $n)) ok))
           conflict))
      ($other (Error input-ledger:proposal (several $serial (size-atom $other))))))))

; task_context.note: a note on a known request, in clock order.
(= (input-ledger:note-in $space $request $text $sources)
   (if (== (input-ledger:request-fields $space $request) none)
       (empty)
       (let* (($sequence (input-ledger:count $space "notes"))
              ($_ (add-atom $space (ledger:note $request $sequence $text $sources))))
         ok)))

; task_context.resume: from a request, through coverings and parents, to the first one
; still selectable; none when the walk ends or returns to a request it saw.
(= (input-ledger:resume-walk $space $request $seen)
   (if (or (input-ledger:no-parent $request) (input-ledger:member $request $seen))
       none
       (case (collapse (match $space (ledger:covered $request $covering) $covering))
         ((($covering) (input-ledger:resume-walk $space $covering (cons-atom $request $seen)))
          ($none (if (input-ledger:selectable $space $request)
                     $request
                     (case (input-ledger:frame-of $space $request)
                       (((frame $parent $t $c) (input-ledger:resume-walk $space $parent (cons-atom $request $seen)))
                        ($other none)))))))))

; _root: the ancestor without a parent; cyclic on a cycle (a refusal of the caller, not a
; failure of the store).
(= (input-ledger:root-walk $space $request $seen)
   (if (input-ledger:member $request $seen)
       cyclic
       (case (input-ledger:frame-of $space $request)
         (((frame $parent $t $c)
           (if (input-ledger:no-parent $parent) $request (input-ledger:root-walk $space $parent (cons-atom $request $seen))))
          ($other $request)))))

; _members: the root and everything reached through child frames and covered batches.
(= (input-ledger:family-step $space $frontier $seen)
   (if (== $frontier ())
       $seen
       (let* ((($node $rest) (decons-atom $frontier))
              ($next (unique-atom (union-atom (collapse (match $space (ledger:child $node $child) $child))
                                              (collapse (match $space (ledger:covers $node $batch) $batch)))))
              ($new (collapse (let $reached (superpose $next) (if (input-ledger:member $reached $seen) (empty) $reached)))))
         (input-ledger:family-step $space (union-atom $rest $new) (union-atom $seen $new)))))

(= (input-ledger:family $view $request)
   (let $root (input-ledger:root-walk $view $request ())
     (if (== $root cyclic) cyclic (family $root (input-ledger:family-step $view ($root) ($root))))))

; Everything a task projection or the conversation reads, from one snapshot, for a list
; of requests or for "all":
;   (view <owner> <requests> <coverings> <notes> <proposals> <actions> <attempts> <arrivals> <captures>)
; <requests> as request-rows answers them; <coverings> ((<request> <covering>) ...);
; <notes> ((<request> <sequence> <text> <sources>) ...); <proposals> ((<serial> <request>
; <commands> <disposition>) ...); <actions> ((<serial> <ticket> <request> <command>) ...);
; <attempts> ((<ticket> <position> <result or none>) ...); <arrivals> ((<request> <position>) ...);
; <captures> ((<serial> <position>) ...) of the turns of these actions and proposals.
(= (input-ledger:of-requests $view $ids $pattern-of)
   (collapse (let $id (superpose $ids) ($pattern-of $view $id))))

(= (input-ledger:notes-of $view $id) (match $view (ledger:note $id $s $t $src) ($id $s $t $src)))
(= (input-ledger:covering-of $view $id) (match $view (ledger:covered $id $c) ($id $c)))
(= (input-ledger:proposals-of $view $id)
   (let* (($serial (match $view (ledger:proposal-of $id $serial) $serial))
          ($key (input-ledger:key $serial)))
     (match $view (ledger:proposal $key $serial $id $c $d $p $n) ($serial $id $c $d))))
(= (input-ledger:request-actions $view $id)
   (match $view (ledger:action $t $id $s $tool $c $st $o $rc) ($s $t $id $c)))
(= (input-ledger:attempts-of-actions $view $actions)
   (collapse (let ($s $t $r $c) (superpose $actions)
     (match $view (ledger:attempt $t $p $cmd $result $o) ($t $p $result)))))
(= (input-ledger:proposal-keys-of $view $id)
   (let* (($serial (match $view (ledger:proposal-of $id $serial) $serial))
          ($key (input-ledger:key $serial)))
     (match $view (ledger:proposal $key $serial $id $c $d $p $n) ($serial $p $n))))
(= (input-ledger:request-action-keys $view $id)
   (match $view (ledger:action $t $id $s $tool $c $st $o $rc) ($s $t $c)))
(= (input-ledger:arrival-of $view $id) (match $view (ledger:arrival $id $p) ($id $p)))

; A turn copied from a journal without its capture event has position none: its calls
; then come after everything, as they do in the SQLite projection.
(= (input-ledger:capture-of $view $serial)
   (let $key (input-ledger:key $serial)
     (match $view (ledger:turn $key $serial $st $sn $p) (if (== $p none) (empty) ($serial $p)))))

(= (input-ledger:captures-of $view $serials)
   (let $unique (unique-atom $serials)
     (collapse (let $serial (superpose $unique) (input-ledger:capture-of $view $serial)))))

(= (input-ledger:first-of ($first $second $third $fourth)) $first)

(= (input-ledger:serials-of $proposals $actions)
   (union-atom (map-atom $proposals $proposal (input-ledger:first-of $proposal))
               (map-atom $actions $action (input-ledger:first-of $action))))

(= (input-ledger:rows-of $view $which)
   (let $heads (if (== $which all) (input-ledger:all-heads $view) (input-ledger:heads-of-ids $view $which))
     (map-atom (input-ledger:sorted-by input-ledger:seq-key $heads) $head (input-ledger:row-of $view $head))))

(= (input-ledger:view-of $view $which)
   (let* (($owner (input-ledger:state-of $view "owner"))
          ($all (== $which all))
          ($requests (input-ledger:rows-of $view $which))
          ($coverings (if $all (collapse (match $view (ledger:covered $r $c) ($r $c)))
                              (input-ledger:of-requests $view $which input-ledger:covering-of)))
          ($notes (if $all (collapse (match $view (ledger:note $r $s $t $src) ($r $s $t $src)))
                          (input-ledger:of-requests $view $which input-ledger:notes-of)))
          ($proposals (if $all (collapse (match $view (ledger:proposal $k $s $r $c $d $p $n) ($s $r $c $d)))
                              (input-ledger:of-requests $view $which input-ledger:proposals-of)))
          ($actions (if $all (collapse (match $view (ledger:action $t $r $s $tool $c $st $o $rc) ($s $t $r $c)))
                            (input-ledger:of-requests $view $which input-ledger:request-actions)))
          ($attempts (input-ledger:attempts-of-actions $view $actions))
          ($arrivals (if $all (collapse (match $view (ledger:arrival $r $p) ($r $p)))
                             (input-ledger:of-requests $view $which input-ledger:arrival-of)))
          ($captures (input-ledger:captures-of $view (input-ledger:serials-of $proposals $actions))))
     (view $owner $requests $coverings $notes $proposals $actions $attempts $arrivals $captures)))

(= (input-ledger:task-view $db $which)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer (input-ledger:view-of $view $which)) ($_ (db:close $view))) $answer)))))

(= (input-ledger:task-read $db $verb $body)
   (case (db:snapshot $db inputs)
     (((Error $where $why) (Error $where $why))
      ($view (let* (($answer ($body $view)) ($_ (db:close $view))) $answer)))))

; read(): the request of a call's serial (its proposal's, else its action's), or none.
(= (input-ledger:call-request $view $serial $ticket)
   (case (let $key (input-ledger:key $serial) (collapse (match $view (ledger:proposal $key $serial $r $c $d $p $n) $r)))
     ((($request) $request)
      ($none (case (collapse (match $view (ledger:action $ticket $r $serial $tool $c $st $o $rc) $r))
               ((($request) $request) ($other none)))))))

; ---- the cognition side's reads (task_context_cetta, task_context_views, context_compaction) ----

; The meta keys other modules keep, as the JSON text the SQLite meta table holds:
;   (ledger:meta <key> <JSON text>)      task_window:<root>, context_failure:<...> and the like
; A key of the ledger itself (owner, context_focus ...) is read from its ledger:state.
(= (input-ledger:json-of $value)
   (if (== $value none) "null"
     (case (get-type $value)
       ((String (json-stringify (JsonString $value)))
        (Number (repr $value))
        (Bool (if $value "true" "false"))
        ($other (repr $value))))))

(= (input-ledger:typed-key $key)
   (input-ledger:member $key ("owner" "revision" "mode" "paused" "next_sample" "pending" "context_focus")))

(= (input-ledger:meta-of $view $key)
   (if (input-ledger:typed-key $key)
       (let $value (input-ledger:state-of $view $key) (if (== $value absent) none (input-ledger:json-of $value)))
       (if (== $key "router_revision")
           (let $value (input-ledger:state-of $view $key) (if (== $value absent) none $value))
           (case (collapse (match $view (ledger:meta $key $text) $text))
             ((($text) $text) ($other none))))))

; context:write-checkpoint: the key is set when it holds <old> (none: when it is absent).
(= (input-ledger:meta-cas-in $space $key $old $new)
   (case (collapse (match $space (ledger:meta $key $text) $text))
     ((() (if (== $old none) (let $_ (add-atom $space (ledger:meta $key $new)) published) (empty)))
      (($text) (if (== $text $old) (input-ledger:replace-meta $space $key $text $new) (empty)))
      ($other (Error input-ledger:meta (several $key))))))

(= (input-ledger:replace-meta $space $key $text $new)
   (if (== $text $new) published
     (let $_ (input-ledger:replace $space (ledger:meta $key $text) (ledger:meta $key $new)) published)))

(= (input-ledger:meta-put-in $space $key $new)
   (case (collapse (match $space (ledger:meta $key $text) $text))
     ((() (let $_ (add-atom $space (ledger:meta $key $new)) ok))
      (($text) (if (== $text $new) (empty) (let $_ (input-ledger:replace $space (ledger:meta $key $text) (ledger:meta $key $new)) ok)))
      ($other (Error input-ledger:meta (several $key))))))

(= (input-ledger:first-of-two ($k $t)) $k)

; The meta keys under a prefix, by key: ((<key> <JSON text>) ...).
(= (input-ledger:meta-prefix-of $view $prefix)
   (input-ledger:sorted-by input-ledger:first-of-two
     (collapse (match $view (ledger:meta $key $text) (if (str:starts-with $key $prefix) ($key $text) (empty))))))

; task_request_view: a covered batch's content names its covering batch.
(= (input-ledger:view-content $view $id $content)
   (case (collapse (match $view (ledger:covered $id $covering) $covering))
     ((($covering) (str:join "" ("[input batch covered by " $covering "; original source via request-read " $id "]")))
      ($other $content))))

;   (<id> <content> <status> <kind> <note> <seq>) of the requests among $which (all), oldest first
(= (input-ledger:request-view-row $view (request $id $q $k $c $st $p $n $sm $dg))
   ($id (input-ledger:view-content $view $id (input-ledger:text-one $view $id)) $st $k $n $q))

(= (input-ledger:request-view-rows $view $which)
   (let $heads (if (== $which all) (input-ledger:all-heads $view) (input-ledger:heads-of-ids $view $which))
     (map-atom (input-ledger:sorted-by input-ledger:seq-key $heads) $head (input-ledger:request-view-row $view $head))))

(= (input-ledger:note-key ($r $s $t $x)) $s)

;   ((<sequence> <request> <text> <sources>) ...) by sequence
(= (input-ledger:note-rows $notes)
   (map-atom (input-ledger:sorted-by input-ledger:note-key $notes) $note (input-ledger:note-row $note)))
(= (input-ledger:note-row ($request $sequence $text $sources)) ($sequence $request $text $sources))

; The call rows of task_context_cetta's pairs query: (<serial> <request> <position> <command>
; <record> <ticket> <disposition>), <command> an element's JSON text, <record> its attempt's
; result (none before it is recorded), <ticket> the attempt's ticket (none without an
; attempt); a legacy action's calls only where an attempt exists; by serial and position.
(= (input-ledger:json-items $text)
   (case (json-parse $text) (((JsonArray $items) $items) ($other ()))))

(= (input-ledger:legacy-commands $command)
   (case (json-parse $command)
     (((JsonObject $pairs) (case (input-ledger:json-member $pairs "commands") (((JsonArray $items) $items) ($other ()))))
      ($value ($value)))))

(= (input-ledger:json-member $pairs $key)
   (if (== $pairs ()) none
     (let* ((($head $tail) (decons-atom $pairs)) ((JsonPair $name $value) $head))
       (if (== $name $key) $value (input-ledger:json-member $tail $key)))))

; The calls of a list of requests, by serial and position: the conversation's keys
; (input-ledger:entry-keys) of their proposals and actions, ordered by serial.
(= (input-ledger:pair-rows $view $which)
   (let* (($proposals (input-ledger:of-requests $view $which input-ledger:proposal-keys-of))
          ($serials (sort-atom (map-atom $proposals $proposal (input-ledger:first-of-three $proposal))))
          ($actions (sort-atom (input-ledger:of-requests $view $which input-ledger:request-action-keys)))
          ($calls (input-ledger:proposal-keys $proposals by-serial ()))
          ($all (input-ledger:legacy-keys $view (input-ledger:unproposed $actions $serials ()) by-serial $calls)))
     (input-ledger:key-rows $view (sort-atom $all) -1 (input-ledger:state-of $view "owner") none ())))

;   (task-rows <root> <requests> <notes> <pairs>) of the family of a request
(= (input-ledger:task-rows-of $view $request)
   (case (input-ledger:family $view $request)
     ((cyclic cyclic)
      ((family $root $members)
       (task-rows $root (map-atom (input-ledger:request-view-rows $view $members) $row (input-ledger:without-seq $row))
                  (input-ledger:note-rows (input-ledger:of-requests $view $members input-ledger:notes-of))
                  (input-ledger:pair-rows $view $members))))))

(= (input-ledger:without-seq ($id $content $status $kind $note $seq)) ($id $content $status $kind $note))

; The conversation (task_context_cetta's ordered query): each request at its acceptance
; (category 0, ordinal its seq), each call at its turn's capture (category 1, ordinal its
; position); a call whose turn has no capture comes after everything, by serial. Entries
; are (<ordering> <category> <ordinal> <row>), <row> as pair-rows answers; an input's row is
; (-1 <request> -1 "null" <record> "input" "input").
(= (input-ledger:input-record $content $limit)
   (json-stringify (JsonObject ((JsonPair "disposition" (JsonString "input"))
     (JsonPair "result" (if (== $limit -2) (JsonNumber (repr (str:byte-length $content)))
                          (if (< $limit 0) JsonNull (JsonString $content))))))))

; Each entry is first its key, (<ordering> <category> <ordinal> <reference>), made from the small
; records alone: a request's arrival and seq, a proposal's capture position and number of
; commands, a legacy action's attempts. The keys are sorted natively; only the entries a reader
; returns are then made into rows, a proposal's commands parsed and its attempts read once for
; all its calls.
(= (input-ledger:arrival-of-id $view $id)
   (case (collapse (match $view (ledger:arrival $id $p) $p)) ((($p) $p) ($other 0))))

(= (input-ledger:input-key $view (request $id $seq $k $c $st $p $n $sm $dg))
   ((input-ledger:arrival-of-id $view $id) 0 $seq (input $id)))

(= (input-ledger:ordering $position $serial $last)
   (if (== $position none) (+ $last (+ $serial 1)) $position))

(= (input-ledger:ordering-in $mode $serial $position)
   (case $mode ((by-serial $serial) ((after $last) (input-ledger:ordering $position $serial $last)))))

(= (input-ledger:legacy-ordering $view $mode $serial)
   (case $mode
     ((by-serial $serial)
      ((after $last) (input-ledger:ordering (input-ledger:turn-position $view $serial) $serial $last)))))

(= (input-ledger:call-keys $ordering $serial $index $count $tag $out)
   (if (>= $index $count) $out
     (let* (($next (+ $index 1))
            ($key ($ordering 1 $index ($tag $serial $index))))
       (input-ledger:call-keys $ordering $serial $next $count $tag (cons-atom $key $out)))))

(= (input-ledger:proposal-keys $proposals $mode $out)
   (if (== $proposals ()) $out
     (let* ((($head $tail) (decons-atom $proposals)) (($serial $position $count) $head)
            ($more (input-ledger:call-keys (input-ledger:ordering-in $mode $serial $position) $serial 0 $count call $out)))
       (input-ledger:proposal-keys $tail $mode $more))))

; Actions without a proposal, by merging both lists sorted by serial.
(= (input-ledger:unproposed $actions $serials $out)
   (if (== $actions ()) $out
     (let* ((($action $rest) (decons-atom $actions)) (($serial $ticket $command) $action))
       (if (== $serials ()) (input-ledger:unproposed $rest () (cons-atom $action $out))
         (let* ((($next $later) (decons-atom $serials)))
           (if (< $next $serial) (input-ledger:unproposed $actions $later $out)
             (if (== $next $serial) (input-ledger:unproposed $rest $serials $out)
               (input-ledger:unproposed $rest $serials (cons-atom $action $out)))))))))

(= (input-ledger:attempts-of-ticket $view $ticket)
   (collapse (match $view (ledger:attempt $ticket $p $cmd $result $o) ($p $result))))

; A legacy action's calls are those of its commands that have an attempt.
(= (input-ledger:legacy-keys $view $actions $mode $out)
   (if (== $actions ()) $out
     (let* ((($action $rest) (decons-atom $actions)) (($serial $ticket $command) $action)
            ($count (size-atom (input-ledger:legacy-commands $command)))
            ($positions (input-ledger:attempts-of-ticket $view $ticket))
            ($ordering (input-ledger:legacy-ordering $view $mode $serial))
            ($more (foldl-atom $positions $out $acc $attempt
                     (input-ledger:legacy-key $ordering $serial $count $attempt $acc))))
       (input-ledger:legacy-keys $view $rest $mode $more))))

(= (input-ledger:legacy-key $ordering $serial $count ($position $result) $acc)
   (if (< $position $count) (cons-atom ($ordering 1 $position (legacy $serial $position)) $acc) $acc))

(= (input-ledger:entry-keys $view)
   (let* (($last (input-ledger:state-of $view "clock"))
          ($inputs (map-atom (input-ledger:all-heads $view) $head (input-ledger:input-key $view $head)))
          ($proposals (collapse (match $view (ledger:proposal $k $s $r $c $d $p $n) ($s $p $n))))
          ($serials (sort-atom (map-atom $proposals $proposal (input-ledger:first-of-three $proposal))))
          ($actions (sort-atom (collapse (match $view (ledger:action $t $r $s $tool $c $st $o $rc) ($s $t $c)))))
          ($calls (input-ledger:proposal-keys $proposals (after $last) $inputs))
          ($all (input-ledger:legacy-keys $view (input-ledger:unproposed $actions $serials ()) (after $last) $calls)))
     (sort-atom $all)))

(= (input-ledger:first-of-three ($a $b $c)) $a)

; The rows of a list of keys, in order. <cache> holds the last proposal's or action's parts:
; (parts <tag> <serial> <request> <items> <ticket> <attempts> <disposition>).
(= (input-ledger:call-parts $view $tag $serial $owner)
   (let* (($ticket (str:concat $owner (str:concat ":" (repr $serial))))
          ($action (input-ledger:actions-of $view $ticket))
          ($attempts (input-ledger:attempts-of-ticket $view $ticket)))
     (if (== $tag call)
         (case (let $key (input-ledger:key $serial)
                 (collapse (match $view (ledger:proposal $key $serial $r $c $d $p $n) ($r $c $d))))
           (((($request $commands $disposition))
             (parts $tag $serial $request (input-ledger:json-items $commands) $ticket $attempts $disposition))
            ($other (parts $tag $serial "" () $ticket () "admitted"))))
         (case $action
           ((((ledger:action $t $request $s $tool $command $st $o $rc))
             (parts $tag $serial $request (input-ledger:legacy-commands $command) $ticket $attempts "legacy"))
            ($other (parts $tag $serial "" () $ticket () "legacy")))))))

(= (input-ledger:attempt-in $attempts $position)
   (if (== $attempts ()) none
     (let* ((($head $tail) (decons-atom $attempts)) (($p $result) $head))
       (if (== $p $position) (found $result) (input-ledger:attempt-in $tail $position)))))

(= (input-ledger:call-row (parts $tag $serial $request $items $ticket $attempts $disposition) $index)
   (let $command (if (< $index (size-atom $items)) (json-stringify (index-atom $items $index)) "null")
     (case (input-ledger:attempt-in $attempts $index)
       (((found $result) ($serial $request $index $command $result $ticket $disposition))
        ($none ($serial $request $index $command none none $disposition))))))

(= (input-ledger:input-row $view $id $input-limit)
   (let $content (input-ledger:view-content $view $id (input-ledger:text-one $view $id))
     (-1 $id -1 "null" (input-ledger:input-record $content $input-limit) "input" "input")))

(= (input-ledger:key-rows $view $keys $input-limit $owner $cache $out)
   (if (== $keys ()) (list:reverse $out)
     (let* ((($key $rest) (decons-atom $keys)) (($ordering $category $ordinal $reference) $key))
       (case $reference
         (((input $id)
           (input-ledger:key-rows $view $rest $input-limit $owner $cache (cons-atom (input-ledger:input-row $view $id $input-limit) $out)))
          (($tag $serial $index)
           (let* (($parts (case $cache
                            (((parts $tag $serial $r $i $t $a $d) $cache)
                             ($other (input-ledger:call-parts $view $tag $serial $owner)))))
                  ($row (input-ledger:call-row $parts $index)))
             (input-ledger:key-rows $view $rest $input-limit $owner $parts (cons-atom $row $out)))))))))

; The rows of the entries from <offset>, <count> of them (all for 0), results shaped by <mode>.
(= (input-ledger:entry-rows $view $offset $count $input-limit $mode)
   (let* (($keys (input-ledger:entry-keys $view))
          ($rest (list:drop $offset $keys))
          ($page (if (== $count 0) $rest (list:take $count $rest)))
          ($owner (input-ledger:state-of $view "owner"))
          ($rows (input-ledger:key-rows $view $page $input-limit $owner none ())))
     (rows (size-atom $keys) (map-atom $rows $row (input-ledger:shaped-row $row $mode)))))

(= (input-ledger:first-of-call ($serial $request $position $command $record $ticket $disposition)) $serial)

; task:limited-result-sql: a recorded result whose text is longer than the limit keeps its
; first and last halves around a marker, and says so; any other record is unchanged.
; <mode> a limit (0 or less: the whole result), or dispositions: the record's disposition alone
; ("recorded" without one), as task:conversation-history asks for.
; A call's result is cut at the limit; an input is shown whole, as task_context_cetta's query
; cuts only the attempts' results.
(= (input-ledger:shaped-row ($serial $request $position $command $record $ticket $disposition) $mode)
   ($serial $request $position $command
    (if (== $mode dispositions) (input-ledger:disposition-record $record)
      (if (== $ticket "input") $record (input-ledger:limited $record $mode)))
    $ticket $disposition))

(= (input-ledger:disposition-record $record)
   (if (== $record none) none
     (case (catch (json-parse $record))
       (((JsonObject $pairs)
         (let $value (input-ledger:json-member $pairs "disposition")
           (json-stringify (JsonObject ((JsonPair "disposition"
             (if (or (== $value none) (== $value JsonNull)) (JsonString "recorded") $value)))))))
        ($other "{\"disposition\":\"recorded\"}")))))

;   (history <total> <pairs>): the conversation's entries from <offset>, <count> of them (all
;   for 0), with inputs without their bodies and results reduced to their dispositions.
(= (input-ledger:conversation-history-of $view $offset $count)
   (let (rows $total $page) (input-ledger:entry-rows $view $offset $count -1 dispositions)
     (history $total $page)))

(= (input-ledger:limited $record $limit)
   (if (or (<= $limit 0) (== $record none)) $record
     (case (catch (json-parse $record))
       (((JsonObject $pairs)
         (case (input-ledger:json-member $pairs "result")
           (((JsonString $text)
             (let $length (str:char-length $text)
               (if (> $length $limit)
                   (json-stringify (JsonObject (input-ledger:truncated-pairs $pairs (input-ledger:cut $text $length $limit))))
                   $record)))
            ($other $record))))
        ($other $record)))))

(= (input-ledger:cut $text $length $limit)
   (let* (($half (math:floor (/ $limit 2)))
          ($tokens (math:floor (/ (+ (- $length $limit) 3) 4))))
     (str:join "" ((str:char-slice $text 0 $half) "\n…" (repr $tokens)
                   " tokens truncated; task-read shows the whole result…\n"
                   (str:char-slice $text (- $length $half) $length)))))

(= (input-ledger:truncated-pairs $pairs $short)
   (let $replaced (map-atom $pairs $pair (input-ledger:replace-result $pair $short))
     (if (== (input-ledger:json-member $pairs "result_truncated") none)
         (union-atom $replaced ((JsonPair "result_truncated" (JsonBool True))))
         (map-atom $replaced $pair (input-ledger:mark-truncated $pair)))))

(= (input-ledger:replace-result (JsonPair $name $value) $short)
   (if (== $name "result") (JsonPair "result" (JsonString $short)) (JsonPair $name $value)))
(= (input-ledger:mark-truncated (JsonPair $name $value))
   (if (== $name "result_truncated") (JsonPair "result_truncated" (JsonBool True)) (JsonPair $name $value)))


(= (input-ledger:open-request ($id $content $status $kind $note $seq) $focus)
   (or (== $id $focus)
       (or (input-ledger:member $status ("active" "waiting-user" "deferred"))
           (and (== $status "processed") (or (== $kind "message") (== $kind "notification"))))))

(= (input-ledger:excerpt-row ($id $content $status $kind $note $seq))
   ($id $status $kind $note (str:char-slice $content 0 160)))

;   (conversation-rows <requests> <notes> <pairs>): with a limit of 0 or less every request
;   (id content status kind note) and note; else the open requests and the focus as
;   (id status kind note excerpt) and their notes, and results cut at the limit. The pairs
;   are the entries after the first <offset>.
(= (input-ledger:open-head (request $id $q $k $c $st $p $n $sm $dg) $focus)
   (input-ledger:open-request ($id "" $st $k $n $q) $focus))

(= (input-ledger:conversation-rows-of $view $focus $offset $limit)
   (let* (($heads (if (<= $limit 0) (input-ledger:all-heads $view)
                    (foldl-atom (input-ledger:all-heads $view) () $kept $head
                      (if (input-ledger:open-head $head $focus) (cons-atom $head $kept) $kept))))
          ($shown (map-atom (input-ledger:sorted-by input-ledger:seq-key $heads) $head (input-ledger:request-view-row $view $head)))
          ($requests (if (<= $limit 0) (map-atom $shown $row (input-ledger:without-seq $row))
                         (map-atom $shown $row (input-ledger:excerpt-row $row))))
          ($ids (map-atom $shown $row (input-ledger:first-of-row $row)))
          ($notes (if (<= $limit 0) (input-ledger:note-rows (collapse (match $view (ledger:note $r $s $t $src) ($r $s $t $src))))
                      (input-ledger:note-rows (input-ledger:of-requests $view $ids input-ledger:notes-of))))
          ((rows $total $pairs) (input-ledger:entry-rows $view $offset 0 $limit $limit)))
     (conversation-rows $requests $notes $pairs)))

(= (input-ledger:first-of-row ($id $content $status $kind $note $seq)) $id)

; task:initial-start: where the first window starts. Each entry weighs its input's bytes or
; its result's (at most 160000), its command's bytes, its request's characters and 160;
; everything fits in 90% of the window, or the window starts at the first entry whose
; suffix fits in 20% of it (the last entry when none does).
(= (input-ledger:entry-bytes ($serial $request $position $command $record $ticket $disposition))
   (let $body (if (== $serial -1)
                  (case (json-parse $record) (((JsonObject $pairs) (case (input-ledger:json-member $pairs "result") (((JsonNumber $n) (parse $n)) ($other 0)))) ($other 0)))
                  (if (== $record none) 0 (min (str:byte-length $record) 160000)))
     (+ (+ $body (str:byte-length $command)) (+ (str:char-length $request) 160))))

; From the last entry back (<reversed> sizes, <index> the last entry's): the smallest index
; whose suffix fits; the suffix only grows towards the first entry.
(= (input-ledger:suffix-start $reversed $index $tail $bound $best)
   (if (== $reversed ()) $best
     (let* ((($size $rest) (decons-atom $reversed))
            ($sum (+ $tail $size))
            ($next (if (<= $sum $bound) $index $best)))
       (input-ledger:suffix-start $rest (- $index 1) $sum $bound $next))))

(= (input-ledger:initial-start-of $view $capacity $ratio)
   (let* (((rows $count-of-keys $entries) (input-ledger:entry-rows $view 0 0 -2 0))
          ($sizes (map-atom $entries $entry (input-ledger:entry-bytes $entry)))
          ($total (foldl-atom $sizes 0 $sum $size (+ $sum $size)))
          ($count (size-atom $sizes)))
     (if (<= $total (* 0.9 (* $capacity $ratio))) 0
       (let $found (input-ledger:suffix-start (list:reverse $sizes) (- $count 1) 0 (* 0.2 (* $capacity $ratio)) none)
         (if (== $found none) (max 0 (- $count 1)) $found)))))

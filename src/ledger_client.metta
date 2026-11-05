; The caller's side of the native ledger: each verb is one LedgerCall on the ledger's
; server (src/input_ledger_server.metta), which runs src/input_ledger.metta next to its
; store. A request of the Python adapter (input_ack.py, through a persistent value
; worker) is (ledger <socket> <verb> <arguments>) and its answer (answer <value>).
!(import! &self (library db))
!(import! &self (library list))

(= (ledger-client:sessions) 3)

; A call whose reply was lost or whose server was not reachable is made again: a write
; carries its request id, so a repeat replays the answer of the first commit. An answer
; larger than one wire value is not lost: a read's is then read in pages.
(= (ledger-client:too-large $outcome)
   (== $outcome (Error ledger-client:call (unanswered ((ServiceFailedV1 reply-limit))))))

(= (ledger-client:lost $outcome)
   (case $outcome
     (((Error ledger-client:call (unanswered ((ServiceFailedV1 reply-limit)))) False)
      ((Error $where unavailable) True)
      ((Error $where uncertain) True)
      ((Error ledger-client:call (unanswered $why)) True)
      ((Error db:connect $why) True)
      ($other False))))

(= (ledger-client:answer $reply)
   (case $reply
     ((((LedgerAnswersV1 ($value))) $value)
      (((LedgerAnswersV1 $answers)) (Error ledger-client:call (answers $answers)))
      ((DbRefusedV1 $why) (Error ledger-client:call (refused $why)))
      ($other (Error ledger-client:call (unanswered $other))))))

(= (ledger-client:remote $db)
   (case (db:resource $db)
     (((db:remote $remote) $remote)
      ($other (Error ledger-client:call (not-remote $other))))))

(= (ledger-client:attempt $socket $route $request)
   (case (db:connect $socket)
     (((Error $where $why) (Error $where $why))
      ($db (let* (($answer (case (ledger-client:remote $db)
                             (((Error $where $why) (Error $where $why))
                              ($remote (ledger-client:answer (collapse (store:remote-call $remote $route $request)))))))
                  ($_ (db:close $db)))
             $answer)))))

(= (ledger-client:session $socket $route $request $left)
   (let $outcome (ledger-client:attempt $socket $route $request)
     (if (and (> $left 1) (ledger-client:lost $outcome))
         (ledger-client:session $socket $route $request (- $left 1))
         $outcome)))

(= (ledger-client:call $socket $verb $arguments)
   (let $outcome (ledger-client:session $socket LedgerCall (LedgerCallV1 $verb $arguments) (ledger-client:sessions))
     (if (ledger-client:too-large $outcome)
         (ledger-client:pages $socket $verb $arguments 0 (ledger-client:page-rows) none)
         $outcome)))

; One page of a read (input-ledger:page): (page more|last <value>).
(= (ledger-client:page $socket $verb $arguments $start $count)
   (ledger-client:session $socket LedgerPage (LedgerPageV1 $verb $arguments $start $count) (ledger-client:sessions)))

(= (ledger-client:page-rows) 2000)

; A page too large for the wire is asked again with half its rows.
(= (ledger-client:pages $socket $verb $arguments $start $count $merged)
   (let $outcome (ledger-client:page $socket $verb $arguments $start $count)
     (case $outcome
       (((page $more $value)
         (let $joined (ledger-client:merge $merged $value)
           (if (== $more more)
               (ledger-client:pages $socket $verb $arguments (+ $start $count) $count $joined)
               $joined)))
        ($other (if (and (ledger-client:too-large $other) (> $count 1))
                    (ledger-client:pages $socket $verb $arguments $start (ledger-client:half $count) $merged)
                    $other))))))

(= (ledger-client:half $count) (let $odd (% $count 2) (/ (- $count $odd) 2)))

; Pages joined: a list answer's rows in order; a record's expression fields joined so, its
; head and scalars those of the first page.
(= (ledger-client:merge $merged $value)
   (if (== $merged none) $value
     (if (ledger-client:rows-answer $merged) (union-atom $merged $value)
       (let* ((($head $fields) (decons-atom $merged)) (($again $more) (decons-atom $value)))
         (cons-atom $head (ledger-client:merge-fields $fields $more ()))))))

(= (ledger-client:rows-answer $answer)
   (if (== $answer ()) True (== (get-metatype (car-atom $answer)) Expression)))

(= (ledger-client:merge-fields $fields $more $out)
   (if (== $fields ()) (list:reverse $out)
     (let* ((($field $rest) (decons-atom $fields)) (($next $later) (decons-atom $more)))
       (ledger-client:merge-fields $rest $later
         (cons-atom (if (== (get-metatype $field) Expression) (union-atom $field $next) $field) $out)))))

(= (ledger-client:run $request)
   (case $request
     (((ledger $socket $verb $arguments) (answer (ledger-client:call $socket $verb $arguments)))
      ((ledger-page $socket $verb $arguments $start $count)
       (answer (ledger-client:page $socket $verb $arguments $start $count)))
      ($other (Error ledger-client:run (unknown-request $other))))))

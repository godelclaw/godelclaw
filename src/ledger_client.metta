; The caller's side of the native ledger: each verb is one LedgerCall on the ledger's
; server (src/input_ledger_server.metta), which runs src/input_ledger.metta next to its
; store. A request of the Python adapter (input_ack.py, through a persistent value
; worker) is (ledger <socket> <verb> <arguments>) and its answer (answer <value>).
!(import! &self (library db))

(= (ledger-client:sessions) 3)

; A call whose reply was lost or whose server was not reachable is made again: a write
; carries its request id, so a repeat replays the answer of the first commit.
(= (ledger-client:lost $outcome)
   (case $outcome
     (((Error $where unavailable) True)
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

(= (ledger-client:attempt $socket $verb $arguments)
   (case (db:connect $socket)
     (((Error $where $why) (Error $where $why))
      ($db (let* (($answer (case (ledger-client:remote $db)
                             (((Error $where $why) (Error $where $why))
                              ($remote (ledger-client:answer
                                         (collapse (store:remote-call $remote LedgerCall (LedgerCallV1 $verb $arguments))))))))
                  ($_ (db:close $db)))
             $answer)))))

(= (ledger-client:session $socket $verb $arguments $left)
   (let $outcome (ledger-client:attempt $socket $verb $arguments)
     (if (and (> $left 1) (ledger-client:lost $outcome))
         (ledger-client:session $socket $verb $arguments (- $left 1))
         $outcome)))

(= (ledger-client:call $socket $verb $arguments)
   (ledger-client:session $socket $verb $arguments (ledger-client:sessions)))

(= (ledger-client:run $request)
   (case $request
     (((ledger $socket $verb $arguments) (answer (ledger-client:call $socket $verb $arguments)))
      ($other (Error ledger-client:run (unknown-request $other))))))

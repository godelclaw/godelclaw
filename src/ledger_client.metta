; The caller's side of the native ledger: each verb is one LedgerCall on the ledger's
; server (src/input_ledger_server.metta), which runs src/input_ledger.metta next to its
; store. A request of the Python adapter (input_ack.py, through a persistent value
; worker) is (ledger <socket> <verb> <arguments>) and its answer (answer <value>).
; This client only sends server calls. It does not need PeTTa's local DB/query
; facade; use the same connection/adoption protocol in both native dialects.
!(case (import! &self (library store))
     (((Error $where $why)
       (let $reason (format-args "{}" ($why))
         (if (or (== $reason "import! expects a module name") (== $reason "unknown library"))
           (import! &self store) (Error $where $why))))
      ($loaded $loaded)))
!(case (import! &self (library space_service))
     (((Error $where $why)
       (let $reason (format-args "{}" ($why))
         (if (or (== $reason "import! expects a module name") (== $reason "unknown library"))
           (import! &self space_service) (Error $where $why))))
      ($loaded $loaded)))
!(case (import! &self (library list))
     (((Error $where $why)
       (let $reason (format-args "{}" ($why))
         (if (or (== $reason "import! expects a module name") (== $reason "unknown library"))
           (import! &self list) (Error $where $why))))
      ($loaded $loaded)))
!(import! &self (library system))

(= (ledger-client:sessions) 3)

(= (ledger-client:pageable $verb)
   (case $verb
     (("request-rows" True) ("actions" True) ("attempts" True) ("task-view" True) ("task-rows" True)
      ("conversation-rows" True) ("conversation-history" True) ("turn-dump" True) ("meta-prefix" True)
      ("llm-usage" True) ("llm-records" True)
      ($other False))))

; A call whose reply was lost or whose server was not reachable is made again: a write
; carries its request id, so a repeat replays the answer of the first commit. An answer
; larger than one wire value is not lost: a read's is then read in pages.
(: ledger-client:too-large (-> %Undefined% Bool))
(= (ledger-client:too-large $outcome)
   (case $outcome
     (((Error ledger-client:call (unanswered ((ServiceFailedV1 reply-limit)))) True)
      ((Error ledger-client:call (unanswered ((ServiceRefusedV1 reply-limit)))) True)
      ((Error ledger-client:call (unanswered ((StoreRemoteFailed limit)))) True)
      ((Error ledger-client:call (unanswered ((StoreRemoteFailed reply-limit)))) True)
      ((Error ledger-client:call (unanswered (reply-limit))) True)
      ((Error $where reply-limit) True)
      ((Error $where limit) True)
      ($other False))))

(: ledger-client:lost (-> %Undefined% Bool))
(= (ledger-client:lost $outcome)
   (if (ledger-client:too-large $outcome)
       False
       (case $outcome
         (((Error ledger-client:call (unanswered ((StoreRemoteFailed $_)))) False)
          ((Error $where unavailable) True)
          ((Error $where uncertain) True)
          ((Error ledger-client:call (unanswered $why)) True)
          ((Error ledger-client:connect $why) True)
          ($other False)))))

; The PeTTa server spells Boolean wire atoms in lowercase. Normalize only
; this schema's Boolean field, so HE consumers match the same typed result.
; Unrelated text and symbols in stored payloads are left untouched.
(: ledger-client:decoded (-> %Undefined% %Undefined%))
(= (ledger-client:decoded $value)
   (case $value
     (((LedgerChanged $flag)
       (case $flag ((true (LedgerChanged True)) (false (LedgerChanged False))
                    ($other $value))))
      ($other $value))))
(: ledger-client:answer (-> %Undefined% %Undefined%))
(= (ledger-client:answer $reply)
   (case $reply
     ((((LedgerAnswersV1 ($value))) (ledger-client:decoded $value))
      (((LedgerAnswersV1 $answers)) (Error ledger-client:call (answers $answers)))
      ((DbRefusedV1 $why) (Error ledger-client:call (refused $why)))
      ($other (Error ledger-client:call (unanswered $other))))))

(: ledger-client:connect (-> String %Undefined%))
(= (ledger-client:connect $socket)
   (case (space:connect $socket 30000)
     (((SpaceConnected $connection)
       (let* (($opened (store:remote-adopt $connection 30000))
              ($_ (space:disconnect $connection)))
         (case $opened
           (((StoreRemoteOpened $owner $_context) $owner)
            ($failure (Error ledger-client:connect $failure))))))
      ($failure (Error ledger-client:connect $failure)))))

(: ledger-client:attempt (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (ledger-client:attempt $socket $route $request)
   (case (ledger-client:connect $socket)
     (((Error $where $why) (Error $where $why))
      ($remote (case (ledger-client:answer (collapse (store:remote-call $remote $route $request)))
                 (($answer (let $_ (store:close $remote) $answer))))))))

(: ledger-client:session (-> %Undefined% %Undefined% %Undefined% %Undefined% %Undefined%))
(= (ledger-client:session $socket $route $request $left)
   (case (ledger-client:attempt $socket $route $request)
     (($outcome (if (and (> $left 1) (ledger-client:lost $outcome))
         (ledger-client:session $socket $route $request (- $left 1))
         $outcome)))))

(: ledger-client:call (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (ledger-client:call $socket $verb $arguments)
   (case (ledger-client:session $socket LedgerCall (LedgerCallV1 $verb $arguments) (ledger-client:sessions))
     (($outcome (if (and (ledger-client:too-large $outcome) (ledger-client:pageable $verb))
         (ledger-client:pages $socket $verb $arguments 0 (ledger-client:page-rows) none)
         $outcome)))))

; One page of a read (input-ledger:page): (page more|last <value>).
(= (ledger-client:page $socket $verb $arguments $start $count)
   (ledger-client:session $socket LedgerPage
     (LedgerPageV1 $verb $arguments $start $count (store:value-byte-limit))
     (ledger-client:sessions)))

(= (ledger-client:page-with-id $socket $read-id $verb $arguments $start $count)
   (ledger-client:session $socket LedgerPage
     (LedgerPageV1 $verb $arguments $start $count (store:value-byte-limit) $read-id)
     (ledger-client:sessions)))

(= (ledger-client:page-rows) 2000)

(= (ledger-client:fields-max-size $fields $best)
   (if (== $fields ()) $best
     (let ($field $rest) (decons-atom $fields)
       (if (== (get-metatype $field) Expression)
           (let* (($n (size-atom $field))
                  ($next (if (> $n $best) $n $best)))
             (ledger-client:fields-max-size $rest $next))
           (ledger-client:fields-max-size $rest $best)))))

(= (ledger-client:page-taken $value $fallback)
   (if (== (get-metatype $value) Expression)
       (if (ledger-client:rows-answer $value)
           (let $n (size-atom $value) (if (> $n 0) $n $fallback))
           (let* ((($head $fields) (decons-atom $value))
                  ($n (ledger-client:fields-max-size $fields 0)))
             (if (> $n 0) $n $fallback)))
       $fallback))

(= (ledger-client:grow $count $cap)
   (let $doubled (* $count 2)
     (if (< $doubled $cap) $doubled $cap)))

; A page too large for the wire is asked again with half its rows; after a successful
; slice, a halved count can grow back up to its initial cap.
(= (ledger-client:pages $socket $verb $arguments $start $count $merged)
   (ledger-client:pages-run $socket (system:uuid4-hex) $verb $arguments $start $count $count $merged))

(= (ledger-client:pages-run $socket $read-id $verb $arguments $start $count $cap $merged)
   (let $outcome (ledger-client:page-with-id $socket $read-id $verb $arguments $start $count)
     (case $outcome
       (((page $more $value)
         (let* (($joined (ledger-client:merge $merged $value))
                ($taken (ledger-client:page-taken $value $count))
                ($next-count (ledger-client:grow $taken $cap)))
           (if (== $more more)
               (ledger-client:pages-run $socket $read-id $verb $arguments (+ $start $taken) $next-count $cap $joined)
               $joined)))
        ($other (if (and (ledger-client:too-large $other) (> $count 1))
                    (ledger-client:pages-run $socket $read-id $verb $arguments $start (ledger-client:half $count) $cap $merged)
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

; Native CLI to inspect and resolve an instance's protected requests on its
; native input ledger (METTACLAW_INPUT_LEDGER_SOCKET).
; Replaces scripts/strategy_ctl.py with exact JSON and exit-code parity.
!(import! &self ./state_file_cetta.metta)
!(import! &self ./cli_values_cetta.metta)
!(import! &self ./ledger_client.metta)
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library list))

(= (sctl:encoded $value)
   (json:stringify-python-with $value ((ensure-ascii False) (sort-keys True) (separators "," ":"))))

(= (sctl:digest $value)
   (str:sha256 (sctl:encoded $value)))

(= (sctl:flag? $v)
   (case $v ((True True) (true True) (1 True) ((LedgerChanged True) True) ($other False))))

(= (sctl:socket $override)
   (if (!= $override "") $override (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "")))

(= (sctl:state-lookup $entries $key $fallback)
   (if (== $entries ()) $fallback
     (let ($k $v) (car-atom $entries)
       (if (== $k $key) $v (sctl:state-lookup (cdr-atom $entries) $key $fallback)))))

(= (sctl:owner $socket)
   (let* (($entries (ledger-client:call $socket "state" ()))
          ($found (if (== (get-metatype $entries) Expression) (sctl:state-lookup $entries "owner" none) none)))
     (if (and (!= $found none) (== (get-type $found) String)) $found
       (let* (($cand (system:uuid4-hex))
              ($set (ledger-client:call $socket "owner" ($cand (system:uuid4-hex))))
              ($after (ledger-client:call $socket "state" ()))
              ($resolved (if (== (get-metatype $after) Expression) (sctl:state-lookup $after "owner" $cand) $cand)))
         (if (== (get-type $resolved) String) $resolved $cand)))))

(= (sctl:counts-obj $items)
   (if (== $items ()) ()
     (let (($k $v) $rest) ((car-atom $items) (cdr-atom $items))
       (cons-atom (JsonPair $k $v) (sctl:counts-obj $rest)))))

(= (sctl:unfinished-list $items)
   (if (== $items ()) ()
     (let (($id $summary $digest $checker) $rest) ((car-atom $items) (cdr-atom $items))
       (cons-atom (JsonObject ((JsonPair "id" $id) (JsonPair "summary" $summary)
                               (JsonPair "content_digest" $digest) (JsonPair "checker" $checker)))
                  (sctl:unfinished-list $rest)))))

(= (sctl:action-results $rows $limit)
   (if (== $rows ()) ()
     (let ($row $rest) ((car-atom $rows) (cdr-atom $rows))
       (case $row
         (((action $ticket $req $ser $tool $cmd $st $obs $receipt $payload)
           (case $receipt
             (((receipt $disp $dom)
               (let* (($pstr (if (== (get-type $payload) String) $payload (repr $payload)))
                      ($plen (str:char-length $pstr))
                      ($sliced (str:char-slice $pstr 0 (min $limit $plen)))
                      ($obj (JsonObject ((JsonPair "ticket" $ticket)
                                         (JsonPair "payload" $sliced)
                                         (JsonPair "disposition" $disp)
                                         (JsonPair "domain" $dom)
                                         (JsonPair "payload_digest" (sctl:digest $pstr))
                                         (JsonPair "truncated" (> $plen $limit))))))
                 (cons-atom $obj (sctl:action-results $rest $limit))))
              ($no-rcpt (sctl:action-results $rest $limit)))))
          ($other (sctl:action-results $rest $limit)))))))

(= (sctl:status $socket)
   (case (catch (ledger-client:call $socket "request-protected" ("")))
     (((protected $counts-raw $total $unfin-raw $focus-raw)
       (let* (($counts (JsonObject (sctl:counts-obj $counts-raw)))
              ($unfin (sctl:unfinished-list $unfin-raw))
              ($active-count (sf:get $counts "active" 0))
              ($omitted (max 0 (- $active-count (size-atom $unfin))))
              (($req-text $req-len) (case $focus-raw (((slice $t $l) ($t $l)) ($none ("" 0)))))
              ($owner (sctl:owner $socket))
              ($act-raw (catch (ledger-client:call $socket "actions" ((protected "")))))
              ($results (if (== (get-metatype $act-raw) Expression) (sctl:action-results $act-raw 6000) ())))
         (SctlOk (JsonObject ((JsonPair "owner" $owner)
                              (JsonPair "focus" JsonNull)
                              (JsonPair "request" $req-text)
                              (JsonPair "unfinished" (JsonArray $unfin))
                              (JsonPair "counts" $counts)
                              (JsonPair "omitted_active" $omitted)
                              (JsonPair "request_truncated" (> $req-len 4000))
                              (JsonPair "request_index" (JsonObject ((JsonPair "retrieve" (JsonArray ("request-page" 0)))
                                                                     (JsonPair "scope" "all retained requests"))))
                              (JsonPair "results" (JsonArray $results)))))))
      ($err (SctlError (str:concat "request-protected failed: " (repr $err)))))))

(= (sctl:infer-kind $key)
   (if (str:starts-with $key "input:") "message"
     (if (str:starts-with $key "goal:") "goal"
       (if (== $key "autonomous") "autonomous" "task"))))

(= (sctl:accept $socket $key $content $checker)
   (let* (($kind (sctl:infer-kind $key))
          ($summary (str:char-slice $content 0 (min 180 (str:char-length $content))))
          ($dg (sctl:digest $content))
          ($ans (catch (ledger-client:call $socket "request-accept"
                         ($key $content $checker $kind $summary $dg (system:uuid4-hex))))))
     (case $ans
       ((created (SctlOk True))
        (exists (SctlOk False))
        (conflict (SctlError "request identity reused for different intent"))
        ($other (SctlError (str:concat "request-accept failed: " (repr $other))))))))

(= (sctl:cancel $socket $key $confirmed)
   (if (not $confirmed) (SctlOk False)
     (let $ans (catch (ledger-client:call $socket "request-cancel" ($key (system:uuid4-hex))))
       (case $ans
         (((Error $w $why) (SctlError (str:concat "request-cancel failed: " (repr $why))))
          ($ok (SctlOk (sctl:flag? $ok))))))))

(= (sctl:reconcile $socket $ticket $result $confirmed)
   (if (not $confirmed) (SctlError "--confirm is required")
     (let* (($dg (sctl:digest $result))
            ($len (str:char-length $result))
            ($ans (catch (ledger-client:call $socket "report"
                           ($ticket $result "returned" "unknown" $dg $len (system:uuid4-hex))))))
       (case $ans
         (((Error $w $why) (SctlError (str:concat "report failed: " (repr $why))))
          ($ok (SctlOk (sctl:flag? $ok))))))))

(= (sctl:find-action $rows $request)
   (if (== $rows ()) none
     (let ($row $rest) ((car-atom $rows) (cdr-atom $rows))
       (case $row
         (((action $tk $req $ser $tool $cmd $st $obs $rcpt $pay)
           (if (== $req $request) $row (sctl:find-action $rest $request)))
          ($other (sctl:find-action $rest $request)))))))

(= (sctl:finish $socket $request $ticket $confirmed)
   (if (not $confirmed) (SctlError "--confirm is required")
     (let* (($got (catch (ledger-client:call $socket "request-get" ($request text))))
            ($active-op (case $got
                          (((got $_id $_k $checker $status $_p $_n $_t)
                            (and (== $status "active") (== $checker "operator")))
                           ($other False)))))
       (if (not $active-op) (SctlOk False)
         (let* (($acts (catch (ledger-client:call $socket "actions" ((ticket $ticket)))))
                ($found (if (== (get-metatype $acts) Expression) (sctl:find-action $acts $request) none))
                ($eligible (case $found
                             (((action $_tk $_rq $_sr $_tl $_cm $_st $obs (receipt $disp $_dom) $_pl)
                               (and (sctl:flag? $obs) (== $disp "returned")))
                              ($other-el False)))))
           (if (not $eligible) (SctlOk False)
             (let $att (catch (ledger-client:call $socket "attest" ($request $ticket (system:uuid4-hex))))
               (if (not (sctl:flag? $att)) (SctlOk False)
                 (let $fin (catch (ledger-client:call $socket "finish" ($request $ticket (system:uuid4-hex))))
                   (SctlOk (sctl:flag? $fin)))))))))))

; Parse positional args mixed with --socket <path>, --checker <name>, --confirm.
(= (sctl:parse $args $pos $opts)
   (if (== $args ()) (SctlParsed (list:reverse $pos) $opts)
     (let ($head $rest) ((car-atom $args) (cdr-atom $args))
       (if (== $head "--confirm")
         (sctl:parse $rest $pos (sf:put $opts "--confirm" True))
         (if (or (== $head "--checker") (== $head "--socket"))
           (if (== $rest ()) (SctlError (str:concat $head " requires a value"))
             (sctl:parse (cdr-atom $rest) $pos (sf:put $opts $head (car-atom $rest))))
           (if (str:starts-with $head "-")
             (SctlError (str:concat "unknown option: " $head))
             (sctl:parse $rest (cons-atom $head $pos) $opts)))))))

(= (sctl:emit $res)
   (case $res
     (((SctlOk $val)
       (let* (($out (json:stringify-python-with $val ((ensure-ascii False) (sort-keys True))))
              ($w (system:write stdout (str:concat $out "\n"))))
         (if (or (== $val False) (== $val JsonNull)) 1 0)))
      ((SctlError $msg)
       (progn (system:write stderr (str:concat $msg "\n")) 2)))))

(= (sctl:dispatch $pos $opts)
   (if (== $pos ()) (SctlError "operation required: status|accept|finish|reconcile|cancel")
     (let* (($op (car-atom $pos))
            ($rest (cdr-atom $pos))
            ($socket (sctl:socket (sf:get $opts "--socket" "")))
            ($confirm (sf:get $opts "--confirm" False)))
       (if (== $socket "") (SctlError "METTACLAW_INPUT_LEDGER_SOCKET is not set")
         (case $op
           (("status"
             (if (!= $rest ()) (SctlError "status takes no positional arguments")
               (sctl:status $socket)))
            ("accept"
             (if (!= (size-atom $rest) 2) (SctlError "accept requires <request> <content>")
               (sctl:accept $socket (index-atom $rest 0) (index-atom $rest 1)
                 (sf:get $opts "--checker" "operator"))))
            ("cancel"
             (if (or (!= (size-atom $rest) 1) (not $confirm)) (SctlError "cancel requires <request> --confirm")
               (sctl:cancel $socket (index-atom $rest 0) $confirm)))
            ("reconcile"
             (if (or (!= (size-atom $rest) 2) (not $confirm)) (SctlError "reconcile requires <ticket> <result> --confirm")
               (sctl:reconcile $socket (index-atom $rest 0) (index-atom $rest 1) $confirm)))
            ("finish"
             (if (or (!= (size-atom $rest) 2) (not $confirm)) (SctlError "finish requires <request> <ticket> --confirm")
               (sctl:finish $socket (index-atom $rest 0) (index-atom $rest 1) $confirm)))
            ($bad (SctlError (str:concat "unknown operation: " $bad)))))))))

(= (sctl:main $args)
   (case (sctl:parse (cli:strings $args) () (JsonObject ()))
     (((SctlParsed $pos $opts) (sctl:emit (sctl:dispatch $pos $opts)))
      ((SctlError $msg) (progn (system:write stderr (str:concat $msg "\n")) 2)))))

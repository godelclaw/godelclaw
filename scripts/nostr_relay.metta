; A closed Nostr relay for agent mail (NIP-01, NIP-09, NIP-42), natively on
; CeTTa: the relay Mettapedia.Protocols.Nostr.Relay models and
; tests/nostr_relay_fixture.py implements in Python.
;
; Each connection is sent a fresh challenge, and nothing is served until it
; signs in with a key on the allowlist and an address of this relay. A
; signed-in key may publish only events it signed, and may read only events
; it wrote or that name it in a `p` tag. A deletion request removes only its
; author's events. Ephemeral events are relayed, not stored. The allowlist
; file is watched: a change takes effect at once, and the connections of a
; removed key are closed. Stored events live in a named space of CeTTa's
; space service, journaled there, so they outlast a restart; a stored answer is
; each filter's newest events, newest first.
;
; One io:wait-ready loop serves the listener, every connection, the sends and
; the allowlist watch. Plain ws on the address given; TLS belongs in a proxy
; in front.
;
;   cetta --lang petta --quiet scripts/nostr_relay.metta [--config PATH]
;
; relay.toml (paths relative to it):
;   host = "127.0.0.1"
;   port = 7447
;   urls = ["wss://relay.example"]  # an AUTH event must name one; [] accepts any
;   allowlist = "allow.toml"        # [members] NAME = "hex public key"
;   store = "store.sock"            # the socket of a cetta-space-service
; and, optionally, space (the store's name, default "nostr-relay"),
; max_message_bytes, max_connections, idle_ms, auth_window, reject_future,
; max_limit, max_subscriptions and max_filters.
!(import! &self (library system))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library io))
!(import! &self (library toml))
!(import! &self (library crypto))
!(import! &self (library space_service))
!(import! &self "../src/nostr_mail_cetta.metta")

; ── text, lists and facts ───────────────────────────────────────────────────
(= (rl:text $value) (if (== (get-type $value) String) $value (repr $value)))
(= (rl:cat $parts) (str:join "" $parts))
(= (rl:log $line)
   (let $stamp (system:format-time "%Y-%m-%d %H:%M:%S " (system:wall-time) local)
     (system:write stderr (rl:cat ($stamp $line "\n")))))
(= (rl:reverse-onto $items $done)
   (if (== $items ()) $done (rl:reverse-onto (cdr-atom $items) (cons-atom (car-atom $items) $done))))
(= (rl:append $front $back) (rl:reverse-onto (rl:reverse-onto $front ()) $back))
(= (rl:last $items $default)
   (if (== $items ()) $default (rl:last (cdr-atom $items) (car-atom $items))))
(= (rl:pairs $value) (case $value (((JsonObject $pairs) $pairs) ($other ()))))
(= (rl:is-object $value) (case $value (((JsonObject $pairs) true) ($other false))))
(= (rl:is-array $value) (case $value (((JsonArray $items) true) ($other false))))
(= (rl:all-text $items)
   (if (== $items ()) true (if (nostr:is-text (car-atom $items)) (rl:all-text (cdr-atom $items)) false)))
(= (rl:all-integers $items)
   (if (== $items ()) true (if (nostr:is-integer (car-atom $items)) (rl:all-integers (cdr-atom $items)) false)))
(= (rl:intersects $left $right)
   (if (== $left ()) false (if (nostr:member (car-atom $left) $right) true (rl:intersects (cdr-atom $left) $right))))
(= (rl:field-or $object $key $default)
   (let $value (nostr:field $object $key) (if (== $value nostr:missing) $default $value)))

; remove-atom, like add-atom, takes its argument as written: bind it first.
(= (rl:remove-all $atoms)
   (if (== $atoms ()) ()
     (let* (($atom (car-atom $atoms)) ($removed (remove-atom &self $atom))) (rl:remove-all (cdr-atom $atoms)))))
(= (rl:get $name)
   (let $values (collapse (match &self (RlSetting $name $value) $value))
     (if (== $values ()) () (car-atom $values))))
(= (rl:set $name $value)
   (let $gone (rl:remove-all (collapse (match &self (RlSetting $name $old) (RlSetting $name $old))))
     (add-atom &self (RlSetting $name $value))))

; ── paths ───────────────────────────────────────────────────────────────────
(= (rl:expand $path)
   (let $home (system:getenv-or-default "HOME" "")
     (if (== $path "~") $home
       (if (str:starts-with $path "~/") (rl:cat ($home (str:byte-slice $path 1 (str:byte-length $path)))) $path))))
(= (rl:parent $path)
   (let $parts (str:split "/" $path)
     (if (== (size-atom $parts) 1) "."
       (let $joined (str:join "/" (rl:reverse-onto (cdr-atom (rl:reverse-onto $parts ())) ()))
         (if (== $joined "") "/" $joined)))))
(= (rl:base-name $path) (rl:last (str:split "/" $path) ""))
(= (rl:relative $directory $value)
   (let $path (rl:expand $value)
     (if (str:starts-with $path "/") $path (rl:cat ($directory "/" $path)))))
(= (rl:read-file $path)
   (case (fs:read-text-result $path 0 "raw")
     (((ok $text) $text)
      (("ok" $text2) $text2)
      ($refused (rl:failed (rl:cat ($path ": " (rl:text $refused))))))))

; ── configuration and the allowlist ─────────────────────────────────────────
(= (rl:configure $config-path)
   (let* (($path (rl:expand $config-path)) ($text (rl:read-file $path)))
     (case $text
       (((rl:failed $why) $text)
        ($read (case (toml:parse $read)
                 (((JsonObject $pairs) (rl:configure-from (JsonObject $pairs) (rl:parent $path)))
                  ($bad (rl:failed (rl:cat ($path ": not TOML")))))))))))

(= (rl:texts $items)
   (if (== $items ()) ()
     (let $rest (rl:texts (cdr-atom $items))
       (if (nostr:is-text (car-atom $items)) (cons-atom (car-atom $items) $rest) $rest))))

(= (rl:configure-from $config $directory)
   (let* (($allow (rl:field-or $config "allowlist" ())) ($store (rl:field-or $config "store" ())))
     (if (not (nostr:is-text $allow)) (rl:failed "the configuration names no allowlist")
       (if (not (nostr:is-text $store)) (rl:failed "the configuration names no store socket")
         (let* (($host (rl:field-or $config "host" "127.0.0.1"))
                ($urls (rl:texts (nostr:items (rl:field-or $config "urls" (JsonArray ())))))
                ($allow-path (rl:relative $directory $allow))
                ($store-path (rl:relative $directory $store))
                ($s1 (rl:set host $host))
                ($s2 (rl:set port (rl:field-or $config "port" 7447)))
                ($s3 (rl:set urls $urls))
                ($s4 (rl:set allowlist $allow-path))
                ($s5 (rl:set store $store-path))
                ($s5b (rl:set space-name (rl:field-or $config "space" "nostr-relay")))
                ($s6 (rl:set max-message-bytes (rl:field-or $config "max_message_bytes" 131072)))
                ($s7 (rl:set max-connections (rl:field-or $config "max_connections" 128)))
                ($s8 (rl:set idle-ms (rl:field-or $config "idle_ms" 30000)))
                ($s9 (rl:set auth-window (rl:field-or $config "auth_window" 600)))
                ($s10 (rl:set reject-future (rl:field-or $config "reject_future" 1800)))
                ($s11 (rl:set max-limit (rl:field-or $config "max_limit" 5000)))
                ($s12 (rl:set max-subscriptions (rl:field-or $config "max_subscriptions" 20)))
                ($s13 (rl:set max-filters (rl:field-or $config "max_filters" 10))))
           RlConfigured)))))

; The keys of [members], each a 64-digit lowercase hex public key.
(= (rl:member-keys $pairs $keys)
   (if (== $pairs ()) $keys
     (case (car-atom $pairs)
       (((JsonPair $name $key)
         (rl:member-keys (cdr-atom $pairs) (if (nostr:is-hex $key 64) (cons-atom $key $keys) $keys)))
        ($other (rl:member-keys (cdr-atom $pairs) $keys))))))

(= (rl:load-allowlist)
   (let $path (rl:get allowlist)
     (case (rl:read-file $path)
       (((rl:failed $why) (rl:failed $why))
        ($text (case (toml:parse $text)
                 (((JsonObject $pairs)
                   (rl:allow (rl:member-keys (rl:pairs (rl:field-or (JsonObject $pairs) "members" (JsonObject ()))) ())))
                  ($bad (rl:failed (rl:cat ($path ": not TOML")))))))))))

(= (rl:add-allowed $keys)
   (if (== $keys ()) ()
     (let* (($key (car-atom $keys)) ($added (add-atom &self (RlAllow $key)))) (rl:add-allowed (cdr-atom $keys)))))
(= (rl:allow $keys)
   (let* (($gone (rl:remove-all (collapse (match &self (RlAllow $k) (RlAllow $k)))))
          ($added (rl:add-allowed $keys))
          ($closed (rl:close-removed (collapse (match &self (RlConn $c $ch $key) (RlConn $c $ch $key))))))
     (RlAllowed (size-atom $keys))))
(= (rl:allowed $key) (not (== (collapse (match &self (RlAllow $key) $key)) ())))

; A key taken off the allowlist loses its connections.
(= (rl:close-removed $conns)
   (if (== $conns ()) ()
     (let $done (case (car-atom $conns)
                  (((RlConn $conn $challenge $key)
                    (if (== $key ()) () (if (rl:allowed $key) () (rl:end $conn "restricted: not on the allowlist"))))))
       (rl:close-removed (cdr-atom $conns)))))

(= (rl:reload)
   (case (rl:load-allowlist)
     (((rl:failed $why) (rl:log (rl:cat ("allowlist unchanged: " $why))))
      ((RlAllowed $count) (rl:log (rl:cat ("allowlist: " (rl:text $count) " keys")))))))

; ── the store ───────────────────────────────────────────────────────────────
; A named space of the space service at `store`: each event as (RelayEvent ID
; PUBKEY KIND CREATED JSON), and a copy (RelayCopy READER ID PUBKEY KIND
; CREATED JSON) for each key that may read it, its author and every key a p
; tag names, so that what a key may read is one query. Writes are batches
; committed at the revision they were read at; one that finds the space
; changed is done again.
(= (rl:space)
   (let* (($socket (rl:get store)) ($name (rl:get space-name)))
     (case (space:open $socket $name native)
       (((reply $rid ok (space $u $i $n $b $k $r $c)) (space $u $i $n $b $k $r $c))
        ($error (rl:failed (rl:cat ("the store is unavailable: " (rl:text $error)))))))))

(= (rl:open-store)
   (case (rl:space) (((rl:failed $why) (rl:failed $why)) ($space RlStoreOpen))))

(= (rl:has $id)
   (case (rl:space)
     (((rl:failed $why) false)
      ($space (case (space:count (rl:get store) $space (RelayEvent $id $p $k $c $j))
                (((reply $rid ok $count) (> $count 0)) ($error false)))))))

; Every row a query selects, page by page; a query the service cannot answer
; whole is a failure, never a shorter answer.
(= (rl:rows $space $pattern $template)
   (let $socket (rl:get store)
     (case (space:query $socket $space $pattern $template 65536 30000)
       (((reply $rid ok (snapshot $boot $cursor $source $revision $total $lease))
         (rl:pages $socket (snapshot $boot $cursor $source $revision $total $lease) 0 $total ()))
        ($error (rl:failed (rl:cat ("the store could not answer: " (rl:text $error)))))))))
(= (rl:pages $socket $snapshot $offset $total $done)
   (if (>= $offset $total) (let $closed (space:close $socket $snapshot) (rl:reverse-onto $done ()))
     (case (space:fetch $socket $snapshot $offset 1024)
       (((reply $rid ok (page $a $b $rows $c $d))
         (let $items (cdr-atom $rows)
           (if (== $items ()) (let $closed (space:close $socket $snapshot) (rl:failed "the store sent an empty page"))
             (rl:pages $socket $snapshot (+ $offset (size-atom $items)) $total (rl:reverse-onto $items $done)))))
        ($error (let $closed (space:close $socket $snapshot) (rl:failed (rl:cat ("the store could not page: " (rl:text $error))))))))))

(= (rl:distinct $items $done)
   (if (== $items ()) (rl:reverse-onto $done ())
     (let $item (car-atom $items)
       (rl:distinct (cdr-atom $items) (if (nostr:member $item $done) $done (cons-atom $item $done))))))
(= (rl:keys-only $values)
   (if (== $values ()) ()
     (let $rest (rl:keys-only (cdr-atom $values))
       (if (nostr:is-hex (car-atom $values) 64) (cons-atom (car-atom $values) $rest) $rest))))
; The keys that may read an event: its author and each key a p tag names.
(= (rl:readers $event)
   (rl:distinct (cons-atom (nostr:field $event "pubkey") (rl:keys-only (nostr:tag-values $event "p"))) ()))

(= (rl:copy-ops $readers $id $pubkey $kind $created $json)
   (if (== $readers ()) ()
     (let $reader (car-atom $readers)
       (cons-atom (add (RelayCopy $reader $id $pubkey $kind $created $json))
         (rl:copy-ops (cdr-atom $readers) $id $pubkey $kind $created $json)))))
(= (rl:removals $rows)
   (if (== $rows ()) () (let $row (car-atom $rows) (cons-atom (remove $row) (rl:removals (cdr-atom $rows))))))

; NIP-09: the deleter's own events among those its e tags name, and their copies.
(= (rl:deletion-ops $space $me $ids)
   (if (== $ids ()) ()
     (let $id (car-atom $ids)
       (case (rl:rows $space (RelayEvent $id $me $k $c $j) (RelayEvent $id $me $k $c $j))
         (((rl:failed $why) (rl:failed $why))
          ($events
           (case (rl:rows $space (RelayCopy $r $id $me $k2 $c2 $j2) (RelayCopy $r $id $me $k2 $c2 $j2))
             (((rl:failed $why2) (rl:failed $why2))
              ($copies
               (case (rl:deletion-ops $space $me (cdr-atom $ids))
                 (((rl:failed $why3) (rl:failed $why3))
                  ($rest (rl:append (rl:removals $events) (rl:append (rl:removals $copies) $rest))))))))))))))

(= (rl:store $event $id $me) (rl:store-try $event $id $me 5))
(= (rl:store-try $event $id $me $tries)
   (case (rl:space)
     (((rl:failed $why) (let $logged (rl:log (rl:cat ("cannot store " $id ": " $why))) failed))
      ($space
       (let* (($pubkey (nostr:field $event "pubkey")) ($kind (nostr:field $event "kind"))
              ($created (nostr:field $event "created_at")) ($json (nostr:compact $event))
              ($removals (if (== $kind 5) (rl:deletion-ops $space $me (rl:distinct (nostr:tag-values $event "e") ())) ())))
         (case $removals
           (((rl:failed $why2) (let $logged2 (rl:log (rl:cat ("cannot store " $id ": " $why2))) failed))
            ($ops
             (let* (($adds (cons-atom (add (RelayEvent $id $pubkey $kind $created $json))
                             (rl:copy-ops (rl:readers $event) $id $pubkey $kind $created $json)))
                    ($batch (cons-atom ops (rl:append $ops $adds))))
               (case (space:batch (rl:get store) $space $batch)
                 (((reply $rid ok $committed) stored)
                  ((reply $rid2 error stale-revision)
                   (if (> $tries 1) (rl:store-try $event $id $me (- $tries 1))
                     (let $logged3 (rl:log (rl:cat ("cannot store " $id ": the store kept changing"))) failed)))
                  ((space:uncertain $rid3 $reason)
                   (case (space:receipt (rl:get store) $space $rid3)
                     (((reply $rid4 ok (reply $original ok $answer)) stored)
                      ($unknown (let $logged4 (rl:log (rl:cat ("cannot store " $id ": " (rl:text $reason)))) failed)))))
                  ($error (let $logged5 (rl:log (rl:cat ("cannot store " $id ": " (rl:text $error)))) failed)))))))))))))

; ── filters (NIP-01) ────────────────────────────────────────────────────────
; A filter is an object; ids, authors and #x conditions list text, kinds lists
; integers, since, until and limit are integers.
(= (rl:texts-field $filter $key)
   (let $value (nostr:field $filter $key)
     (if (== $value nostr:missing) true
       (case $value (((JsonArray $items) (rl:all-text $items)) ($other false))))))
(= (rl:integers-field $filter $key)
   (let $value (nostr:field $filter $key)
     (if (== $value nostr:missing) true
       (case $value (((JsonArray $items) (rl:all-integers $items)) ($other false))))))
(= (rl:integer-field $filter $key)
   (let $value (nostr:field $filter $key) (if (== $value nostr:missing) true (nostr:is-integer $value))))
; A single-letter tag name, which filters' #x conditions read (NIP-01).
(= (rl:indexed-name $name)
   (if (nostr:is-text $name) (if (== (str:byte-length $name) 1) (str:is-all-in alpha $name) false) false))
(= (rl:tag-key $key) (if (str:starts-with $key "#") (rl:indexed-name (str:byte-slice $key 1 (str:byte-length $key))) false))
(= (rl:tag-fields-valid $pairs)
   (if (== $pairs ()) true
     (case (car-atom $pairs)
       (((JsonPair $key $value)
         (if (rl:tag-key $key)
           (case $value
             (((JsonArray $items) (if (rl:all-text $items) (rl:tag-fields-valid (cdr-atom $pairs)) false))
              ($other false)))
           (rl:tag-fields-valid (cdr-atom $pairs))))))))
(= (rl:valid-filter $filter)
   (if (not (rl:is-object $filter)) false
     (if (not (rl:texts-field $filter "ids")) false
       (if (not (rl:texts-field $filter "authors")) false
         (if (not (rl:integers-field $filter "kinds")) false
           (if (not (rl:integer-field $filter "since")) false
             (if (not (rl:integer-field $filter "until")) false
               (if (not (rl:integer-field $filter "limit")) false
                 (rl:tag-fields-valid (rl:pairs $filter))))))))))

(= (rl:valid-filters $filters)
   (if (== $filters ()) true (if (rl:valid-filter (car-atom $filters)) (rl:valid-filters (cdr-atom $filters)) false)))

(= (rl:within $filter $key $value)
   (let $values (nostr:field $filter $key)
     (if (== $values nostr:missing) true (nostr:member $value (nostr:items $values)))))
(= (rl:since-ok $filter $created)
   (let $since (nostr:field $filter "since") (if (== $since nostr:missing) true (>= $created $since))))
(= (rl:until-ok $filter $created)
   (let $until (nostr:field $filter "until") (if (== $until nostr:missing) true (<= $created $until))))
(= (rl:tags-match $event $filter $pairs)
   (if (== $pairs ()) true
     (case (car-atom $pairs)
       (((JsonPair $key $values)
         (if (rl:tag-key $key)
           (if (rl:intersects (nostr:tag-values $event (str:byte-slice $key 1 2)) (nostr:items (nostr:field $filter $key)))
             (rl:tags-match $event $filter (cdr-atom $pairs))
             false)
           (rl:tags-match $event $filter (cdr-atom $pairs))))))))

; Live delivery: every condition of the filter holds for the event.
(= (rl:accepts $event $filter)
   (let $created (nostr:field $event "created_at")
     (if (not (rl:within $filter "ids" (nostr:field $event "id"))) false
       (if (not (rl:within $filter "authors" (nostr:field $event "pubkey"))) false
         (if (not (rl:within $filter "kinds" (nostr:field $event "kind"))) false
           (if (not (rl:since-ok $filter $created)) false
             (if (not (rl:until-ok $filter $created)) false
               (rl:tags-match $event $filter (rl:pairs $filter)))))))))
(= (rl:accepts-any $event $filters)
   (if (== $filters ()) false
     (if (rl:accepts $event (car-atom $filters)) true (rl:accepts-any $event (cdr-atom $filters)))))
(= (rl:readable $event $key)
   (if (== (nostr:field $event "pubkey") $key) true (nostr:member $key (nostr:tag-values $event "p"))))

; A stored answer: each filter's newest events among those the key may read,
; newest first and ties by id, the filter's limit capped by max_limit; each
; event once, in the order of the filters.
(= (rl:limit-of $filter)
   (let* (($most (rl:get max-limit)) ($limit (nostr:field $filter "limit")))
     (if (== $limit nostr:missing) $most (if (< $limit $most) (if (< $limit 0) 0 $limit) $most))))

; (RlStored ID CREATED EVENT JSON) for each row, the event parsed once.
(= (rl:parsed $rows)
   (if (== $rows ()) ()
     (case (car-atom $rows)
       (((RelayStored $id $created $json)
         (let $event (json:parse $json) (cons-atom (RlStored $id $created $event $json) (rl:parsed (cdr-atom $rows)))))
        ($other (rl:parsed (cdr-atom $rows)))))))

(= (rl:newer (RlStored $id1 $created1 $e1 $j1) (RlStored $id2 $created2 $e2 $j2))
   (if (> $created1 $created2) true (if (< $created1 $created2) false (< (str:compare $id1 $id2) 0))))
(= (rl:take-n $items $n)
   (if (== $n 0) () (if (== $items ()) () (cons-atom (car-atom $items) (rl:take-n (cdr-atom $items) (- $n 1))))))
(= (rl:drop-n $items $n) (if (== $n 0) $items (if (== $items ()) () (rl:drop-n (cdr-atom $items) (- $n 1)))))
(= (rl:merge $left $right)
   (if (== $left ()) $right
     (if (== $right ()) $left
       (if (rl:newer (car-atom $left) (car-atom $right))
         (cons-atom (car-atom $left) (rl:merge (cdr-atom $left) $right))
         (cons-atom (car-atom $right) (rl:merge $left (cdr-atom $right)))))))
(= (rl:halves $count $half) (if (< $count 2) $half (rl:halves (- $count 2) (+ $half 1))))
(= (rl:sort $items)
   (let $count (size-atom $items)
     (if (< $count 2) $items
       (let $half (rl:halves $count 0)
         (rl:merge (rl:sort (rl:take-n $items $half)) (rl:sort (rl:drop-n $items $half)))))))

(= (rl:selected $stored $filter)
   (if (== $stored ()) ()
     (let* (($item (car-atom $stored)) ($rest (rl:selected (cdr-atom $stored) $filter)))
       (case $item
         (((RlStored $id $created $event $json) (if (rl:accepts $event $filter) (cons-atom $item $rest) $rest)))))))

; The rows of every filter, each event once, as (ID JSON) in answer order, or
; a failure of the store.
(= (rl:answer $me $filters)
   (case (rl:space)
     (((rl:failed $why) (rl:failed $why))
      ($space
       (case (rl:rows $space (RelayCopy $me $id $pk $kind $created $json) (RelayStored $id $created $json))
         (((rl:failed $why2) (rl:failed $why2))
          ($rows (rl:answer-filters (rl:parsed $rows) $filters () ()))))))))
(= (rl:answer-filters $stored $filters $seen $done)
   (if (== $filters ()) (rl:reverse-onto $done ())
     (let* (($filter (car-atom $filters))
            ($chosen (rl:take-n (rl:sort (rl:selected $stored $filter)) (rl:limit-of $filter))))
       (rl:answer-chosen $stored (cdr-atom $filters) $chosen $seen $done))))
(= (rl:answer-chosen $stored $filters $chosen $seen $done)
   (if (== $chosen ()) (rl:answer-filters $stored $filters $seen $done)
     (case (car-atom $chosen)
       (((RlStored $id $created $event $json)
         (if (nostr:member $id $seen)
           (rl:answer-chosen $stored $filters (cdr-atom $chosen) $seen $done)
           (rl:answer-chosen $stored $filters (cdr-atom $chosen) (cons-atom $id $seen) (cons-atom ($id $json) $done))))))))

; ── connections ─────────────────────────────────────────────────────────────
; (RlConn CONN CHALLENGE KEY), KEY () until a sign-in; (RlSub CONN SUB
; FILTERS); outstanding io requests (RlReq ID ROLE CONN).
(= (rl:conn $conn)
   (let $conns (collapse (match &self (RlConn $conn $challenge $key) (RlConn $conn $challenge $key)))
     (if (== $conns ()) () (car-atom $conns))))
(= (rl:open $conn) (not (== (rl:conn $conn) ())))
; The key a connection signed in with, while it is allowlisted.
(= (rl:key-of $conn)
   (case (rl:conn $conn)
     (((RlConn $c $challenge $key) (if (== $key ()) () (if (rl:allowed $key) $key ())))
      ($none ()))))

(= (rl:submit $role $conn $request)
   (case (io:submit $request)
     (((io:pending $id) (let $added (add-atom &self (RlReq $id $role $conn)) (RlPending $id)))
      ($refused (RlRefused $refused)))))
(= (rl:send-text $conn $text)
   (if (rl:open $conn) (let $submitted (rl:submit send $conn (ws:send $conn $text)) ()) ()))
(= (rl:send $conn $message) (rl:send-text $conn (nostr:compact $message)))
(= (rl:notice $conn $text) (rl:send $conn (JsonArray ("NOTICE" $text))))
(= (rl:receive $conn)
   (case (rl:submit receive $conn (ws:receive $conn))
     (((RlPending $id) ())
      ($refused (rl:gone $conn)))))

(= (rl:connected $conn)
   (let* (($challenge (crypto:random-hex 16))
          ($added (add-atom &self (RlConn $conn $challenge ())))
          ($sent (rl:send $conn (JsonArray ("AUTH" $challenge)))))
     (rl:receive $conn)))

(= (rl:forget $conn)
   (let* (($key (case (rl:conn $conn) (((RlConn $c $challenge $k) $k) ($none ()))))
          ($had (rl:subscription-count $conn))
          ($subs (rl:remove-all (collapse (match &self (RlSub $conn $sub $filters) (RlSub $conn $sub $filters)))))
          ($conns (rl:remove-all (collapse (match &self (RlConn $conn $challenge $k2) (RlConn $conn $challenge $k2))))))
     (if (== $had 0) () (rl:tell-subscriptions $key))))
; A connection that ended: its records go, and closing it releases it.
(= (rl:gone $conn)
   (if (rl:open $conn)
     (let* (($forgotten (rl:forget $conn)) ($closing (rl:submit close $conn (ws:close $conn 1000 "")))) ())
     ()))
; A connection this relay ends.
(= (rl:end $conn $reason)
   (let* (($forgotten (rl:forget $conn)) ($closing (rl:submit close $conn (ws:close $conn 1000 $reason)))) ()))

; ── messages ────────────────────────────────────────────────────────────────
(= (rl:on-message $conn $text)
   (case (json:parse $text)
     (((JsonArray $items)
       (if (== $items ()) (rl:notice $conn "invalid: empty message")
         (let* (($verb (car-atom $items)) ($rest (cdr-atom $items)))
           (if (== $verb "AUTH") (rl:auth $conn $rest)
             (if (== $verb "EVENT") (rl:event $conn $rest)
               (if (== $verb "REQ") (rl:req $conn $rest)
                 (if (== $verb "CLOSE") (rl:close-sub $conn $rest)
                   (rl:notice $conn "invalid: unknown message type"))))))))
      ($other (rl:notice $conn "invalid: not a JSON array")))))

(= (rl:event-id $event)
   (let $id (if (rl:is-object $event) (nostr:field $event "id") nostr:missing)
     (if (nostr:is-text $id) $id "")))

(= (rl:recent $created)
   (if (nostr:is-integer $created)
     (let* (($now (nostr:now)) ($window (rl:get auth-window)))
       (if (< $created (- $now $window)) false (<= $created (+ $now $window))))
     false))
(= (rl:names-this-relay $named)
   (let $urls (rl:get urls) (if (== $urls ()) true (rl:intersects $named $urls))))

; NIP-42: a signed AUTH event naming this connection's challenge, recent, naming
; an address of this relay, from an allowlisted key.
(= (rl:auth-ok $conn $event)
   (case (rl:conn $conn)
     (((RlConn $c $challenge $key)
       (if (not (rl:is-object $event)) false
         (if (not (nostr:verify $event)) false
           (if (not (== (nostr:field $event "kind") (nostr:kind-auth))) false
             (if (not (nostr:member $challenge (nostr:tag-values $event "challenge"))) false
               (if (not (rl:recent (nostr:field $event "created_at"))) false
                 (if (not (rl:names-this-relay (nostr:tag-values $event "relay"))) false
                   (rl:allowed (nostr:field $event "pubkey")))))))))
      ($none false))))

(= (rl:auth $conn $args)
   (if (not (== (size-atom $args) 1)) (rl:notice $conn "invalid: AUTH takes one event")
     (let* (($event (car-atom $args)) ($id (rl:event-id $event)))
       (if (rl:auth-ok $conn $event)
         (case (rl:conn $conn)
           (((RlConn $c $challenge $old)
             (let* (($key (nostr:field $event "pubkey"))
                    ($gone (remove-atom &self (RlConn $c $challenge $old)))
                    ($added (add-atom &self (RlConn $c $challenge $key)))
                    ($logged (rl:log (rl:cat ("signed in: " $key)))))
               (rl:send $conn (JsonArray ("OK" $id true "")))))))
         (rl:send $conn (JsonArray ("OK" $id false "restricted: sign-in refused")))))))

(= (rl:ephemeral $kind) (if (>= $kind 20000) (< $kind 30000) false))

; Why a published event is refused, or ().
(= (rl:refusal $me $event)
   (if (== $me ()) "auth-required: sign in"
     (if (not (rl:is-object $event)) "invalid: bad event"
       (if (not (nostr:verify $event)) "invalid: bad event"
         (if (not (== (nostr:field $event "pubkey") $me)) "restricted: only your own events"
           (if (== (nostr:field $event "kind") (nostr:kind-auth)) "invalid: AUTH events are not stored"
             (if (> (nostr:field $event "created_at") (+ (nostr:now) (rl:get reject-future)))
               "invalid: too far in the future"
               ())))))))

(= (rl:event $conn $args)
   (if (not (== (size-atom $args) 1)) (rl:notice $conn "invalid: EVENT takes one event")
     (let* (($event (car-atom $args)) ($id (rl:event-id $event)) ($me (rl:key-of $conn))
            ($reason (rl:refusal $me $event)))
       (if (not (== $reason ())) (rl:send $conn (JsonArray ("OK" $id false $reason)))
         (if (rl:ephemeral (nostr:field $event "kind"))
           (let $relayed (rl:broadcast $event (nostr:compact $event)) (rl:send $conn (JsonArray ("OK" $id true ""))))
           (if (rl:has $id) (rl:send $conn (JsonArray ("OK" $id true "duplicate: already have it")))
             (case (rl:store $event $id $me)
               ((stored (let $answered (rl:send $conn (JsonArray ("OK" $id true "")))
                          (rl:broadcast $event (nostr:compact $event))))
                ($failed (rl:send $conn (JsonArray ("OK" $id false "error: could not store the event"))))))))))))

(= (rl:event-message $sub $json) (rl:cat ("[\"EVENT\"," (nostr:compact $sub) "," $json "]")))

(= (rl:broadcast $event $json)
   (rl:deliver $event $json (collapse (match &self (RlSub $conn $sub $filters) (RlSub $conn $sub $filters)))))
(= (rl:deliver $event $json $subs)
   (if (== $subs ()) ()
     (let $done (case (car-atom $subs)
                  (((RlSub $conn $sub $filters)
                    (let $key (rl:key-of $conn)
                      (if (== $key ()) ()
                        (if (rl:readable $event $key)
                          (if (rl:accepts-any $event $filters) (rl:send-text $conn (rl:event-message $sub $json)) ())
                          ()))))))
       (rl:deliver $event $json (cdr-atom $subs)))))

(= (rl:subscription-count $conn) (size-atom (collapse (match &self (RlSub $conn $sub $filters) $sub))))
; Each change of a key's subscriptions is logged with their number over all
; its connections.
(= (rl:count-subscriptions $conns $count)
   (if (== $conns ()) $count
     (rl:count-subscriptions (cdr-atom $conns) (+ $count (rl:subscription-count (car-atom $conns))))))
(= (rl:tell-subscriptions $key)
   (if (== $key ()) ()
     (let $count (rl:count-subscriptions (collapse (match &self (RlConn $c $challenge $key) $c)) 0)
       (rl:log (rl:cat ("subscriptions of " $key ": " (rl:text $count)))))))
(= (rl:has-sub $conn $sub) (not (== (collapse (match &self (RlSub $conn $sub $filters) $sub)) ())))
(= (rl:drop-sub $conn $sub)
   (rl:remove-all (collapse (match &self (RlSub $conn $sub $filters) (RlSub $conn $sub $filters)))))

(= (rl:valid-sub $sub)
   (if (nostr:is-text $sub) (if (> (str:byte-length $sub) 0) (<= (str:byte-length $sub) 64) false) false))

(= (rl:req $conn $args)
   (if (< (size-atom $args) 2) (rl:notice $conn "invalid: REQ takes a subscription and filters")
     (let* (($sub (car-atom $args)) ($filters (cdr-atom $args)) ($me (rl:key-of $conn)))
       (if (not (rl:valid-sub $sub)) (rl:notice $conn "invalid: subscription id")
         (if (== $me ()) (rl:send $conn (JsonArray ("CLOSED" $sub "auth-required: sign in")))
           (if (not (rl:valid-filters $filters)) (rl:send $conn (JsonArray ("CLOSED" $sub "invalid: filter")))
             (if (> (size-atom $filters) (rl:get max-filters))
               (rl:send $conn (JsonArray ("CLOSED" $sub "error: too many filters")))
               (if (and (not (rl:has-sub $conn $sub)) (>= (rl:subscription-count $conn) (rl:get max-subscriptions)))
                 (rl:send $conn (JsonArray ("CLOSED" $sub "error: too many subscriptions")))
                 (case (rl:answer $me $filters)
                   (((rl:failed $why)
                     (let $logged (rl:log (rl:cat ("cannot answer " $sub ": " $why)))
                       (rl:send $conn (JsonArray ("CLOSED" $sub "error: the store could not answer")))))
                    ($rows
                     (let* (($dropped (rl:drop-sub $conn $sub))
                            ($added (add-atom &self (RlSub $conn $sub $filters)))
                            ($sent (rl:send-rows $conn $sub $rows))
                            ($ended (rl:send $conn (JsonArray ("EOSE" $sub)))))
                       (rl:tell-subscriptions $me)))))))))))))
(= (rl:send-rows $conn $sub $rows)
   (if (== $rows ()) ()
     (let* ((($id $json) (car-atom $rows)) ($sent (rl:send-text $conn (rl:event-message $sub $json))))
       (rl:send-rows $conn $sub (cdr-atom $rows)))))

(= (rl:close-sub $conn $args)
   (if (not (== (size-atom $args) 1)) (rl:notice $conn "invalid: CLOSE takes a subscription")
     (let $dropped (rl:drop-sub $conn (car-atom $args)) (rl:tell-subscriptions (rl:key-of $conn)))))

; ── the loop ────────────────────────────────────────────────────────────────
(= (rl:accept)
   (let $listener (rl:get listener)
     (case (rl:submit accept 0 (ws:accept $listener))
       (((RlPending $id) ())
        ($refused (rl:log (rl:cat ("cannot accept: " (rl:text $refused)))))))))

(= (rl:watch)
   (let* (($path (rl:get allowlist)) ($directory (rl:parent $path)) ($name (rl:base-name $path)))
     (case (rl:submit watch 0 (fs:changed $directory ($name)))
       (((RlPending $id) ())
        ($refused (rl:log (rl:cat ("the allowlist is not watched: " (rl:text $refused)))))))))

(= (rl:completed $role $conn $value)
   (if (== $role accept)
     (case $value
       (((ws:accepted $c $target $peer $fields) (let $handled (rl:connected $c) (rl:accept)))
        ($other (rl:log (rl:cat ("the listener stopped: " (rl:text $other)))))))
     (if (== $role receive)
       (case $value
         (((ws:message $c $text) (let $handled (rl:on-message $conn $text) (if (rl:open $conn) (rl:receive $conn) ())))
          ($lost (rl:gone $conn))))
       (if (== $role send)
         (case $value (((ws:sent $c) ()) ($lost (rl:gone $conn))))
         ; Watching again before reading: a change after this read is seen.
         (if (== $role watch) (let $watching (rl:watch) (rl:reload)) ())))))

(= (rl:dispatch $ids)
   (if (== $ids ()) ()
     (let* (($id (car-atom $ids))
            ($completion (io:wait $id 0))
            ($requests (collapse (match &self (RlReq $id $role $conn) (RlReq $id $role $conn))))
            ($handled (if (== $requests ()) ()
                        (case (car-atom $requests)
                          (((RlReq $rid $role $conn)
                            (let $gone (remove-atom &self (RlReq $rid $role $conn))
                              (case $completion
                                (((io:event $eid $value) (rl:completed $role $conn $value))
                                 ($none ()))))))))))
       (rl:dispatch (cdr-atom $ids)))))

(= (rl:cycle)
   (let* (($ready (io:wait-ready 60000))
          ($handled (case $ready (((io:ready $ids) (rl:dispatch $ids)) ($idle ())))))
     RlCycled))
(= (rl:loop) (let $cycled (once (rl:cycle)) (rl:loop)))

(= (rl:listen)
   (let* (($host (rl:get host)) ($port (rl:get port)) ($bytes (rl:get max-message-bytes))
          ($connections (rl:get max-connections)) ($idle (rl:get idle-ms)))
     (case (io:submit (ws:listen $host $port $bytes $connections $idle))
       (((io:pending $l)
         (case (io:wait $l 10000)
           (((io:event $l (ws:listening $l $bound))
             (let* (($s (rl:set listener $l))
                    ($accepting (rl:accept))
                    ($watching (rl:watch))
                    ($logged (rl:log (rl:cat ("listening on " (rl:text $host) " port " (rl:text $bound)))))
                    ($told (println! (RelayListening $bound))))
               (rl:loop)))
            ($other (rl:failed (rl:cat ("cannot listen: " (rl:text $other))))))))
        ($refused (rl:failed (rl:cat ("cannot listen: " (rl:text $refused)))))))))

(= (rl:start $config)
   (case (rl:configure $config)
     ((RlConfigured
       (case (rl:open-store)
         ((RlStoreOpen
           (case (rl:load-allowlist)
             (((rl:failed $why) (rl:failed $why))
              ((RlAllowed $count)
               (let $logged (rl:log (rl:cat ("allowlist: " (rl:text $count) " keys"))) (rl:listen))))))
          ($error (rl:failed (rl:cat ("cannot open the store: " (rl:text $error))))))))
      ($failure $failure))))

; The engine hands the arguments over as symbols.
(= (rl:options $arguments $config)
   (if (== $arguments ()) (RlOptions $config)
     (let* (($argument (rl:text (car-atom $arguments))) ($rest (cdr-atom $arguments)))
       (if (== $argument "--config")
         (if (== $rest ()) (RlUsage "--config needs a path") (rl:options (cdr-atom $rest) (rl:text (car-atom $rest))))
         (if (str:starts-with $argument "--config=")
           (rl:options $rest (str:byte-slice $argument 9 (str:byte-length $argument)))
           (RlUsage (rl:cat ("unrecognized argument: " $argument))))))))

(= (rl:main)
   (case (rl:options (system:args) (system:getenv-or-default "NOSTR_RELAY_CONFIG" "~/.config/godelclaw-nostr/relay.toml"))
     (((RlOptions $config)
       (case (rl:start $config)
         (((rl:failed $why) (let $logged (rl:log (rl:cat ("relay: " $why))) (system:exit 1)))
          ($other $other))))
      ((RlUsage $why) (let $logged (rl:log (rl:cat ("usage: " $why))) (system:exit 2))))))

!(rl:main)

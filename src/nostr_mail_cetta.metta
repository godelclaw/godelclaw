; Nostr agent mail on CeTTa, function for function the native nostr_mail.py:
; events (NIP-01) signed with BIP-340, kind-3838 letters sealed with NIP-44
; v2, kind-7 receipts and the member registry.  Keys, ids and signatures are
; lowercase hex; an event is the JSON value a relay carries.  A value Python
; answers with None is () here.
!(import! &self (library crypto))
!(import! &self (library fs))
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library system))

(= (nostr:kind-mail) 3838)
(= (nostr:kind-receipt) 7)
(= (nostr:kind-auth) 22242)

; A field of a JSON object as Python's json.loads keeps it: the last of
; duplicate keys, or nostr:missing.
(= (nostr:field $object $key)
   (case (json:get-last $object $key)
     (((JsonFoundV1 $index $value) $value)
      ($absent nostr:missing))))

(= (nostr:is-text $value) (== (get-type $value) String))

(= (nostr:is-hex $value $length)
   (if (nostr:is-text $value)
       (if (== (str:byte-length $value) $length)
           (if (str:is-all-in xdigit $value) (== $value (str:lower $value)) false)
           false)
       false))

; An integer, not a float with an integral value.
(= (nostr:is-integer $value)
   (if (== (get-type $value) Number)
       (== (str:number->string $value) (str:number->string (floor-math $value)))
       false))

(= (nostr:all-text $items)
   (if (== $items ()) true
     (if (nostr:is-text (car-atom $items)) (nostr:all-text (cdr-atom $items)) false)))

(= (nostr:tag-lists $tags)
   (if (== $tags ()) true
     (case (car-atom $tags)
       (((JsonArray $items) (if (nostr:all-text $items) (nostr:tag-lists (cdr-atom $tags)) false))
        ($other false)))))

(= (nostr:items $array)
   (case $array
     (((JsonArray $items) $items)
      ($other ()))))

(= (nostr:member $value $items)
   (if (== $items ()) false
     (if (== (car-atom $items) $value) true (nostr:member $value (cdr-atom $items)))))

; ── events (NIP-01) ─────────────────────────────────────────────────────────
; SHA-256 of [0, pubkey, created_at, kind, tags, content], written as
; JSON.stringify and json.dumps(separators=(",", ":"), ensure_ascii=False)
; write it.
(= (nostr:serialization $pubkey $created $kind $tags $content)
   (json:stringify-python-with (JsonArray (0 $pubkey $created $kind $tags $content))
     ((ensure-ascii False) (separators "," ":"))))

(= (nostr:event-id $pubkey $created $kind $tags $content)
   (str:sha256 (nostr:serialization $pubkey $created $kind $tags $content)))

; Whole seconds, an integer in every dialect: floor-math answers a float
; for a float under HE and Prime.
(= (nostr:now)
   (str:string->number (car-atom (str:split "." (str:number->string (floor-math (system:wall-time)))))))

(= (nostr:public-key $secret) (crypto:xonly-public-key $secret))

; An event signed over its identifier with 32 bytes of auxiliary randomness,
; its fields in the order nostr_mail.sign writes them.
(= (nostr:sign-with $secret $kind $tags $content $created $aux)
   (let* (($pubkey (crypto:xonly-public-key $secret))
          ($id (nostr:event-id $pubkey $created $kind $tags $content)))
     (JsonObject ((JsonPair "pubkey" $pubkey) (JsonPair "created_at" $created) (JsonPair "kind" $kind)
                  (JsonPair "tags" $tags) (JsonPair "content" $content) (JsonPair "id" $id)
                  (JsonPair "sig" (crypto:schnorr-sign $secret $id $aux))))))

(= (nostr:sign $secret $kind $tags $content $created)
   (nostr:sign-with $secret $kind $tags $content
     (if (== $created ()) (nostr:now) $created) (crypto:random-hex 32)))

; The event is well formed, its identifier hashes its fields, and its key
; signed it.
(= (nostr:verify $event)
   (let* (($id (nostr:field $event "id")) ($pubkey (nostr:field $event "pubkey"))
          ($sig (nostr:field $event "sig")) ($created (nostr:field $event "created_at"))
          ($kind (nostr:field $event "kind")) ($tags (nostr:field $event "tags"))
          ($content (nostr:field $event "content")))
     (if (nostr:is-hex $id 64)
       (if (nostr:is-hex $pubkey 64)
         (if (nostr:is-hex $sig 128)
           (if (nostr:is-integer $created)
             (if (nostr:is-integer $kind)
               (if (nostr:is-text $content)
                 (if (case $tags (((JsonArray $items) (nostr:tag-lists $items)) ($other false)))
                   (if (== (nostr:event-id $pubkey $created $kind $tags $content) $id)
                       (crypto:schnorr-verify $pubkey $id $sig)
                       false)
                   false)
                 false)
               false)
             false)
           false)
         false)
       false)))

(= (nostr:tag-value $name $items)
   (if (== $items ()) ()
     (if (== (car-atom $items) $name)
         (let $rest (cdr-atom $items) (if (== $rest ()) () (car-atom $rest)))
         ())))

(= (nostr:tag-values-in $tags $name)
   (if (== $tags ()) ()
     (let* (($value (nostr:tag-value $name (nostr:items (car-atom $tags))))
            ($rest (nostr:tag-values-in (cdr-atom $tags) $name)))
       (if (== $value ()) $rest (cons-atom $value $rest)))))

; The second element of each tag with this name, in order.
(= (nostr:tag-values $event $name)
   (nostr:tag-values-in (nostr:items (nostr:field $event "tags")) $name))

; ── mail ────────────────────────────────────────────────────────────────────
(= (nostr:letter-text $subject $body $type $task)
   (json:stringify-python-with
     (JsonObject (nostr:letter-pairs $subject $body $type $task))
     ((ensure-ascii False) (separators "," ":"))))

; Python omits a type, task or reply that is None or empty.
(= (nostr:given $value) (if (== $value ()) false (if (== $value "") false true)))

(= (nostr:letter-pairs $subject $body $type $task)
   (let* (($tail (if (nostr:given $task) ((JsonPair "task" $task)) ()))
          ($middle (if (nostr:given $type) (cons-atom (JsonPair "type" $type) $tail) $tail)))
     (cons-atom (JsonPair "subject" $subject) (cons-atom (JsonPair "body" $body) $middle))))

; A signed kind-3838 message to one recipient, its letter encrypted for them.
(= (nostr:seal-with $secret $recipient $subject $body $type $task $reply-to $ack $created $nonce $aux)
   (let* (($content (crypto:nip44-encrypt (crypto:nip44-conversation-key $secret $recipient)
                      (nostr:letter-text $subject $body $type $task) $nonce))
          ($acked (if $ack ((JsonArray ("ack" "request"))) ()))
          ($replied (if (nostr:given $reply-to) (cons-atom (JsonArray ("e" $reply-to "" "reply")) $acked) $acked))
          ($tags (JsonArray (cons-atom (JsonArray ("p" $recipient))
                              (cons-atom (JsonArray ("encrypted" "nip44")) $replied)))))
     (nostr:sign-with $secret 3838 $tags $content $created $aux)))

(= (nostr:seal $secret $recipient $subject $body $type $task $reply-to $ack)
   (nostr:seal-with $secret $recipient $subject $body $type $task $reply-to $ack
     (nostr:now) (crypto:random-hex 32) (crypto:random-hex 32)))

; The letter of a message for the holder of this key, or ().  The holder is
; its recipient, or its author reading its own sent copy.  Only the p tag and
; the encrypted content are read: a type or task comes from inside the
; ciphertext, never from a tag.
(= (nostr:open-letter $secret $event)
   (if (== (nostr:field $event "kind") 3838)
     (if (nostr:member (JsonArray ("encrypted" "nip44")) (nostr:items (nostr:field $event "tags")))
       (let* (($me (crypto:xonly-public-key $secret))
              ($recipients (nostr:tag-values $event "p"))
              ($author (nostr:field $event "pubkey"))
              ($peer (if (== $author $me)
                         (if (== $recipients ()) () (car-atom $recipients))
                         (if (nostr:member $me $recipients) $author ()))))
         (if (== $peer ()) ()
           (nostr:letter-of (nostr:decrypt-from $secret $peer (nostr:field $event "content")))))
       ())
     ()))

(= (nostr:decrypt-from $secret $peer $content)
   (if (nostr:is-text $content)
     (case (crypto:nip44-conversation-key $secret $peer)
       (((crypto:failed $kind $detail) ())
        ($key (case (crypto:nip44-decrypt $key $content)
                (((crypto:failed $kind2 $detail2) ())
                 ($plaintext $plaintext))))))
     ()))

(= (nostr:text-field $object $key)
   (let $value (nostr:field $object $key) (if (nostr:is-text $value) $value ())))

(= (nostr:letter-of $plaintext)
   (if (== $plaintext ()) ()
     (case (json:parse $plaintext)
       (((JsonObject $pairs)
         (let* (($object (JsonObject $pairs))
                ($subject (nostr:text-field $object "subject"))
                ($body (nostr:text-field $object "body")))
           (if (== $subject ()) ()
             (if (== $body ()) ()
               (nostr:letter $subject $body (nostr:text-field $object "type")
                 (nostr:text-field $object "task"))))))
        ($other ())))))

; A signed receipt for a message: delivered, or read; () for another state.
(= (nostr:receipt-with $secret $message $state $created $aux)
   (if (nostr:member $state ("delivered" "read"))
   (nostr:sign-with $secret 7
     (JsonArray ((JsonArray ("e" (nostr:field $message "id"))) (JsonArray ("p" (nostr:field $message "pubkey")))
                 (JsonArray ("k" "3838")) (JsonArray ("ack" $state))))
     "+" $created $aux)
   ()))

(= (nostr:receipt $secret $message $state)
   (nostr:receipt-with $secret $message $state (nostr:now) (crypto:random-hex 32)))

; (nostr:receipted MESSAGE-ID STATE) of a well-formed receipt, or ().
(= (nostr:receipt-fact $event)
   (let* (($messages (nostr:tag-values $event "e")) ($states (nostr:tag-values $event "ack")))
     (if (== (nostr:field $event "kind") 7)
       (if (== $messages ()) ()
         (if (== $states ()) ()
           (if (nostr:member (car-atom $states) ("delivered" "read"))
               (nostr:receipted (car-atom $messages) (car-atom $states))
               ())))
       ())))

; Member names by key, from a registry file {name: {"pubkey_hex": ...}}: a
; list of (nostr:member-name KEY NAME), a key named twice keeping its first
; name.
(= (nostr:load-registry $path)
   (case (json:parse (fs:read-text $path))
     (((JsonObject $pairs) (nostr:registry-entries $pairs ()))
      ($other ()))))

(= (nostr:registry-entries $pairs $seen)
   (if (== $pairs ()) ()
     (case (car-atom $pairs)
       (((JsonPair $name $entry)
         (let $key (case $entry (((JsonObject $fields) (nostr:field $entry "pubkey_hex")) ($other ())))
           (if (nostr:is-hex $key 64)
             (if (nostr:member $key $seen)
                 (nostr:registry-entries (cdr-atom $pairs) $seen)
                 (cons-atom (nostr:member-name $key $name)
                   (nostr:registry-entries (cdr-atom $pairs) (cons-atom $key $seen))))
             (nostr:registry-entries (cdr-atom $pairs) $seen))))
        ($other (nostr:registry-entries (cdr-atom $pairs) $seen))))))

; ── relay client (NIP-01, NIP-42) over the io runtime's WebSockets ─────────
; One connection, signed in with one key before any request, used by one
; request at a time: (nostr:relay CONN URL SECRET).  Failures are
; (nostr:relay-failed WHAT DETAIL).

(= (nostr:compact $value)
   (json:stringify-python-with $value ((ensure-ascii False) (separators "," ":"))))

(= (nostr:relay-send $conn $message $timeout)
   (case (io:submit (ws:send $conn (nostr:compact (JsonArray $message))))
     (((io:pending $s)
       (case (io:wait $s $timeout)
         (((io:event $s (ws:sent $conn)) sent)
          ($other (nostr:relay-failed lost $other)))))
      ($refused (nostr:relay-failed lost $refused)))))

; The items of the next message that is a JSON array, nostr:timeout, or a
; failure once the connection is lost.
(= (nostr:relay-next $conn $timeout)
   (case (io:submit (ws:receive $conn))
     (((io:pending $r)
       (case (io:wait $r $timeout)
         (((io:event $r (ws:message $c $text))
           (case (json:parse $text)
             (((JsonArray $items) (if (== $items ()) (nostr:relay-next $conn $timeout) $items))
              ($other (nostr:relay-next $conn $timeout)))))
          ((io:event $r $other) (nostr:relay-failed lost $other))
          ($idle (let $cancelled (io:cancel $r) nostr:timeout)))))
      ($refused (nostr:relay-failed lost $refused)))))

(= (nostr:relay-open $url $secret $timeout)
   (case (io:submit (ws:connect $url $timeout 4194304))
     (((io:pending $conn)
       (case (io:wait $conn $timeout)
         (((io:event $conn (ws:connected $conn)) (nostr:sign-in (nostr:relay $conn $url $secret) $timeout))
          ($other (nostr:relay-failed connect $other)))))
      ($refused (nostr:relay-failed connect $refused)))))

(= (nostr:relay-close (nostr:relay $conn $url $secret))
   (case (io:submit (ws:close $conn 1000 ""))
     (((io:pending $x) (let $closed (io:wait $x 5000) closed))
      ($refused closed))))

; NIP-42: answer the relay's challenge with a kind-22242 event naming the
; relay and the challenge, and wait for the relay to accept it.
(= (nostr:sign-in $relay $timeout)
   (let (nostr:relay $conn $url $secret) $relay
     (case (nostr:await-challenge $conn $timeout)
       (((nostr:challenge $challenge)
         (let $auth (nostr:sign $secret 22242
                      (JsonArray ((JsonArray ("relay" $url)) (JsonArray ("challenge" $challenge)))) "" ())
           (case (nostr:relay-send $conn ("AUTH" $auth) $timeout)
             ((sent (case (nostr:await-ok $conn (nostr:field $auth "id") $timeout)
                      (((nostr:ok true $reason) $relay)
                       ((nostr:ok false $reason) (nostr:relay-failed refused $reason))
                       ($failure $failure))))
              ($failure $failure)))))
        ($failure $failure)))))

(= (nostr:await-challenge $conn $timeout)
   (let $message (nostr:relay-next $conn $timeout)
     (case $message
       ((nostr:timeout (nostr:relay-failed timeout "no sign-in challenge"))
        ((nostr:relay-failed $what $detail) $message)
        (("AUTH" $challenge) (nostr:challenge $challenge))
        ($other (nostr:await-challenge $conn $timeout))))))

; (nostr:ok ACCEPTED REASON) of the OK answer naming this event.
(= (nostr:await-ok $conn $id $timeout)
   (let $message (nostr:relay-next $conn $timeout)
     (case $message
       ((nostr:timeout (nostr:relay-failed timeout "no answer to an event"))
        ((nostr:relay-failed $what $detail) $message)
        (("OK" $id $accepted) (nostr:ok (== $accepted true) ""))
        (("OK" $id $accepted $reason) (nostr:ok (== $accepted true) $reason))
        ($other (nostr:await-ok $conn $id $timeout))))))

; (nostr:ok ACCEPTED REASON) as the relay answered a published event.
(= (nostr:publish $relay $event $timeout)
   (let (nostr:relay $conn $url $secret) $relay
     (case (nostr:relay-send $conn ("EVENT" $event) $timeout)
       ((sent (nostr:await-ok $conn (nostr:field $event "id") $timeout))
        ($failure $failure)))))

; The stored events matching any of the filters, up to the relay's
; end-of-stored marker, in the order the relay sent them.
(= (nostr:query $relay $filters $timeout)
   (let* (((nostr:relay $conn $url $secret) $relay)
          ($sub (crypto:random-hex 8)))
     (case (nostr:relay-send $conn (cons-atom "REQ" (cons-atom $sub $filters)) $timeout)
       ((sent (let $events (nostr:collect $conn $sub $timeout)
                (let $closed (nostr:relay-send $conn ("CLOSE" $sub) $timeout) $events)))
        ($failure $failure)))))

(= (nostr:collect $conn $sub $timeout)
   (let $message (nostr:relay-next $conn $timeout)
     (case $message
       ((nostr:timeout (nostr:relay-failed timeout "no end of stored events"))
        ((nostr:relay-failed $what $detail) $message)
        (("EVENT" $sub $event)
         (let $rest (nostr:collect $conn $sub $timeout)
           (case $rest (((nostr:relay-failed $what $detail) $rest) ($events (cons-atom $event $events))))))
        (("EOSE" $sub) ())
        (("CLOSED" $sub $reason) (nostr:relay-failed closed $reason))
        ($other (nostr:collect $conn $sub $timeout))))))

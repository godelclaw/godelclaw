; Bring Nostr agent mail into claw-mail, and send claw-mail out over Nostr:
; scripts/nostr_gateway.py natively on CeTTa, decision for decision.
;
; Inbound: for each local agent's key, the gateway reads the mail addressed to
; it on the relay. A message that is well formed, signed by a registered key
; and opens for the agent's key is sent into the agent's claw-mail inbox as mail
; headers and a body, from `nostr.<registry name>`, with the event identifier as
; the send identity: a message read again is a duplicate send, never a second
; delivery. When the author asked for receipts, the agent's key publishes a
; delivered receipt.
;
; Outbound: a local agent sends to the gateway's inbox (`nostr` of its account)
; a header block (To, Subject, In-Reply-To, Type, Task), a blank line and the
; body. The claw-mail sender selects the signing key by the [senders] table; the
; signed event is saved before it is published, and the sender is told the
; outcome at its [replies] address. A receipt for sent mail is believed only
; from the key the mail was addressed to.
;
; The gateway calls claw-mail's own MeTTa layer (shared_mail.metta), not its
; command line, and serves every key's relay connection and the outbox from one
; io:wait-ready loop: each connection is a state machine over WebSocket
; requests, and the outbox is a mailbox watch request, so one slow channel never
; delays another and nothing polls. Within a key, mail is delivered in the order
; it was written. The state file is the Python gateway's, field for field, so
; either gateway can take over from the other.
;
;   cetta --lang petta --quiet scripts/nostr_gateway.metta [--once] [--config PATH]
;
; The configuration is the Python gateway's gateway.toml; `mailer` is not read.
; The mail socket is CETTA_SHARED_MAIL_SOCKET, else `socket` in gateway.toml,
; else the socket of claw-mail's mail.toml.
!(import! &self (library system))
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library io))
!(import! &self (library toml))
!(import! &self (library crypto))
!(import! &self "../src/nostr_mail_cetta.metta")
!(import! &self "shared_mail.metta")

; ── text ────────────────────────────────────────────────────────────────────
(= (gw:text $value) (if (== (get-type $value) String) $value (repr $value)))
(= (gw:cat $parts) (str:join "" $parts))
(= (gw:log $line)
   (let $stamp (system:format-time "%Y-%m-%d %H:%M:%S " (system:wall-time) local)
     (system:write stderr (gw:cat ($stamp $line "\n")))))
; Monotonic milliseconds, an integer in every dialect: the nanosecond count
; less its last six digits (floor-math and / give floats under HE and Prime).
(= (gw:now)
   (let $digits (str:number->string (system:monotonic-ns))
     (str:string->number (str:byte-slice $digits 0 (- (str:byte-length $digits) 6)))))
(= (gw:quoted $text) (gw:cat ("'" (gw:text $text) "'")))

(= (gw:reverse $items) (gw:reverse-onto $items ()))
(= (gw:reverse-onto $items $done)
   (if (== $items ()) $done (gw:reverse-onto (cdr-atom $items) (cons-atom (car-atom $items) $done))))
(= (gw:append $front $back) (gw:reverse-onto (gw:reverse $front) $back))
(= (gw:last $items $default)
   (if (== $items ()) $default (gw:last (cdr-atom $items) (car-atom $items))))
(= (gw:but-last $items) (gw:reverse (cdr-atom (gw:reverse $items))))

; The characters Python's str.isspace() accepts, which str.strip() removes.
(= (gw:python-space $c)
   (if (<= $c 32) (or (and (>= $c 9) (<= $c 13)) (>= $c 28))
     (if (< $c 5760) (or (== $c 133) (== $c 160))
       (or (== $c 5760) (or (and (>= $c 8192) (<= $c 8202))
         (or (== $c 8232) (or (== $c 8233) (or (== $c 8239) (or (== $c 8287) (== $c 12288))))))))))
(= (gw:drop-while-space $cs)
   (if (== $cs ()) ()
     (if (gw:python-space (car-atom $cs)) (gw:drop-while-space (cdr-atom $cs)) $cs)))
(= (gw:strip-codepoints $cs) (gw:reverse (gw:drop-while-space (gw:reverse (gw:drop-while-space $cs)))))
(= (gw:strip $text) (str:from-python-codepoints (gw:strip-codepoints (str:python-codepoints $text))))

; str.strip("<>").
(= (gw:drop-while-angle $cs)
   (if (== $cs ()) ()
     (let $c (car-atom $cs) (if (or (== $c 60) (== $c 62)) (gw:drop-while-angle (cdr-atom $cs)) $cs))))
(= (gw:strip-angles $text)
   (str:from-python-codepoints
     (gw:reverse (gw:drop-while-angle (gw:reverse (gw:drop-while-angle (str:python-codepoints $text)))))))

(= (gw:remove-suffix $text $suffix)
   (if (str:ends-with $text $suffix)
     (str:byte-slice $text 0 (- (str:byte-length $text) (str:byte-length $suffix)))
     $text))

; str.lower() of a header name. Only ASCII letters and the Kelvin sign, which
; lowercases to "k", can lower into a known header; other letters keep their
; case, which shows only in the refusal's list of unknown headers.
(= (gw:lower $text) (str:join "k" (str:split "K" (str:lower $text))))

; One header line: control characters (Unicode category C), line breaks
; included, become spaces, then the value is stripped as Python strips it.
(= (gw:uncontrol $cs $done)
   (if (== $cs ()) (gw:reverse $done)
     (let* (($c (car-atom $cs))
            ($category (str:unicode-category (str:from-python-codepoints ($c)))))
       (gw:uncontrol (cdr-atom $cs) (cons-atom (if (str:starts-with $category "C") 32 $c) $done)))))
(= (gw:header-value $value)
   (str:from-python-codepoints (gw:strip-codepoints (gw:uncontrol (str:python-codepoints $value) ()))))

; str.splitlines(): \n, \r, \r\n, \v, \f, \x1c, \x1d, \x1e, \x85,   and
;   end a line; a last line ends the text unless it is empty.
(= (gw:line-break $c)
   (or (and (>= $c 10) (<= $c 13))
     (or (and (>= $c 28) (<= $c 30)) (or (== $c 133) (or (== $c 8232) (== $c 8233))))))
(= (gw:after-break $c $rest)
   (if (== $c 13)
     (if (== $rest ()) $rest (if (== (car-atom $rest) 10) (cdr-atom $rest) $rest))
     $rest))
(= (gw:split-lines $cs $line $lines)
   (if (== $cs ())
     (gw:reverse (if (== $line ()) $lines (cons-atom (str:from-python-codepoints (gw:reverse $line)) $lines)))
     (let* (($c (car-atom $cs)) ($rest (cdr-atom $cs)))
       (if (gw:line-break $c)
         (gw:split-lines (gw:after-break $c $rest) ()
           (cons-atom (str:from-python-codepoints (gw:reverse $line)) $lines))
         (gw:split-lines $rest (cons-atom $c $line) $lines)))))
(= (gw:splitlines $text) (gw:split-lines (str:python-codepoints $text) () ()))

; A claw-mail name: [A-Za-z0-9_.-]+.
(= (gw:agent-char $c)
   (or (and (>= $c 48) (<= $c 57))
     (or (and (>= $c 65) (<= $c 90))
       (or (and (>= $c 97) (<= $c 122)) (or (== $c 95) (or (== $c 46) (== $c 45)))))))
(= (gw:agent-chars $cs)
   (if (== $cs ()) true (if (gw:agent-char (car-atom $cs)) (gw:agent-chars (cdr-atom $cs)) false)))
(= (gw:agent-name $text)
   (if (nostr:is-text $text) (if (== $text "") false (gw:agent-chars (str:python-codepoints $text))) false))

(= (gw:is-object $value) (case $value (((JsonObject $pairs) true) ($other false))))
(= (gw:pairs $value) (case $value (((JsonObject $pairs) $pairs) ($other ()))))
(= (gw:max $a $b) (if (> $a $b) $a $b))
(= (gw:min $a $b) (if (< $a $b) $a $b))
(= (gw:find $text $needle) (case (str:find $text $needle) (((TextNotFound $n) -1) ($at $at))))

; ── ordering ────────────────────────────────────────────────────────────────
; Stable merge sort of (GwKeyed NUMBER TEXT ITEM) by NUMBER, then TEXT in
; code point order.
(= (gw:before (GwKeyed $n1 $t1 $i1) (GwKeyed $n2 $t2 $i2))
   (if (< $n1 $n2) true (if (> $n1 $n2) false (<= (str:compare $t1 $t2) 0))))
(= (gw:take-n $items $n $done)
   (if (== $n 0) (gw:reverse $done) (gw:take-n (cdr-atom $items) (- $n 1) (cons-atom (car-atom $items) $done))))
(= (gw:drop-n $items $n) (if (== $n 0) $items (gw:drop-n (cdr-atom $items) (- $n 1))))
(= (gw:merge $left $right $done)
   (if (== $left ()) (gw:reverse-onto $done $right)
     (if (== $right ()) (gw:reverse-onto $done $left)
       (if (gw:before (car-atom $left) (car-atom $right))
         (gw:merge (cdr-atom $left) $right (cons-atom (car-atom $left) $done))
         (gw:merge $left (cdr-atom $right) (cons-atom (car-atom $right) $done))))))
(= (gw:half $n $count) (if (< $n 2) $count (gw:half (- $n 2) (+ $count 1))))
(= (gw:sort-keyed $items)
   (let $n (size-atom $items)
     (if (< $n 2) $items
       (let $half (gw:half $n 0)
         (gw:merge (gw:sort-keyed (gw:take-n $items $half ())) (gw:sort-keyed (gw:drop-n $items $half)) ())))))
(= (gw:unkey $items $done)
   (if (== $items ()) (gw:reverse $done)
     (case (car-atom $items) (((GwKeyed $n $t $item) (gw:unkey (cdr-atom $items) (cons-atom $item $done)))))))

; Stored events oldest first: by created_at, then id. A created_at that is
; not a number sorts as 0.
(= (gw:event-key $event)
   (let* (($created (nostr:field $event "created_at")) ($id (nostr:field $event "id")))
     (GwKeyed (if (== (get-type $created) Number) $created 0) (if (nostr:is-text $id) $id "") $event)))
(= (gw:keyed-events $events $done)
   (if (== $events ()) (gw:reverse $done)
     (gw:keyed-events (cdr-atom $events) (cons-atom (gw:event-key (car-atom $events)) $done))))
(= (gw:sort-events $events) (gw:unkey (gw:sort-keyed (gw:keyed-events $events ())) ()))
(= (gw:keyed-texts $texts $done)
   (if (== $texts ()) (gw:reverse $done)
     (gw:keyed-texts (cdr-atom $texts) (cons-atom (GwKeyed 0 (car-atom $texts) (car-atom $texts)) $done))))
(= (gw:sort-texts $texts) (gw:unkey (gw:sort-keyed (gw:keyed-texts $texts ())) ()))

; ── facts ───────────────────────────────────────────────────────────────────
(= (gw:remove-all $atoms)
   (if (== $atoms ()) ()
     (let* (($atom (car-atom $atoms))
            ($removed (if (== $atom GwKeep) () (remove-atom &self $atom))))
       (gw:remove-all (cdr-atom $atoms)))))
(= (gw:get $name)
   (let $values (collapse (match &self (GwSetting $name $value) $value))
     (if (== $values ()) () (car-atom $values))))
(= (gw:set $name $value)
   (let $gone (gw:remove-all (collapse (match &self (GwSetting $name $old) (GwSetting $name $old))))
     (add-atom &self (GwSetting $name $value))))
(= (gw:once) (== (gw:get mode) once))
(= (gw:secret $key)
   (let $secrets (collapse (match &self (GwKey $key $secret) $secret))
     (if (== $secrets ()) () (car-atom $secrets))))
(= (gw:recipient-of $key)
   (let $recipients (collapse (match &self (GwInbound $key $recipient) $recipient))
     (if (== $recipients ()) () (car-atom $recipients))))
(= (gw:name-of $pubkey)
   (let $names (collapse (match &self (GwName $pubkey $name) $name))
     (if (== $names ()) () (car-atom $names))))
; By registry name, the last key of that name, as a reversed dict keeps it.
(= (gw:key-of-name $name) (gw:last (collapse (match &self (GwName $key $name) $key)) ()))
(= (gw:sender-key $sender)
   (let $exact (collapse (match &self (GwSender $sender $key) $key))
     (if (not (== $exact ())) (car-atom $exact)
       (let $parts (str:split "/" $sender)
         (if (== (size-atom $parts) 1) ()
           (let* (($pattern (gw:cat ((car-atom $parts) "/*")))
                  ($keys (collapse (match &self (GwSender $pattern $wild) $wild))))
             (if (== $keys ()) () (car-atom $keys))))))))
(= (gw:reply-to $sender)
   (let $addresses (collapse (match &self (GwReply $sender $address) $address))
     (if (== $addresses ()) $sender (car-atom $addresses))))

; The state, field by field as the Python gateway keeps it.
(= (gw:since-of $key)
   (let $stamps (collapse (match &self (GwSince $key $stamp) $stamp))
     (if (== $stamps ()) 0 (car-atom $stamps))))
(= (gw:set-since $key $stamp)
   (let $gone (gw:remove-all (collapse (match &self (GwSince $key $old) (GwSince $key $old))))
     (add-atom &self (GwSince $key $stamp))))
(= (gw:recent-has $id) (not (== (collapse (match &self (GwRecent $id $stamp) $stamp)) ())))
(= (gw:set-recent $id $stamp)
   (let $gone (gw:remove-all (collapse (match &self (GwRecent $id $old) (GwRecent $id $old))))
     (add-atom &self (GwRecent $id $stamp))))
(= (gw:receipted-has $id) (not (== (collapse (match &self (GwReceipted $id $stamp) $stamp)) ())))
(= (gw:set-receipted $id $stamp)
   (let $gone (gw:remove-all (collapse (match &self (GwReceipted $id $old) (GwReceipted $id $old))))
     (add-atom &self (GwReceipted $id $stamp))))
(= (gw:outbound-of $ident)
   (let $pending (collapse (match &self (GwOutbound $ident $value) $value))
     (if (== $pending ()) () (car-atom $pending))))
(= (gw:set-outbound $ident $value)
   (let $gone (gw:drop-outbound $ident) (add-atom &self (GwOutbound $ident $value))))
(= (gw:drop-outbound $ident)
   (gw:remove-all (collapse (match &self (GwOutbound $ident $old) (GwOutbound $ident $old)))))
(= (gw:sent-record $id)
   (let $records (collapse (match &self (GwSent $id $record) $record))
     (if (== $records ()) () (car-atom $records))))
(= (gw:set-sent $id $record)
   (let $gone (gw:remove-all (collapse (match &self (GwSent $id $old) (GwSent $id $old))))
     (add-atom &self (GwSent $id $record))))

; ── paths ───────────────────────────────────────────────────────────────────
; Path.expanduser for "~" and "~/...".
(= (gw:expand $path)
   (let $home (system:getenv-or-default "HOME" "")
     (if (== $path "~") $home
       (if (str:starts-with $path "~/") (gw:cat ($home (str:byte-slice $path 1 (str:byte-length $path)))) $path))))
(= (gw:parent $path)
   (let $parts (str:split "/" $path)
     (if (== (size-atom $parts) 1) "."
       (let $joined (str:join "/" (gw:but-last $parts)) (if (== $joined "") "/" $joined)))))
; Relative to the configuration's directory, as the Python gateway reads it.
(= (gw:relative $directory $value)
   (let $path (gw:expand $value)
     (if (str:starts-with $path "/") $path (gw:cat ($directory "/" $path)))))
; Path.with_suffix(".tmp").
(= (gw:with-tmp $path)
   (let* (($name (gw:last (str:split "/" $path) ""))
          ($prefix (str:byte-slice $path 0 (- (str:byte-length $path) (str:byte-length $name))))
          ($pieces (str:split "." $name))
          ($stem (str:join "." (gw:but-last $pieces))))
     (if (and (> (size-atom $pieces) 1) (and (not (== $stem "")) (not (== (gw:last $pieces "") ""))))
       (gw:cat ($prefix $stem ".tmp"))
       (gw:cat ($path ".tmp")))))

(= (gw:read-file $path)
   (case (fs:read-text-result $path 0 "raw")
     (((ok $text) $text)
      (("ok" $text2) $text2)
      ($refused (gw:failed os (gw:cat ($path ": " (gw:text $refused))))))))

; ── configuration ───────────────────────────────────────────────────────────
(= (gw:field-or $object $key $default)
   (let $value (nostr:field $object $key) (if (== $value nostr:missing) $default $value)))

(= (gw:load-config $config-path)
   (let* (($path (gw:expand $config-path)) ($text (gw:read-file $path)))
     (case $text
       (((gw:failed $kind $detail) $text)
        ($read (case (toml:parse $read)
                 (((JsonObject $pairs) (gw:configure (JsonObject $pairs) (gw:parent $path)))
                  ($bad (gw:failed value (gw:cat ($path ": " (gw:text $bad))))))))))))

(= (gw:configure $config $directory)
   (let* (($relay (gw:field-or $config "relay" ()))
          ($registry (gw:field-or $config "registry" ()))
          ($keys (gw:field-or $config "keys" ())))
     (if (not (nostr:is-text $relay)) (gw:failed value "the configuration names no relay")
       (if (not (nostr:is-text $registry)) (gw:failed value "the configuration names no registry")
         (if (not (nostr:is-text $keys)) (gw:failed value "the configuration names no keys file")
           (let $registry-path (gw:relative $directory $registry)
             (case (gw:read-file $registry-path)
               (((gw:failed $kind $detail) (gw:failed $kind $detail))
                ($text
                 (let* (($names (gw:add-names (nostr:load-registry $registry-path)))
                        ($loaded (gw:load-keys (gw:relative $directory $keys))))
                   (case $loaded
                     (((gw:failed $kind2 $detail2) $loaded)
                      ($ok (gw:configure-rest $config $directory $relay))))))))))))))

(= (gw:configure-rest $config $directory $relay)
   (let* (($s1 (gw:set relay $relay))
          ($s2 (gw:set state (gw:relative $directory
                 (gw:field-or $config "state" "~/.local/state/godelclaw-nostr/gateway.json"))))
          ($s3 (gw:set space (gw:field-or $config "space" "agent-mail")))
          ($s4 (gw:set outbox (gw:field-or $config "outbox" "nostr")))
          ($s5 (gw:set overlap (gw:field-or $config "overlap" 600)))
          ($s6 (gw:set receipt-days (gw:field-or $config "receipt_days" 30)))
          ($s7 (gw:set socket (gw:mail-socket $config $directory)))
          ($s8 (gw:add-table (gw:pairs (gw:field-or $config "inbound" (JsonObject ()))) inbound))
          ($s9 (gw:add-table (gw:pairs (gw:field-or $config "senders" (JsonObject ()))) senders))
          ($s10 (gw:add-table (gw:pairs (gw:field-or $config "replies" (JsonObject ()))) replies)))
     (gw:check-config)))

(= (gw:add-table $pairs $table)
   (if (== $pairs ()) ()
     (let* (((JsonPair $key $value) (car-atom $pairs))
            ($added (case $table
                      ((inbound (add-atom &self (GwInbound $key $value)))
                       (senders (add-atom &self (GwSender $key $value)))
                       (replies (add-atom &self (GwReply $key $value)))))))
       (gw:add-table (cdr-atom $pairs) $table))))

(= (gw:add-names $members)
   (if (== $members ()) ()
     (case (car-atom $members)
       (((nostr:member-name $key $name)
         (let $added (add-atom &self (GwName $key $name)) (gw:add-names (cdr-atom $members))))
        ($other (gw:add-names (cdr-atom $members)))))))

; CETTA_SHARED_MAIL_SOCKET, else `socket` here, else claw-mail's mail.toml.
(= (gw:mail-socket $config $directory)
   (let $environment (system:getenv-or-default "CETTA_SHARED_MAIL_SOCKET" "")
     (if (not (== $environment "")) $environment
       (let $own (gw:field-or $config "socket" ())
         (if (nostr:is-text $own) (gw:relative $directory $own) (gw:claw-mail-socket))))))
(= (gw:claw-mail-socket)
   (let* (($xdg (system:getenv-or-default "XDG_CONFIG_HOME" ""))
          ($base (if (== $xdg "") (gw:expand "~/.config") $xdg))
          ($path (gw:expand (system:getenv-or-default "CLAW_MAIL_CONFIG" (gw:cat ($base "/godelclaw/mail.toml"))))))
     (case (gw:read-file $path)
       (((gw:failed $kind $detail) "")
        ($text (case (toml:parse $text)
                 (((JsonObject $pairs)
                   (let $socket (gw:field-or (JsonObject $pairs) "socket" "")
                     (if (== $socket "") "" (gw:relative (gw:parent $path) $socket))))
                  ($bad ""))))))))

; A key file others can read is refused, as the Python gateway refuses it.
(= (gw:load-keys $path)
   (case (fs:metadata $path follow-permissions)
     (((fs:info $kind $size $identity (fs:permissions $bits))
       (if (> (% $bits 64) 0) (gw:failed permission (gw:cat ($path " is readable by others; make it 0600")))
         (case (gw:read-file $path)
           (((gw:failed $k $d) (gw:failed $k $d))
            ($text (gw:add-keys (gw:pairs (json:parse $text)) $path))))))
      ($refused (gw:failed os (gw:cat ($path ": " (gw:text $refused))))))))
(= (gw:add-keys $pairs $path)
   (if (== $pairs ()) GwKeysLoaded
     (let* (((JsonPair $name $entry) (car-atom $pairs))
            ($secret (gw:field-or $entry "seckey_hex" ())))
       (if (not (nostr:is-text $secret)) (gw:failed value (gw:cat ($path ": no seckey_hex for " $name)))
         (let $added (add-atom &self (GwKey $name $secret)) (gw:add-keys (cdr-atom $pairs) $path))))))

(= (gw:check-names $names)
   (if (== $names ()) GwChecked
     (let $name (car-atom $names)
       (if (gw:agent-name (gw:last (str:split "/" $name) ""))
         (gw:check-names (cdr-atom $names))
         (gw:failed value (gw:cat ("not a claw-mail name: " (gw:text $name))))))))
(= (gw:missing-keys $names $missing)
   (if (== $names ()) $missing
     (let $name (car-atom $names)
       (gw:missing-keys (cdr-atom $names)
         (if (or (not (== (gw:secret $name) ())) (nostr:member $name $missing)) $missing (cons-atom $name $missing))))))
(= (gw:check-config)
   (case (gw:check-names (gw:append (collapse (match &self (GwInbound $key $recipient) $recipient)) ((gw:get outbox))))
     ((GwChecked
       (let $missing (gw:missing-keys (gw:append (collapse (match &self (GwInbound $k $r) $k))
                                                 (collapse (match &self (GwSender $s $sk) $sk))) ())
         (if (== $missing ()) GwConfigured
           (gw:failed value (gw:cat ("no key for: " (str:join ", " (gw:sort-texts $missing))))))))
      ($failure $failure))))

; ── state ───────────────────────────────────────────────────────────────────
(= (gw:known-field $name)
   (nostr:member $name ("since" "recent" "receipted" "outbound" "sent")))
(= (gw:load-state)
   (let $path (gw:get state)
     (case (fs:read-text-result $path 0 "raw")
       (((ok $text) (gw:state-from (json:parse $text) $path))
        (("ok" $text2) (gw:state-from (json:parse $text2) $path))
        ((missing $where) (gw:state-from (JsonObject ()) $path))
        (("missing" $where2) (gw:state-from (JsonObject ()) $path))
        ($refused (gw:failed os (gw:cat ($path ": " (gw:text $refused)))))))))
(= (gw:state-from $value $path)
   (case $value
     (((JsonObject $pairs)
       (let* (($order (gw:state-fields $pairs ()))
              ($missing (gw:absent-fields ("since" "recent" "receipted" "outbound" "sent") $order ())))
         (gw:set state-order (gw:append $order $missing))))
      ($other (gw:failed value (gw:cat ($path ": not a JSON object")))))))
(= (gw:absent-fields $fields $present $missing)
   (if (== $fields ()) (gw:reverse $missing)
     (let $field (car-atom $fields)
       (gw:absent-fields (cdr-atom $fields) $present
         (if (nostr:member $field $present) $missing (cons-atom $field $missing))))))
; Each top-level field once, in the file's order; a field that is not one of
; the five is kept as it was.
(= (gw:state-fields $pairs $order)
   (if (== $pairs ()) (gw:reverse $order)
     (let* (((JsonPair $name $value) (car-atom $pairs))
            ($seen (nostr:member $name $order))
            ($loaded (if (gw:known-field $name) (gw:load-field $name (gw:pairs $value))
                       (gw:set (GwStateExtra $name) $value))))
       (gw:state-fields (cdr-atom $pairs) (if $seen $order (cons-atom $name $order))))))
(= (gw:load-field $name $pairs)
   (if (== $pairs ()) ()
     (let* (((JsonPair $key $value) (car-atom $pairs))
            ($added (case $name
                      (("since" (gw:set-since $key $value))
                       ("recent" (gw:set-recent $key $value))
                       ("receipted" (gw:set-receipted $key $value))
                       ("outbound" (gw:set-outbound $key $value))
                       ("sent" (gw:set-sent $key $value))))))
       (gw:load-field $name (cdr-atom $pairs)))))

(= (gw:field-json $name)
   (case $name
     (("since" (JsonObject (collapse (match &self (GwSince $k1 $v1) (JsonPair $k1 $v1)))))
      ("recent" (JsonObject (collapse (match &self (GwRecent $k2 $v2) (JsonPair $k2 $v2)))))
      ("receipted" (JsonObject (collapse (match &self (GwReceipted $k3 $v3) (JsonPair $k3 $v3)))))
      ("outbound" (JsonObject (collapse (match &self (GwOutbound $k4 $v4) (JsonPair $k4 $v4)))))
      ("sent" (JsonObject (collapse (match &self (GwSent $k5 $v5) (JsonPair $k5 $v5)))))
      ($other (gw:get (GwStateExtra $name))))))
(= (gw:state-pairs $names $done)
   (if (== $names ()) (gw:reverse $done)
     (let $name (car-atom $names)
       (gw:state-pairs (cdr-atom $names) (cons-atom (JsonPair $name (gw:field-json $name)) $done)))))

; json.dump of the state to a 0600 temporary file, fsync, then rename.
(= (gw:save)
   (let* (($path (gw:get state))
          ($temporary (gw:with-tmp $path))
          ($directory (fs:make-directories (gw:parent $path) 511))
          ($text (json:stringify-python (JsonObject (gw:state-pairs (gw:get state-order) ())))))
     (case (fs:open-write $temporary ("create" "truncate") 384)
       (((fs:failed $a $b $c $d $e $f) (gw:failed os (gw:cat ($temporary ": " $f))))
        ($writer
         (let* (($written (fs:write-all $writer $text))
                ($synced (fs:sync $writer))
                ($closed (fs:close $writer))
                ($failures (gw:fs-failures ($written $synced $closed) ())))
           (if (not (== $failures ())) (gw:failed os (gw:cat ($temporary ": " (gw:text (car-atom $failures)))))
             (case (fs:rename $temporary $path)
               (((fs:failed $a2 $b2 $c3 $d2 $e2 $f2) (gw:failed os (gw:cat ($path ": " $f2))))
                ($renamed GwSaved))))))))))
(= (gw:fs-failures $results $failures)
   (if (== $results ()) $failures
     (gw:fs-failures (cdr-atom $results)
       (case (car-atom $results) (((fs:failed $a $b $c $d $e $f) (cons-atom $f $failures)) ($ok $failures))))))

; Forget what no read position can reach again, and sent mail past its
; receipt period.
(= (gw:prune)
   (let* (($stamps (collapse (match &self (GwSince $key $stamp) $stamp)))
          ($horizon (- (if (== $stamps ()) 0 (min-atom $stamps)) (* 2 (gw:get overlap))))
          ($oldest (- (system:wall-time) (* (gw:get receipt-days) 86400)))
          ($r1 (gw:remove-all (collapse (match &self (GwRecent $i1 $t1) (if (gw:before-stamp $t1 $horizon) (GwRecent $i1 $t1) GwKeep)))))
          ($r2 (gw:remove-all (collapse (match &self (GwReceipted $i2 $t2) (if (gw:before-stamp $t2 $horizon) (GwReceipted $i2 $t2) GwKeep)))))
          ($r3 (gw:remove-all (collapse (match &self (GwSent $i3 $record) (if (gw:before-stamp (nostr:field $record "at") $oldest) (GwSent $i3 $record) GwKeep))))))
     GwPruned))
(= (gw:before-stamp $stamp $limit) (if (== (get-type $stamp) Number) (< $stamp $limit) true))

; ── links ───────────────────────────────────────────────────────────────────
; A relay connection: (GwLink NAME ROLE CONN PHASE DEADLINE). ROLE is
; (inbound KEY RECIPIENT) or (outbound IDENT SENDER KEY EVENT); PHASE is
; connecting, challenge, (auth ID), (stored SUB), (live SUB), (publish ID),
; (backoff TIMER) or done; DEADLINE is a monotonic millisecond or 0.
; Outstanding io requests are (GwReq ID NAME CONN WHAT).
(= (gw:link $name)
   (let $links (collapse (match &self (GwLink $name $r $c $p $d) (GwLink $name $r $c $p $d)))
     (if (== $links ()) () (car-atom $links))))
(= (gw:drop-link $name)
   (gw:remove-all (collapse (match &self (GwLink $name $r $c $p $d) (GwLink $name $r $c $p $d)))))
(= (gw:put-link $name $role $conn $phase $deadline)
   (let $gone (gw:drop-link $name) (add-atom &self (GwLink $name $role $conn $phase $deadline))))
(= (gw:set-deadline $name $deadline)
   (case (gw:link $name)
     (((GwLink $n $role $conn $phase $old) (gw:put-link $name $role $conn $phase $deadline))
      ($none ()))))

(= (gw:queue-of $name)
   (let $queues (collapse (match &self (GwQueue $name $items) $items)) (if (== $queues ()) () (car-atom $queues))))
(= (gw:set-queue $name $items)
   (let $gone (gw:remove-all (collapse (match &self (GwQueue $name $old) (GwQueue $name $old))))
     (add-atom &self (GwQueue $name $items))))
(= (gw:enqueue $name $items) (gw:set-queue $name (gw:append (gw:queue-of $name) $items)))
(= (gw:stored-of $name)
   (let $stored (collapse (match &self (GwStored $name $items) $items)) (if (== $stored ()) () (car-atom $stored))))
(= (gw:set-stored $name $items)
   (let $gone (gw:remove-all (collapse (match &self (GwStored $name $old) (GwStored $name $old))))
     (add-atom &self (GwStored $name $items))))
(= (gw:awaiting $name) (not (== (collapse (match &self (GwAwait $name $r $e) $r)) ())))
(= (gw:clear-link $name)
   (let* (($q (gw:remove-all (collapse (match &self (GwQueue $name $a) (GwQueue $name $a)))))
          ($s (gw:remove-all (collapse (match &self (GwStored $name $b) (GwStored $name $b)))))
          ($w (gw:remove-all (collapse (match &self (GwAwait $name $c $d) (GwAwait $name $c $d))))))
     ()))

; Backoff as the Python gateway's forever(): 1 s, doubling to 60 s, back to
; 1 s after a run that lasted over a minute.
(= (gw:started-of $name)
   (let $values (collapse (match &self (GwStarted $name $at) $at)) (if (== $values ()) 0 (car-atom $values))))
; add-atom keeps its atom as written, so every value is computed first.
(= (gw:mark-started $name)
   (let* (($gone (gw:remove-all (collapse (match &self (GwStarted $name $old) (GwStarted $name $old)))))
          ($now (gw:now)))
     (add-atom &self (GwStarted $name $now))))
(= (gw:backoff-of $name)
   (let $values (collapse (match &self (GwBackoff $name $seconds) $seconds)) (if (== $values ()) 1 (car-atom $values))))
(= (gw:next-backoff $name)
   (let* (($current (if (> (- (gw:now) (gw:started-of $name)) 60000) 1 (gw:backoff-of $name)))
          ($gone (gw:remove-all (collapse (match &self (GwBackoff $name $old) (GwBackoff $name $old)))))
          ($next (gw:min 60 (* $current 2)))
          ($stored (add-atom &self (GwBackoff $name $next))))
     $current))

(= (gw:kind-name $kind)
   (case $kind
     ((connection "ConnectionError") (permission "PermissionError") (timeout "TimeoutError")
      (value "ValueError") (runtime "RuntimeError") (os "OSError") ($other (gw:text $kind)))))

(= (gw:submit $name $conn $what $request)
   (case (io:submit $request)
     (((io:pending $id) (let $added (add-atom &self (GwReq $id $name $conn $what)) (GwPending $id)))
      ($refused (gw:failed connection (gw:text $refused))))))
(= (gw:send $name $conn $items)
   (let $text (nostr:compact (JsonArray $items)) (gw:submit $name $conn send (ws:send $conn $text))))
(= (gw:listen $name $conn)
   (case (gw:submit $name $conn receive (ws:receive $conn))
     (((GwPending $r) ())
      ($failure (gw:link-failed $name $failure)))))
(= (gw:close-conn $name $conn)
   (let $submitted (gw:submit $name $conn close (ws:close $conn 1000 "")) ()))

(= (gw:secret-of-role $role)
   (case $role
     (((inbound $key $recipient) (gw:secret $key))
      ((outbound $ident $sender $key2 $event) (gw:secret $key2)))))

(= (gw:open-link $name $role)
   (let $relay (gw:get relay)
     (case (gw:submit $name 0 connect (ws:connect $relay 30000 4194304))
       (((GwPending $conn) (gw:put-link $name $role $conn connecting 0))
        ($failure (let $put (gw:put-link $name $role 0 connecting 0) (gw:link-failed $name $failure)))))))

; A completed request of a link. Completions of an earlier connection are
; spent; a connection that opened after its link moved on is closed.
(= (gw:link-event $id $name $conn $what $value)
   (case (gw:link $name)
     (((GwLink $n $role $current $phase $deadline)
       (if (== $what connect)
         (if (and (== $current $id) (== $phase connecting))
           (case $value
             (((ws:connected $c) (gw:connected $name $role $current))
              ($other (gw:link-failed $name (gw:failed connection (gw:lost $other))))))
           (gw:close-stale $name $id $value))
         (if (== $what retry)
           (if (== $phase (backoff $id)) (gw:restart $name $role) ())
           (if (== $what close) ()
             (if (not (== $conn $current)) ()
               (if (== $what receive)
                 (case $value
                   (((ws:message $c2 $text) (gw:on-message $name $current $text))
                    ($lost (gw:link-failed $name (gw:failed connection (gw:lost $lost))))))
                 (case $value
                   (((ws:sent $c3) ())
                    ($lost2 (gw:link-failed $name (gw:failed connection (gw:lost $lost2))))))))))))
      ($none (gw:close-stale $name $id $value)))))
(= (gw:close-stale $name $id $value)
   (case $value (((ws:connected $c) (gw:close-conn $name $c)) ($other ()))))
(= (gw:lost $value)
   (case $value
     (((ws:closed $c $code $reason) "relay closed the connection")
      ((ws:failed $c2 $code2 $text) (gw:text $text))
      ($other (gw:text $other)))))

(= (gw:connected $name $role $conn)
   (let $put (gw:put-link $name $role $conn challenge (+ (gw:now) 30000)) (gw:listen $name $conn)))

(= (gw:on-message $name $conn $text)
   (let $handled (gw:message $name (case (json:parse $text) (((JsonArray $items) $items) ($other ()))))
     (case (gw:link $name)
       (((GwLink $n $role $current $phase $deadline)
         (if (and (== $current $conn) (gw:reading $phase)) (gw:listen $name $conn) ()))
        ($none ())))))
(= (gw:reading $phase)
   (case $phase
     ((challenge true) ((auth $a) true) ((stored $s) true) ((live $l) true) ((publish $p) true) ($other false))))

; Before sign-in every message must be a JSON array, and each resets the
; 30-second wait; after it, anything else is ignored, as the Python reader does.
(= (gw:message $name $items)
   (case (gw:link $name)
     (((GwLink $n $role $conn $phase $deadline)
       (if (gw:signing-in $phase)
         (if (== $items ()) (gw:link-failed $name (gw:failed value "relay sent a message that is not a JSON array"))
           (gw:sign-in-step $name $role $conn $phase $items))
         (if (< (size-atom $items) 2) () (gw:reader-step $name $role $conn $phase $items))))
      ($none ()))))
(= (gw:signing-in $phase) (case $phase ((challenge true) ((auth $a) true) ($other false))))

; (GwOk ACCEPTED REASON) when the items are an OK answer naming this event.
(= (gw:ok-answer $items $event-id)
   (if (< (size-atom $items) 3) ()
     (let* (($verb (car-atom $items)) ($rest (cdr-atom $items)) ($named (car-atom $rest)) ($tail (cdr-atom $rest)))
       (if (and (== $verb "OK") (== $named $event-id))
         (GwOk (== (car-atom $tail) true) (if (< (size-atom $tail) 2) "" (gw:text (car-atom (cdr-atom $tail)))))
         ()))))

(= (gw:sign-in-step $name $role $conn $phase $items)
   (let $renewed (+ (gw:now) 30000)
     (case $phase
       ((challenge
         (case $items
           ((("AUTH" $challenge) (gw:authenticate $name $role $conn $challenge))
            ($other (gw:put-link $name $role $conn challenge $renewed)))))
        ((auth $auth-id)
         (case (gw:ok-answer $items $auth-id)
           (((GwOk true $reason) (gw:signed-in $name $role $conn))
            ((GwOk false $reason2)
             (gw:link-failed $name (gw:failed permission (gw:cat ("relay refused sign-in: " $reason2)))))
            ($not-ok (gw:put-link $name $role $conn $phase $renewed)))))
        ($other-phase ())))))

; NIP-42: a kind-22242 event naming the relay and the challenge.
(= (gw:authenticate $name $role $conn $challenge)
   (let* (($relay (gw:get relay))
          ($auth (nostr:sign (gw:secret-of-role $role) 22242
                   (JsonArray ((JsonArray ("relay" $relay)) (JsonArray ("challenge" $challenge)))) "" ())))
     (case (gw:send $name $conn ("AUTH" $auth))
       (((GwPending $s) (gw:put-link $name $role $conn (auth (nostr:field $auth "id")) (+ (gw:now) 30000)))
        ($failure (gw:link-failed $name $failure))))))

(= (gw:signed-in $name $role $conn)
   (case $role
     (((inbound $key $recipient) (gw:subscribe $name $role $conn $key))
      ((outbound $ident $sender $key2 $event) (gw:publish-pending $name $role $conn $event)))))

; ── inbound ─────────────────────────────────────────────────────────────────
(= (gw:start-inbound $key)
   (let $started (gw:mark-started $key) (gw:open-link $key (inbound $key (gw:recipient-of $key)))))

(= (gw:restart $name $role)
   (case $role
     (((inbound $key $recipient) (gw:start-inbound $key))
      ($other ()))))

; The mail and receipts addressed to the key from its read position less the
; overlap: stored ones, oldest first, then live ones in arrival order.
(= (gw:subscribe $name $role $conn $key)
   (let* (($me (crypto:xonly-public-key (gw:secret $key)))
          ($since (gw:max 0 (- (gw:since-of $key) (gw:get overlap))))
          ($sub (crypto:random-hex 8))
          ($filter (JsonObject ((JsonPair "kinds" (JsonArray (3838 7))) (JsonPair "#p" (JsonArray ($me)))
                                (JsonPair "since" $since)))))
     (case (gw:send $name $conn ("REQ" $sub $filter))
       (((GwPending $s)
         (let $cleared (gw:set-stored $name ()) (gw:put-link $name $role $conn (stored $sub) 0)))
        ($failure (gw:link-failed $name $failure))))))

(= (gw:reader-step $name $role $conn $phase $items)
   (let* (($verb (car-atom $items)) ($key (car-atom (cdr-atom $items))))
     (case $phase
       (((stored $sub) (gw:subscription-message $name $role $conn $phase $sub $verb $key $items))
        ((live $sub2) (gw:subscription-message $name $role $conn $phase $sub2 $verb $key $items))
        ((publish $event-id) (gw:publication-message $role $conn $event-id $items))
        ($other ())))))

(= (gw:subscription-message $name $role $conn $phase $sub $verb $key $items)
   (if (and (== $verb "EVENT") (and (== $key $sub) (== (size-atom $items) 3)))
     (gw:on-event $name $phase (car-atom (cdr-atom (cdr-atom $items))))
     (if (and (== $verb "EOSE") (== $key $sub))
       (gw:on-eose $name $role $conn $phase)
       (if (and (== $verb "CLOSED") (== $key $sub))
         (let $reason (if (> (size-atom $items) 2) (gw:text (car-atom (cdr-atom (cdr-atom $items)))) "")
           (gw:link-failed $name (gw:failed permission (gw:cat ("relay closed the subscription: " $reason)))))
         (if (== $verb "OK") (gw:receipt-ok $name $items) ())))))

(= (gw:on-event $name $phase $event)
   (case $phase
     (((stored $sub) (gw:set-stored $name (cons-atom $event (gw:stored-of $name))))
      ($live (let $queued (gw:enqueue $name ($event)) (gw:pump $name))))))

; The end of stored events: they are taken oldest first; then the read
; positions are pruned and saved. A second end is ignored.
(= (gw:on-eose $name $role $conn $phase)
   (case $phase
     (((stored $sub)
       (let* (($sorted (gw:sort-events (gw:reverse (gw:stored-of $name))))
              ($cleared (gw:set-stored $name ()))
              ($put (gw:put-link $name $role $conn (live $sub) 0))
              ($queued (gw:enqueue $name (gw:append $sorted (GwEndOfStored)))))
         (gw:pump $name)))
      ($other ()))))

; Take queued items in order; a receipt waiting for the relay's answer holds
; the rest.
(= (gw:pump $name)
   (if (gw:awaiting $name) ()
     (let $queue (gw:queue-of $name)
       (if (== $queue ()) ()
         (let* (($item (car-atom $queue)) ($rest (gw:set-queue $name (cdr-atom $queue))))
           (if (== $item GwEndOfStored) (gw:end-of-stored $name)
             (case (gw:take $name $item)
               (((gw:failed $kind $detail) (gw:link-failed $name (gw:failed $kind $detail)))
                (GwAwaiting ())
                ($done (gw:pump $name))))))))))

(= (gw:end-of-stored $name)
   (let $pruned (gw:prune)
     (case (gw:save)
       (((gw:failed $kind $detail) (gw:link-failed $name (gw:failed $kind $detail)))
        ($saved (if (gw:once) (gw:finish-key $name) (gw:pump $name)))))))

(= (gw:take $key $event)
   (if (not (gw:is-object $event)) GwSkipped
     (let $id (nostr:field $event "id")
       (if (not (nostr:is-text $id)) GwSkipped
         (if (gw:recent-has $id) GwSkipped
           (if (== (nostr:field $event "kind") 7)
             (case (gw:note-receipt $event)
               (((gw:failed $k $d) (gw:failed $k $d))
                ($noted (gw:record $key $event))))
             (case (gw:deliver $key $event)
               ((GwKept (gw:record $key $event))
                ((GwPublishReceipt $receipt) (gw:await-receipt $key $event $receipt))
                (GwRetry (gw:failed runtime
                           (gw:cat ("claw-mail did not take " (str:char-slice $id 0 16) "; retrying from the read position"))))
                ($failure $failure)))))))))

; The event is handled: remember it, advance the key's read position, save.
(= (gw:record $key $event)
   (let* (($created (nostr:field $event "created_at"))
          ($stamp (if (nostr:is-integer $created) $created 0))
          ($remembered (gw:set-recent (nostr:field $event "id") $stamp))
          ($advanced (gw:set-since $key (gw:max (gw:since-of $key) $stamp))))
     (gw:save)))

; Send one event into claw-mail if it is kept: GwKept, GwRetry to read it
; again later, or GwPublishReceipt when its author asked for a receipt.
(= (gw:deliver $key $event)
   (let* (($secret (gw:secret $key))
          ($me (crypto:xonly-public-key $secret))
          ($pubkey (nostr:field $event "pubkey"))
          ($author (gw:name-of $pubkey))
          ($letter (if (== $author ()) ()
                     (if (== $pubkey $me) ()
                       (if (nostr:verify $event) (nostr:open-letter $secret $event) ())))))
     (if (== $letter ())
       (let $logged (gw:log (gw:cat ("not kept: " (str:char-slice (gw:text (nostr:field $event "id")) 0 16)
                                     " (unsigned, unregistered or does not open)")))
         GwKept)
       (let $sender (gw:cat ("nostr." $author))
         (if (gw:agent-name $sender)
           (gw:deliver-letter $key $event $letter $author $sender)
           (let $logged2 (gw:log (gw:cat ("not kept: registry name " (gw:quoted $author) " is not a claw-mail name")))
             GwKept))))))

(= (gw:deliver-letter $key $event $letter $author $sender)
   (let* (($id (nostr:field $event "id"))
          ($recipient (gw:recipient-of $key))
          ($text (gw:render $event $letter $author $recipient)))
     (case (shared-mail:post (gw:get socket) (gw:get space) $id $recipient $sender $text)
       (((mailbox:sent $sent $status) (gw:after-delivery $key $event $id))
        ((MailboxFailure identity-conflict)
         (let $logged (gw:log (gw:cat ("conflict on " $id ": the same identifier was delivered with other text")))
           GwKept))
        ($refused
         (let $logged2 (gw:log (gw:cat ("claw-mail refused " $id ": " (gw:text $refused)))) GwRetry))))))

(= (gw:after-delivery $key $event $id)
   (if (and (nostr:member (JsonArray ("ack" "request")) (nostr:items (nostr:field $event "tags")))
            (not (gw:receipted-has $id)))
     (GwPublishReceipt (nostr:receipt (gw:secret $key) $event "delivered"))
     GwKept))

(= (gw:await-receipt $key $event $receipt)
   (case (gw:link $key)
     (((GwLink $name $role $conn $phase $deadline)
       (case (gw:send $key $conn ("EVENT" $receipt))
         (((GwPending $s)
           (let* (($receipt-id (nostr:field $receipt "id"))
                  ($waiting (add-atom &self (GwAwait $key $receipt-id $event)))
                  ($put (gw:put-link $name $role $conn $phase (+ (gw:now) 30000))))
             GwAwaiting))
          ($failure $failure))))
      ($none (gw:failed connection "the connection is gone")))))

(= (gw:receipt-ok $name $items)
   (let $waits (collapse (match &self (GwAwait $name $receipt $event) (GwAwait $name $receipt $event)))
     (if (== $waits ()) ()
       (case (car-atom $waits)
         (((GwAwait $n $receipt-id $awaited)
           (case (gw:ok-answer $items $receipt-id)
             (((GwOk $accepted $reason) (gw:receipt-answered $name $receipt-id $awaited $accepted $reason))
              ($other ())))))))))

(= (gw:receipt-answered $name $receipt-id $event $accepted $reason)
   (let* (($gone (remove-atom &self (GwAwait $name $receipt-id $event)))
          ($id (nostr:field $event "id"))
          ($noted (if $accepted (gw:set-receipted $id (nostr:field $event "created_at"))
                    (gw:log (gw:cat ("receipt for " $id " refused: " $reason)))))
          ($cleared (gw:set-deadline $name 0)))
     (case (gw:record $name $event)
       (((gw:failed $kind $detail) (gw:link-failed $name (gw:failed $kind $detail)))
        ($recorded (gw:pump $name))))))

; A delivered or read receipt for mail an agent sent is believed only from the
; key the mail was addressed to, and the sender is told.
(= (gw:note-receipt $event)
   (case (nostr:receipt-fact $event)
     (((nostr:receipted $message $how)
       (let $record (gw:sent-record $message)
         (if (== $record ()) GwNoted
           (if (not (nostr:verify $event)) GwNoted
             (if (not (== (nostr:field $event "pubkey") (nostr:field $record "to_key"))) GwNoted
               (let* (($state (if (== $how "delivered") "Delivered" "Read"))
                      ($to (gw:text (nostr:field $record "to")))
                      ($subject (gw:text (nostr:field $record "subject")))
                      ($text (gw:cat ($state " by " $to ": " $subject " (event " $message ")"))))
                 (gw:tell (nostr:field $record "sender") (gw:cat ("nostr-receipt-" (nostr:field $event "id"))) $text)))))))
      ($none GwNoted))))

; The claw-mail text of a kept message: mail headers, a blank line, the body.
(= (gw:event-ids $values $done)
   (if (== $values ()) (gw:reverse $done)
     (let $value (car-atom $values)
       (gw:event-ids (cdr-atom $values) (if (nostr:is-hex $value 64) (cons-atom $value $done) $done)))))
(= (gw:render $event $letter $author $recipient)
   (case $letter
     (((nostr:letter $subject $body $type $task)
       (let* (($id (nostr:field $event "id"))
              ($parents (gw:event-ids (nostr:tag-values $event "e") ()))
              ($date (system:format-time "%a, %d %b %Y %H:%M:%S GMT" (nostr:field $event "created_at") utc))
              ($head (gw:cat ("From: " $author " <" (nostr:field $event "pubkey") "@nostr>\nTo: " $recipient
                              "\nSubject: " (gw:header-value $subject) "\nDate: " $date
                              "\nMessage-ID: <" $id "@nostr>")))
              ($reply (if (== $parents ()) "" (gw:cat ("\nIn-Reply-To: <" (car-atom $parents) "@nostr>"))))
              ($type-line (if (== $type ()) "" (gw:cat ("\nX-Team-Type: " (gw:header-value $type)))))
              ($task-line (if (== $task ()) "" (gw:cat ("\nX-Team-Task: " (gw:header-value $task))))))
         (gw:cat ($head $reply $type-line $task-line "\n\n" $body)))))))

; Tell an outbound sender about its mail, once per identity.
(= (gw:tell $sender $ident $text)
   (case (shared-mail:post (gw:get socket) (gw:get space) $ident (gw:reply-to $sender) (gw:get outbox) $text)
     (((mailbox:sent $sent $status) GwTold)
      ($refused (gw:failed runtime (gw:cat ("could not tell " (gw:text $sender) ": " (gw:text $refused))))))))

; ── failures ────────────────────────────────────────────────────────────────
(= (gw:link-failed $name $failure)
   (case (gw:link $name)
     (((GwLink $n $role $conn $phase $deadline)
       (let* (($closed (if (> $conn 0) (gw:close-conn $name $conn) ()))
              ($cleared (gw:clear-link $name)))
         (case $role
           (((inbound $key $recipient) (gw:inbound-failed $name $role $failure))
            ((outbound $ident $sender $key2 $event)
             (let $gone (gw:drop-link $name) (gw:outbox-failed $failure)))))))
      ($none ()))))

(= (gw:failure-text $failure)
   (case $failure
     (((gw:failed $kind $detail) (gw:cat ((gw:kind-name $kind) ": " (gw:text $detail))))
      ($other (gw:cat ("RuntimeError: " (gw:text $other)))))))

(= (gw:inbound-failed $name $role $failure)
   (if (gw:once)
     (let $logged (gw:log (gw:cat ("inbound for " $name " failed: " (gw:failure-text $failure))))
       (gw:finish-key $name))
     (let* (($backoff (gw:next-backoff $name))
            ($logged2 (gw:log (gw:cat ("inbound for " $name ": " (gw:failure-text $failure)
                                      "; again in " (repr $backoff) "s")))))
       (case (gw:submit $name 0 retry (timer:after (* $backoff 1000)))
         (((GwPending $timer) (gw:put-link $name $role 0 (backoff $timer) 0))
          ($refused (gw:put-link $name $role 0 done 0)))))))

; Deadlines: a sign-in, a receipt or a publication the relay did not answer.
(= (gw:expire $now)
   (gw:expire-links (collapse (match &self (GwLink $name $role $conn $phase $deadline)
                                (if (and (> $deadline 0) (<= $deadline $now)) $name GwKeep)))))
(= (gw:expire-links $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names))
            ($failed (if (== $name GwKeep) ()
                       (gw:link-failed $name (gw:failed timeout "the relay did not answer in time")))))
       (gw:expire-links (cdr-atom $names)))))

; ── outbound ────────────────────────────────────────────────────────────────
(= (gw:outbox-state)
   (let $states (collapse (match &self (GwOutbox $state) $state)) (if (== $states ()) idle (car-atom $states))))
(= (gw:set-outbox $state)
   (let $gone (gw:remove-all (collapse (match &self (GwOutbox $old) (GwOutbox $old))))
     (add-atom &self (GwOutbox $state))))

; One round: open the account's inbox, handle deliveries to the outbox until
; it is idle, then watch the inbox for the next one.
(= (gw:outbox-round)
   (let $socket (gw:get socket)
     (case (shared-mail:identity $socket)
       (((shared-mail:account $uid $login $role)
         (case (mailbox:open $socket (gw:cat ("mail@" $login)))
           (((space $u $i $n $b $k $r $c) (gw:outbox-next (space $u $i $n $b $k $r $c)))
            ($error (gw:outbox-failed (gw:failed runtime (gw:cat ("outbox unavailable: " (gw:text $error)))))))))
        ($error2 (gw:outbox-failed (gw:failed runtime (gw:cat ("outbox unavailable: " (gw:text $error2))))))))))

(= (gw:outbox-next $space)
   (let $set (gw:set-outbox draining)
     (case (mailbox:next-in (gw:get socket) $space (gw:get outbox))
       (((mailbox:delivery $id $from $text)
         (case (gw:handle $id (shared-mail:sender $from) $text)
           ((GwHandled (gw:outbox-next $space))
            (GwPublishing ())
            ($failure (gw:outbox-failed $failure)))))
        ((mailbox:idle) (gw:outbox-idle $space))
        ($error (gw:outbox-failed (gw:failed runtime (gw:cat ("outbox unavailable: " (gw:text $error))))))))))

(= (gw:outbox-idle $space)
   (if (gw:once) (gw:set-outbox done)
     (case $space
       (((space $uuid $id $name $backend $kind $revision $count)
         (let* (($socket (gw:get socket)) ($rid (space:request-id)))
           (case (gw:submit outbox 0 watch (space:request $socket (request $rid watch $uuid $id $revision 30000) 31000))
             (((GwPending $watch) (gw:set-outbox (watching $watch)))
              ($failure (gw:outbox-failed $failure))))))))))

(= (gw:outbox-event $id $what $value)
   (let $state (gw:outbox-state)
     (if (== $what watch)
       (if (== $state (watching $id))
         (case $value
           (((reply $rid ok $changed) (gw:outbox-round))
            ($error (gw:outbox-failed (gw:failed runtime (gw:cat ("outbox unavailable: " (gw:text $error))))))))
         ())
       (if (== $what retry)
         (if (== $state (backoff $id)) (let $started (gw:mark-started outbox) (gw:outbox-round)) ())
         ()))))

(= (gw:outbox-failed $failure)
   (if (gw:once)
     (let $logged (gw:log (gw:cat ("outbound failed: " (gw:failure-text $failure)))) (gw:set-outbox done))
     (let* (($backoff (gw:next-backoff outbox))
            ($logged2 (gw:log (gw:cat ("outbound: " (gw:failure-text $failure) "; again in " (repr $backoff) "s")))))
       (case (gw:submit outbox 0 retry (timer:after (* $backoff 1000)))
         (((GwPending $timer) (gw:set-outbox (backoff $timer)))
          ($refused (gw:set-outbox done)))))))

; (headers, body) of an outbound request, or (GwNotSent WHY).
(= (gw:parse-outgoing $text)
   (let $at (gw:find $text "\n\n")
     (if (< $at 0) (GwNotSent "write the headers, a blank line, then the body")
       (let* (($head (str:byte-slice $text 0 $at))
              ($body (str:byte-slice $text (+ $at 2) (str:byte-length $text))))
         (case (gw:header-fields (gw:splitlines $head) ())
           (((GwNotSent $why) (GwNotSent $why))
            ($fields (gw:check-fields $fields $body))))))))
(= (gw:header-fields $lines $fields)
   (if (== $lines ()) (gw:reverse $fields)
     (let* (($line (car-atom $lines))
            ($colon (gw:find $line ":"))
            ($name (if (< $colon 0) "" (gw:strip (str:byte-slice $line 0 $colon)))))
       (if (== $name "")
         (GwNotSent (gw:cat ("not a header line: " (str:char-slice $line 0 80))))
         (gw:header-fields (cdr-atom $lines)
           (cons-atom (GwField (gw:lower $name) (gw:strip (str:byte-slice $line (+ $colon 1) (str:byte-length $line))))
                      $fields))))))
; The last value of a header, as later lines replace earlier ones.
(= (gw:field-of $fields $name $default)
   (if (== $fields ()) $default
     (case (car-atom $fields)
       (((GwField $field $value)
         (gw:field-of (cdr-atom $fields) $name (if (== $field $name) $value $default)))))))
(= (gw:unknown-names $fields $unknown)
   (if (== $fields ()) $unknown
     (case (car-atom $fields)
       (((GwField $name $value)
         (gw:unknown-names (cdr-atom $fields)
           (if (or (nostr:member $name ("to" "subject" "in-reply-to" "type" "task")) (nostr:member $name $unknown))
             $unknown (cons-atom $name $unknown))))))))
(= (gw:check-fields $fields $body)
   (let $unknown (gw:unknown-names $fields ())
     (if (not (== $unknown ()))
       (GwNotSent (gw:cat ("unknown header: " (str:join ", " (gw:sort-texts $unknown)))))
       (if (== (gw:field-of $fields "to" "") "") (GwNotSent "no To: header")
         (let $parent (gw:remove-suffix (gw:strip-angles (gw:field-of $fields "in-reply-to" "")) "@nostr")
           (if (and (not (== $parent "")) (not (nostr:is-hex $parent 64)))
             (GwNotSent "In-Reply-To must name an event identifier")
             (GwFields (gw:append $fields ((GwField "in-reply-to" $parent))) $body)))))))

; Publish one outbound request, tell its sender, settle it. The signed event
; is saved before it is published, so a retry publishes the same event.
(= (gw:handle $ident $sender $text)
   (let $pending (gw:outbound-of $ident)
     (if (== $pending ()) (gw:handle-new $ident $sender $text) (gw:start-publication $ident $sender $pending))))

(= (gw:outgoing $sender $text)
   (let $key (gw:sender-key $sender)
     (if (== $key ()) (GwNotSent (gw:cat ($sender " has no Nostr key at this gateway")))
       (case (gw:parse-outgoing $text)
         (((GwFields $fields $body)
           (let* (($to (gw:field-of $fields "to" "")) ($recipient (gw:key-of-name $to)))
             (if (== $recipient ()) (GwNotSent (gw:cat ($to " is not in the registry")))
               (GwOutgoing $key $recipient $to $fields $body))))
          ($refusal $refusal))))))

(= (gw:handle-new $ident $sender $text)
   (case (gw:outgoing $sender $text)
     (((GwOutgoing $key $recipient $to $fields $body)
       (let* (($event (nostr:seal (gw:secret $key) $recipient (gw:field-of $fields "subject" "") $body
                        (gw:field-of $fields "type" ()) (gw:field-of $fields "task" ())
                        (gw:field-of $fields "in-reply-to" "") true))
              ($pending (JsonObject ((JsonPair "key" $key) (JsonPair "to" $to) (JsonPair "to_key" $recipient)
                                     (JsonPair "event" $event))))
              ($kept (gw:set-outbound $ident $pending)))
         (case (gw:save)
           (((gw:failed $kind $detail) (gw:failed $kind $detail))
            ($saved (gw:start-publication $ident $sender $pending))))))
      ((GwNotSent $why)
       (case (gw:tell $sender (gw:cat ("nostr-outcome-" $ident)) (gw:cat ("Not sent: " $why)))
         ((GwTold (let $acked (shared-mail:ack (gw:get socket) (gw:get outbox) $ident) GwHandled))
          ($failure $failure)))))))

(= (gw:start-publication $ident $sender $pending)
   (let* (($role (outbound $ident $sender (nostr:field $pending "key") (nostr:field $pending "event")))
          ($state (gw:set-outbox (publishing $ident)))
          ($opened (gw:open-link outbound $role)))
     GwPublishing))

(= (gw:publish-pending $name $role $conn $event)
   (case (gw:send $name $conn ("EVENT" $event))
     (((GwPending $s) (gw:put-link $name $role $conn (publish (nostr:field $event "id")) (+ (gw:now) 30000)))
      ($failure (gw:link-failed $name $failure)))))

(= (gw:publication-message $role $conn $event-id $items)
   (case (gw:ok-answer $items $event-id)
     (((GwOk $accepted $reason)
       (let* (($closed (gw:close-conn outbound $conn)) ($gone (gw:drop-link outbound)))
         (case (gw:published $role $accepted $reason)
           ((GwHandled (gw:outbox-round))
            ($failure (gw:outbox-failed $failure))))))
      ($other ()))))

(= (gw:published $role $accepted $reason)
   (case $role
     (((outbound $ident $sender $key $event)
       (let* (($pending (gw:outbound-of $ident))
              ($to (gw:text (nostr:field $pending "to")))
              ($subject (case (nostr:open-letter (gw:secret $key) $event) (((nostr:letter $s $b $t $k) $s) ($none ""))))
              ($id (nostr:field $event "id"))
              ($outcome (gw:cat ("nostr-outcome-" $ident))))
         (case (if $accepted
                 (gw:sent $ident $sender $pending $to $subject $id $outcome)
                 (gw:tell $sender $outcome (gw:cat ("Not sent to " $to ": the relay refused it: " $reason))))
           ((GwTold (gw:settle $ident))
            ($failure $failure))))))))

(= (gw:sent $ident $sender $pending $to $subject $id $outcome)
   (let $recorded (gw:set-sent $id (JsonObject ((JsonPair "sender" $sender) (JsonPair "to" $to)
                                                (JsonPair "to_key" (gw:field-or $pending "to_key" JsonNull))
                                                (JsonPair "subject" $subject) (JsonPair "at" (system:wall-time)))))
     (case (gw:save)
       (((gw:failed $kind $detail) (gw:failed $kind $detail))
        ($saved (gw:tell $sender $outcome (gw:cat ("Sent to " $to ": " $subject " (event " $id ")"))))))))

; The outbox delivery is acknowledged before the saved event is forgotten, so
; a failed acknowledgement publishes the same event again, never a new one.
(= (gw:settle $ident)
   (case (shared-mail:ack (gw:get socket) (gw:get outbox) $ident)
     (((mailbox:acked $id $status)
       (let $gone (gw:drop-outbound $ident) (case (gw:save) (((gw:failed $k $d) (gw:failed $k $d)) ($saved GwHandled)))))
      ($refused (gw:failed runtime (gw:cat ("could not acknowledge " $ident ": " (gw:text $refused))))))))

; ── running ─────────────────────────────────────────────────────────────────
; Once: each key in turn reads what is stored, then the outbox is emptied.
(= (gw:finish-key $name)
   (let* (($link (gw:link $name))
          ($closed (case $link (((GwLink $n $r $conn $p $d) (if (> $conn 0) (gw:close-conn $name $conn) ())) ($none ()))))
          ($cleared (gw:clear-link $name))
          ($done (gw:drop-link $name))
          ($rest (gw:get pending-keys)))
     (if (== $rest ()) (gw:outbox-round)
       (let $set (gw:set pending-keys (cdr-atom $rest)) (gw:start-inbound (car-atom $rest))))))

(= (gw:dispatch $ids)
   (if (== $ids ()) ()
     (let* (($id (car-atom $ids))
            ($completion (io:wait $id 0))
            ($requests (collapse (match &self (GwReq $id $name $conn $what) (GwReq $id $name $conn $what))))
            ($handled (if (== $requests ()) ()
                        (case (car-atom $requests)
                          (((GwReq $rid $name2 $conn2 $what2)
                            (let $gone (remove-atom &self (GwReq $rid $name2 $conn2 $what2))
                              (case $completion
                                (((io:event $eid $value)
                                  (if (== $name2 outbox) (gw:outbox-event $rid $what2 $value)
                                    (gw:link-event $rid $name2 $conn2 $what2 $value)))
                                 ($none ()))))))))))
       (gw:dispatch (cdr-atom $ids)))))

(= (gw:wait-timeout $now)
   (let $deadlines (collapse (match &self (GwLink $name $role $conn $phase $deadline)
                               (if (> $deadline 0) $deadline GwKeep)))
     (gw:nearest $deadlines $now 60000)))
(= (gw:nearest $deadlines $now $best)
   (if (== $deadlines ()) $best
     (let $deadline (car-atom $deadlines)
       (gw:nearest (cdr-atom $deadlines) $now
         (if (== $deadline GwKeep) $best (gw:min $best (gw:max 0 (- $deadline $now))))))))

(= (gw:finished)
   (if (gw:once)
     (and (== (gw:outbox-state) done) (== (collapse (match &self (GwLink $n $r $c $p $d) $n)) ()))
     false))

(= (gw:cycle)
   (let* (($timeout (gw:wait-timeout (gw:now)))
          ($ready (io:wait-ready $timeout))
          ($handled (case $ready (((io:ready $ids) (gw:dispatch $ids)) ($idle ()))))
          ($expired (gw:expire (gw:now))))
     GwCycled))

(= (gw:loop)
   (if (gw:finished) GwFinished (let $cycled (once (gw:cycle)) (gw:loop))))

; The engine hands the arguments over as symbols.
(= (gw:options $arguments $once $config)
   (if (== $arguments ()) (GwOptions $once $config)
     (let* (($argument (gw:text (car-atom $arguments))) ($rest (cdr-atom $arguments)))
       (if (== $argument "--once") (gw:options $rest true $config)
         (if (== $argument "--config")
           (if (== $rest ()) (GwUsage "--config needs a path") (gw:options (cdr-atom $rest) $once (gw:text (car-atom $rest))))
           (if (str:starts-with $argument "--config=")
             (gw:options $rest $once (str:byte-slice $argument 9 (str:byte-length $argument)))
             (GwUsage (gw:cat ("unrecognized argument: " $argument)))))))))

(= (gw:start $once $config)
   (case (gw:load-config $config)
     ((GwConfigured
       (case (gw:load-state)
         (((gw:failed $kind $detail) (gw:failed $kind $detail))
          ($loaded
           (let* (($keys (collapse (match &self (GwInbound $key $recipient) $key)))
                  ($mode (gw:set mode (if $once once serve))))
             (if $once
               (if (== $keys ()) (gw:outbox-round)
                 (let $set (gw:set pending-keys (cdr-atom $keys)) (gw:start-inbound (car-atom $keys))))
               (let* (($inbound (gw:start-all $keys)) ($started (gw:mark-started outbox)))
                 (gw:outbox-round))))))))
      ($failure $failure))))
(= (gw:start-all $keys)
   (if (== $keys ()) () (let $started (gw:start-inbound (car-atom $keys)) (gw:start-all (cdr-atom $keys)))))

(= (gw:main)
   (case (gw:options (system:args) false
           (system:getenv-or-default "NOSTR_GATEWAY_CONFIG" "~/.config/godelclaw-nostr/gateway.toml"))
     (((GwOptions $once $config)
       (case (gw:start $once $config)
         (((gw:failed $kind $detail)
           (let $logged (gw:log (gw:failure-text (gw:failed $kind $detail))) (system:exit 1)))
          ($started (let $finished (gw:loop) (system:exit 0))))))
      ((GwUsage $why)
       (let $logged (gw:log (gw:cat ("usage: nostr_gateway.metta [--once] [--config PATH]: " $why))) (system:exit 2))))))

!(gw:main)

; A chat channel's durable transport between the loop and the channel's own
; service process (channels/chat_service_main.metta), for every channel that
; has no service of its own: IRC and Mattermost today.
;
; The service owns the connection: it keeps it open, answers keepalives while
; the loop waits on a model call, decides each message with the channel access
; policy (channels/access_policy.metta) and hands the loop what is recorded.
; The two sides share two spool directories under <root>/<kind>, the root
; METTACLAW_CHAT_SPOOL_ROOT, else "chat" beside METTACLAW_ENGINE_STATE_PATH:
;
; - inbox/: one whole JSON file a message, renamed into place and synced,
;   named <order>-<digest>.json so the names sort as the messages arrived:
;   {"id", "sender", "text", "note", "reason", "tier", "provenance", "at"}.
;   ID is the delivery identity the loop's input scope records ("irc:...",
;   "mattermost:<post id>"); NOTE, REASON, TIER and PROVENANCE are the access
;   policy's decision. A message stays in the inbox until the turn that read
;   it is committed: the loop removes it then (chat:ack), so a crash before
;   that presents it again, and one already committed is dropped unread.
; - outbox/: one file a send, {"key", "text"}, written by the loop and
;   removed by the service once the send is done; a send the channel refuses
;   for good leaves KEY.failed with the reason. A service that is down when
;   the loop sends finds the file when it comes back.
;
; A sender-wake decision also asks the resting loop to wake, through the same
; request file the command responder writes (METTACLAW_WAKE_REQUEST_PATH).
!(import! &self (library fs))
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/state_file_cetta.metta)
!(import! &self ./src/activity_source_cetta.metta)

!(bind! &chat-state (new-space))

(= (chat:root)
   (let $configured (system:getenv-or-default "METTACLAW_CHAT_SPOOL_ROOT" "")
     (if (not (== $configured "")) $configured
       (let $engine (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH" "")
         (if (== $engine "") "" (path:join (path:dirname $engine) "chat"))))))
(= (chat:folder $kind $part)
   (let $root (chat:root)
     (if (== $root "") "" (path:join (path:join $root (chat:kind-name $kind)) $part))))
(= (chat:kind-name $kind) (if (== (get-type $kind) String) $kind (repr $kind)))
(= (chat:inbox $kind) (chat:folder $kind "inbox"))
(= (chat:outbox $kind) (chat:folder $kind "outbox"))

;; ------------------------------------------------------------ spool files

; A name that sorts as the items arrived: wall-clock microseconds, then this
; process's sequence, then a digest of the item's identity.
(= (chat:next-sequence)
   (let* (($last (case (collapse (match &chat-state (ChatSequence $n) $n)) ((() 0) ($ns (car-atom $ns)))))
          ($next (+ $last 1))
          ($_ (remove-atom &chat-state (ChatSequence $last)))
          ($_ (add-atom &chat-state (ChatSequence $next))))
     $next))
(= (chat:pad $number $width)
   (let $digits (repr $number)
     (str:concat (str:byte-slice "00000000000000000000" 0 (max 0 (- $width (str:byte-length $digits)))) $digits)))
(= (chat:order) (str:join "-" ((chat:pad (math:floor (* (system:wall-time) 1000000)) 20) (chat:pad (chat:next-sequence) 8))))
(= (chat:file-name $identity) (str:join "" ((chat:order) "-" (str:byte-slice (str:sha256 $identity) 0 16) ".json")))

; Whole JSON files only, oldest name first, never a sibling being written.
(= (chat:names $folder)
   (if (or (== $folder "") (not (fs:exists $folder))) ()
     (sort-strings (chat:json-names (fs:entries $folder)))))
(= (chat:json-names $entries)
   (if (== $entries ()) ()
     (let* (($name (car-atom $entries)) ($rest (chat:json-names (cdr-atom $entries))))
       (if (and (str:ends-with $name ".json") (not (str:starts-with $name "."))) (cons-atom $name $rest) $rest))))
; An item: its JSON object, or ChatUnreadable.
(= (chat:item $path)
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $text)
       (case (catch (json:parse $text)) (((JsonObject $pairs) (JsonObject $pairs)) ($other ChatUnreadable))))
      ($failed ChatUnreadable))))
(= (chat:write $folder $name $object)
   (let $_ (fs:make-directories $folder 448)
     (sf:replace (path:join $folder $name) (json:stringify-python-with $object ((ensure-ascii False))) 384 True)))
(= (chat:field $item $key) (sf:get $item $key ""))

;; ------------------------------------------------------------ the service's half

; Record one decided message in the inbox; a sender wake also asks the loop to wake.
(= (chat:record $kind $id $sender $text (AccessDecisionV1 $visibility $note $reason $tier $provenance))
   (if (== $visibility drop) dropped
     (let* (($item (JsonObject ((JsonPair "id" $id) (JsonPair "sender" $sender) (JsonPair "text" $text)
                                (JsonPair "note" (repr $note)) (JsonPair "reason" $reason) (JsonPair "tier" $tier)
                                (JsonPair "provenance" $provenance) (JsonPair "at" (system:wall-time)))))
            ($written (chat:write (chat:inbox $kind) (chat:file-name $id) $item))
            ($_ (if (and (== $written True) (== $note sender-wake)) (chat:wake $reason) ())))
       (if (== $written True) recorded $written))))
(= (chat:wake $reason)
   (let $file (system:getenv-or-default "METTACLAW_WAKE_REQUEST_PATH" "")
     (if (== $file "") () (fs:write $file $reason))))

; The sends waiting, oldest first: (NAME KEY TEXT) each, an unreadable one
; set aside as NAME.unreadable.
(= (chat:sends $kind) (chat:sends-from (chat:outbox $kind) (chat:names (chat:outbox $kind))))
(= (chat:sends-from $folder $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names)) ($rest (chat:sends-from $folder (cdr-atom $names)))
            ($item (chat:item (path:join $folder $name))))
       (if (== $item ChatUnreadable)
         (let $_ (fs:rename (path:join $folder $name) (path:join $folder (str:concat $name ".unreadable"))) $rest)
         (cons-atom ($name (chat:field $item "key") (chat:field $item "text")) $rest)))))
; A send is done: its file goes. A refusal for good leaves NAME.failed.
(= (chat:sent $kind $name) (fs:remove (path:join (chat:outbox $kind) $name)))
(= (chat:failed $kind $name $why)
   (let* (($folder (chat:outbox $kind)) ($item (chat:item (path:join $folder $name)))
          ($receipt (if (== $item ChatUnreadable) (JsonObject ()) $item))
          ($_ (chat:write $folder (str:concat $name ".failed") (sf:put $receipt "failure" $why))))
     (fs:remove (path:join $folder $name))))

;; ------------------------------------------------------------ the loop's half

; What the loop prints when it starts on a chat channel.
(= (chat:started $kind)
   (format-args "{} channel: its service (run.sh chat-channel) owns the connection; spool {}"
                ((chat:kind-name $kind) (path:dirname (chat:inbox $kind)))))

; Queue a send for the service: its key names the send.
(= (chat:send $kind $text)
   (let $folder (chat:outbox $kind)
     (if (== $folder "") (format-args "{} unavailable: no state directory for its spool" ((chat:kind-name $kind)))
       (let* (($key (str:concat (chat:kind-name $kind) (str:concat "." (system:uuid4-hex))))
              ($written (chat:write $folder (chat:file-name $key) (JsonObject ((JsonPair "key" $key) (JsonPair "text" $text))))))
         (if (== $written True) (str:concat "durably queued: " $key) (format-args "send not queued: {}" ($written)))))))
; The messages of the inbox not yet committed, oldest first, as
; (NAME ID SENDER TEXT NOTE PROVENANCE); a message an earlier turn already
; committed is removed unread.
(= (chat:pending $kind) (chat:pending-from (chat:inbox $kind) (chat:names (chat:inbox $kind))))
(= (chat:pending-from $folder $names)
   (if (== $names ()) ()
     (let* (($name (car-atom $names)) ($rest (chat:pending-from $folder (cdr-atom $names)))
            ($item (chat:item (path:join $folder $name))))
       (if (== $item ChatUnreadable)
         (let $_ (fs:rename (path:join $folder $name) (path:join $folder (str:concat $name ".unreadable"))) $rest)
         (let $id (chat:field $item "id")
           (if (== (activity-source:already-committed $id) 1)
             (let $_ (fs:remove (path:join $folder $name)) $rest)
             (cons-atom ($name $id (chat:field $item "sender") (chat:field $item "text") (chat:field $item "note")
                         (chat:field $item "provenance"))
                        $rest)))))))

; What a message reads as: "SENDER: TEXT", the access policy's label for a
; sender who is not trusted after the sender.
(= (chat:line ($name $id $sender $text $note $provenance))
   (if (== $provenance "") (str:join "" ($sender ": " $text))
     (str:join "" ($sender " (" $provenance "): " $text))))

; The conversation a turn reads: every pending message, " | " between them,
; "" when there is none. The messages read are remembered for the turn.
(= (chat:receive $kind)
   (let* (($pending (chat:pending $kind))
          ($_ (chat:remember-presented $pending)))
     (str:join " | " (map-atom $pending $message (chat:line $message)))))
(= (chat:remember-presented $pending)
   (let $_ (collapse (match &chat-state (ChatPresented $old) (remove-atom &chat-state (ChatPresented $old))))
     (add-atom &chat-state (ChatPresented $pending))))
(= (chat:presented)
   (case (collapse (match &chat-state (ChatPresented $pending) $pending)) ((() ()) ($found (car-atom $found)))))
(= (chat:presented-ids) (map-atom (chat:presented) $message (let ($name $id $sender $text $note $provenance) $message $id)))

; Whether the messages read may keep a parked agent waiting: all silent.
(= (chat:quiet-batch?)
   (let $pending (chat:presented)
     (if (== $pending ()) False (chat:all-silent? $pending))))
(= (chat:all-silent? $pending)
   (if (== $pending ()) True
     (let ($name $id $sender $text $note $provenance) (car-atom $pending)
       (if (== $note "silent") (chat:all-silent? (cdr-atom $pending)) False))))

; The turn that read the messages is committed: they leave the inbox. 1 when
; nothing is left to settle, 0 when the ledger has not committed them yet.
(= (chat:ack $kind)
   (let $pending (chat:presented)
     (if (== $pending ()) 1
       (if (== (activity-source:committed (str:join "," (chat:presented-ids))) 1)
         (let* (($_ (map-atom $pending $message (let ($name $id $sender $text $note $provenance) $message
                                                  (fs:remove (path:join (chat:inbox $kind) $name)))))
                ($_ (chat:remember-presented ())))
           1)
         0))))

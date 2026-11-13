; The Mattermost channel's own service: it keeps the websocket to the server
; (the engine's transport answers its pings) and carries messages both ways
; through the chat spool (channels/chat_spool.metta).
;
; - It asks the server who the bot is (users/me), opens the websocket and
;   authenticates on it with an authentication_challenge carrying the token.
; - Every post the websocket reports in METTACLAW_MATTERMOST_CHANNEL_ID that is
;   not the bot's own and not seen before (the last 4096 post ids) is decided
;   by the access policy, (ap:decide mattermost USER-ID CHANNEL-ID MENTIONED),
;   MENTIONED when the post mentions the bot. A recorded one goes to the inbox
;   as "mattermost:<post id>", with the sender's display name (first and last
;   name, else the username).
; - Each send of the outbox is posted to the channel; it goes from the outbox
;   once the server answers 2xx, and with a .failed receipt on any other 4xx
;   than 429. Anything else keeps it for the next step.
; - A websocket that has heard nothing for METTACLAW_MATTERMOST_QUIET_SECONDS
;   (30) is sent a ping action, and closed after twice that. A closed or failed
;   one is opened again after METTACLAW_MATTERMOST_RECONNECT_SECONDS (10).
;
; The server is METTACLAW_MATTERMOST_URL (https://chat.singularitynet.io), its
; websocket METTACLAW_MATTERMOST_WEBSOCKET_URL (the URL's wss:// or ws:// form
; with /api/v4/websocket), the token METTACLAW_MATTERMOST_TOKEN. Run by
; `run.sh chat-channel`.
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./channels/chat_spool.metta)
!(import! &self ./channels/access_policy.metta)
!(import! &self ./src/utils_cetta.metta)

!(bind! &mm-state (new-space))
!(bind! &mm-seen (new-space))
(= (mm:get $kind $default)
   (case (collapse (match &mm-state (MmState $kind $value) $value)) ((() $default) ($values (car-atom $values)))))
(= (mm:set $kind $value)
   (let $_ (collapse (match &mm-state (MmState $kind $old) (remove-atom &mm-state (MmState $kind $old))))
     (add-atom &mm-state (MmState $kind $value))))

(= (mm:env $name $default) (system:getenv-or-default $name $default))
(= (mm:number $name $default)
   (case (catch (str:string->number (mm:env $name "")))
     (((Error $where $why) $default) ($n (if (> $n 0) $n $default)))))
(= (mm:url) (mm:trim-slash (mm:env "METTACLAW_MATTERMOST_URL" "https://chat.singularitynet.io")))
(= (mm:trim-slash $url) (if (str:ends-with $url "/") (mm:trim-slash (str:byte-slice $url 0 (- (str:byte-length $url) 1))) $url))
(= (mm:websocket-url)
   (let $configured (mm:env "METTACLAW_MATTERMOST_WEBSOCKET_URL" "")
     (if (not (== $configured "")) $configured
       (let $url (mm:url)
         (str:concat (if (str:starts-with $url "https://") (str:concat "wss://" (str:byte-slice $url 8 (str:byte-length $url)))
                       (if (str:starts-with $url "http://") (str:concat "ws://" (str:byte-slice $url 7 (str:byte-length $url))) $url))
                     "/api/v4/websocket")))))
(= (mm:channel) (mm:env "METTACLAW_MATTERMOST_CHANNEL_ID" "8fjrmabjx7gupy7e5kjznpt5qh"))
(= (mm:token) (mm:env "METTACLAW_MATTERMOST_TOKEN" ""))
(= (mm:tick-ms) 5000)
(= (mm:log $text) (system:write stderr (str:join "" ("[mattermost] " $text "\n"))))

;; ------------------------------------------------------------ REST

; (STATUS BODY) of one request, or (0 REASON) when no answer came.
(= (mm:rest $method $path $body)
   (let $headers ((http:header "Authorization" (str:concat "Bearer " (mm:token)))
                  (http:header "Content-Type" "application/json"))
     (case (catch (io:submit (http:request $method (str:concat (mm:url) $path) $headers $body 30000 1048576)))
       (((io:pending $id)
         (case (io:wait $id 35000)
           (((io:event $done (http:response $status $answer)) ($status $answer))
            ((io:idle) (let $_ (catch (io:cancel $id)) (0 "no answer within 35 s")))
            ($outcome (0 (repr $outcome))))))
        ($refused (0 (repr $refused)))))))
(= (mm:json-field $text $key)
   (case (catch (json:parse $text)) (((JsonObject $pairs) (sf:get (JsonObject $pairs) $key "")) ($other ""))))
(= (mm:bot-id)
   (case (mm:rest "GET" "/api/v4/users/me" "")
     ((($status $body) (if (and (>= $status 200) (< $status 300)) (mm:json-field $body "id") "")))))
; A user's display name: first and last name, else the username, else the id.
(= (mm:display-name $user)
   (case (mm:rest "GET" (str:concat "/api/v4/users/" $user) "")
     ((($status $body)
       (if (not (and (>= $status 200) (< $status 300))) $user
         (let* (($full (str:trim (str:join " " ((mm:json-field $body "first_name") (mm:json-field $body "last_name")))))
                ($name (mm:json-field $body "username")))
           (if (not (== $full "")) $full (if (== $name "") $user $name))))))))

;; ------------------------------------------------------------ the websocket

(= (mm:connect)
   (case (catch (io:submit (ws:connect (mm:websocket-url) 30000 (mm:number "METTACLAW_MATTERMOST_MAX_MESSAGE_BYTES" 1048576))))
     (((io:pending $id)
       (case (io:wait $id 35000)
         (((io:event $done (ws:connected $conn)) (MmConnected $conn))
          ((io:idle) (let $_ (catch (io:cancel $id)) (MmFailed "no websocket within 35 s")))
          ($outcome (MmFailed (repr $outcome))))))
      ($refused (MmFailed (repr $refused))))))
(= (mm:receive $conn) (case (catch (io:submit (ws:receive $conn))) (((io:pending $id) $id) ($refused none))))
(= (mm:send-frame $conn $object)
   (let* (($seq (+ (mm:get seq 0) 1)) ($_ (mm:set seq $seq))
          ($text (json:stringify-python-with (sf:put $object "seq" $seq) ((ensure-ascii False) (separators "," ":")))))
     (case (catch (io:submit (ws:send $conn $text)))
       (((io:pending $id) (case (io:wait $id 30000) (((io:event $done (ws:sent $sent)) True) ($outcome False))))
        ($refused False)))))
(= (mm:close $conn $why)
   (let* (($_ (mm:log $why))
          ($_ (case (catch (io:submit (ws:close $conn 1000 "closing"))) (((io:pending $id) (io:wait $id 5000)) ($refused ())))))
     done))
(= (mm:ended $why) (let $_ (mm:log $why) done))

(= (mm:serve)
   (let* (($_ (once (mm:session)))
          ($_ (sleep (mm:number "METTACLAW_MATTERMOST_RECONNECT_SECONDS" 10))))
     (mm:serve)))
(= (mm:session)
   (let $bot (mm:bot-id)
     (if (== $bot "") (mm:ended "the server did not say who the bot is (users/me)")
       (case (mm:connect)
         (((MmConnected $conn)
           (let* (($_ (mm:set bot $bot)) ($_ (mm:heard))
                  ($_ (mm:send-frame $conn (JsonObject ((JsonPair "action" "authentication_challenge")
                                                       (JsonPair "data" (JsonObject ((JsonPair "token" (mm:token))))))))))
             (mm:run (mm:next $conn (mm:receive $conn)))))
          ((MmFailed $why) (mm:ended (str:concat "websocket failed: " $why))))))))
(= (mm:next $conn $receive)
   (if (== $receive none) (mm:close $conn "the websocket refused a receive") (MmSession $conn $receive)))
(= (mm:run $session)
   (if (== $session done) done (mm:run (once (mm:step $session)))))

(= (mm:step (MmSession $conn $receive))
   (let* (($watch (mm:watch))
          ($_ (io:wait-ready (cons-atom io:ids (mm:handles (cons-atom $receive (cons-atom $watch ())))) (mm:tick-ms)))
          ($_ (mm:finish-watch $watch)))
     (case (io:poll $receive)
       (((io:event $id (ws:message $c $text))
         (let* (($_ (mm:heard)) ($_ (mm:event $text)) ($_ (mm:flush)))
           (mm:next $conn (mm:receive $conn))))
        ((io:event $id (ws:closed $c $code $reason)) (mm:ended (format-args "websocket closed: {} {}" ($code $reason))))
        ((io:event $id (ws:failed $c $code $text)) (mm:ended (str:concat "websocket failed: " $text)))
        ((io:idle)
         (if (mm:dead? $conn) (let $_ (catch (io:cancel $receive)) (mm:close $conn "the server stopped answering"))
           (let $_ (mm:flush) (MmSession $conn $receive))))
        ($other (mm:ended (repr $other)))))))
(= (mm:handles $candidates)
   (if (== $candidates ()) ()
     (let* (($head (car-atom $candidates)) ($rest (mm:handles (cdr-atom $candidates))))
       (if (== $head none) $rest (cons-atom $head $rest)))))
(= (mm:watch)
   (let $folder (chat:outbox mattermost)
     (if (== $folder "") none
       (let $_ (fs:make-directories $folder 448)
         (case (catch (io:submit (fs:changed $folder))) (((io:pending $id) $id) ($refused none)))))))
(= (mm:finish-watch $watch)
   (if (== $watch none) () (case (io:poll $watch) (((io:idle) (catch (io:cancel $watch))) ($done ())))))

(= (mm:heard) (let $_ (mm:set pinged False) (mm:set heard (system:monotonic-ns))))
(= (mm:dead? $conn)
   (let* (($quiet (/ (- (system:monotonic-ns) (mm:get heard 0)) 1000000000.0))
          ($limit (mm:number "METTACLAW_MATTERMOST_QUIET_SECONDS" 30)))
     (if (> $quiet (* 2 $limit)) True
       (let $_ (if (and (> $quiet $limit) (not (mm:get pinged False)))
                 (let $_ (mm:set pinged True) (mm:send-frame $conn (JsonObject ((JsonPair "action" "ping")))))
                 ())
         False))))

;; ------------------------------------------------------------ posts

; One websocket message: a post of the channel is decided and recorded.
(= (mm:event $text)
   (case (catch (json:parse $text))
     (((JsonObject $pairs)
       (let $event (JsonObject $pairs)
         (if (not (== (sf:get $event "event" "") "posted")) ignored
           (let $data (sf:get $event "data" (JsonObject ()))
             (case (catch (json:parse (sf:get $data "post" "")))
               (((JsonObject $fields) (mm:post (JsonObject $fields) (sf:get $data "mentions" "")))
                ($unreadable ignored)))))))
      ($other ignored))))
(= (mm:post $post $mentions)
   (let* (($id (sf:get $post "id" "")) ($user (sf:get $post "user_id" "")) ($bot (mm:get bot "")))
     (if (not (== (sf:get $post "channel_id" "") (mm:channel))) other-channel
       (if (== $user $bot) own
         (if (not (mm:first-sight? $id)) seen
           (let* (($mentioned (mm:mentions? $mentions $bot))
                  ($decision (ap:decide mattermost $user (mm:channel) $mentioned)))
             (chat:record mattermost (str:concat "mattermost:" $id) (mm:display-name $user) (sf:get $post "message" "") $decision)))))))
; Mentions arrive as the JSON text of a list of user ids.
(= (mm:mentions? $mentions $bot)
   (if (or (== $bot "") (not (== (get-type $mentions) String))) False
     (case (catch (json:parse $mentions))
       (((JsonArray $ids) (mm:member? $bot $ids)) ($other False)))))
(= (mm:member? $x $items)
   (if (== $items ()) False (if (== (car-atom $items) $x) True (mm:member? $x (cdr-atom $items)))))

; A post id not seen before is remembered: the last 4096, oldest forgotten first.
(= (mm:seen-limit) 4096)
(= (mm:first-sight? $id)
   (if (== $id "") False
     (if (not (== (collapse (match &mm-seen (MmSeen $id $n) True)) ())) False
       (let* (($n (+ (mm:get seen-count 0) 1)) ($_ (mm:set seen-count $n))
              ($_ (add-atom &mm-seen (MmSeen $id $n)))
              ($oldest (- $n (mm:seen-limit))))
         (let $_ (if (> $oldest 0) (collapse (match &mm-seen (MmSeen $old $oldest) (remove-atom &mm-seen (MmSeen $old $oldest)))) ())
           True)))))

;; ------------------------------------------------------------ the outbox

(= (mm:flush) (mm:flush-sends (chat:sends mattermost)))
(= (mm:flush-sends $sends)
   (if (== $sends ()) flushed
     (let ($name $key $text) (car-atom $sends)
       (case (mm:post-text $text)
         ((sent (let $_ (chat:sent mattermost $name) (mm:flush-sends (cdr-atom $sends))))
          ((refused $why) (let $_ (chat:failed mattermost $name $why) (mm:flush-sends (cdr-atom $sends))))
          ($later later))))))
(= (mm:post-text $text)
   (let* (($message (string-replace $text "\\n" "\n"))
          ($body (json:stringify-python-with (JsonObject ((JsonPair "channel_id" (mm:channel)) (JsonPair "message" $message)))
                                            ((ensure-ascii False) (separators "," ":")))))
     (case (mm:rest "POST" "/api/v4/posts" $body)
       ((($status $answer)
         (if (and (>= $status 200) (< $status 300)) sent
           (if (and (>= $status 400) (and (< $status 500) (not (== $status 429))))
             (refused (format-args "HTTP {}: {}" ($status (str:byte-slice $answer 0 (min 300 (str:byte-length $answer))))))
             later)))))))

; Telegram files, natively: the attachments messages carry, downloaded into
; the agent's inbox, and the files and images she sends. The Telegram client
; (channels/telegram.metta) logs, presents and wakes; this module does the Bot
; API's part and keeps the downloads in flight.
;
; Downloads
; - A message's file is a photo (its largest size) or one of Telegram's
;   single-file kinds. Its download is two requests submitted with io:submit,
;   getFile and then the file itself, written with http:save into the
;   attachments directory as MESSAGE-ID-NAME, the name cut to safe characters.
;   Nothing waits for them: each poll of the client collects what finished
;   (tgf:collect), and the client's rest wait wakes when one does.
; - The answer to a finished download is an announcement for the message:
;   where the file was saved, or why it was not (no file id, past getFile's
;   20 MiB bot limit, no file path, or what failed). A failure never shows a
;   URL or the token.
; - Downloads the ledger logged as dispatched and never as ready are found
;   again at start (tgf:pending), the newest 100.
;
; Uploads
; - A file goes to a chat as a multipart sendDocument or sendPhoto, after the
;   same checks as before (a file, a token, a chat, an allowed chat, at most
;   50 MiB); the answer says what happened, with the message id, or why not.
; - An SVG to send as an image is first drawn as PNG by the first tool that
;   can: cairosvg (the Python runtime's command), rsvg-convert, inkscape, or
;   ImageMagick's convert; without any it goes as a document, with a note.
!(import! &self (library fs))
!(import! &self (library io))
!(import! &self (library json))
!(import! &self (library path))
!(import! &self (library proc))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/state_file_cetta.metta)
!(import! &self ./src/effect_receipt_values_cetta.metta)

!(bind! &tgf (new-space))
!(bind! &tgf-unsafe-name (new-state (re:compile "[^A-Za-z0-9._-]")))

;; ------------------------------------------------------------- configuration

(= (tgf:env $name $default) (system:getenv-or-default $name $default))
; (TgfConfigV1 BASE-URL TOKEN ATTACHMENTS-DIRECTORY LIMIT), read from the
; settings the Python client read: the token is the agent's own, never shown.
; LIMIT caps a download, METTACLAW_TELEGRAM_ATTACHMENT_MAX_BYTES, by default
; getFile's own bot limit of 20 MiB.
(= (tgf:config)
   (TgfConfigV1 (tgf:env "METTACLAW_TELEGRAM_BASE_URL" "https://api.telegram.org") (tgf:token) (tgf:attachments-dir)
                (tgf:attachment-limit)))
(= (tgf:attachment-limit)
   (let $text (str:trim (tgf:env "METTACLAW_TELEGRAM_ATTACHMENT_MAX_BYTES" ""))
     (if (and (not (== $text "")) (str:is-all-in digit $text)) (str:string->number $text) 20971520)))
(= (tgf:token)
   (let $token (tgf:env "METTACLAW_TELEGRAM_BOT_TOKEN" "")
     (if (== $token "") (tgf:env "TELEGRAM_BOT_TOKEN" "") $token)))
; METTACLAW_TELEGRAM_ATTACHMENTS_DIR, else memory/attachments beside the
; transport offset's directory, else under the working directory.
(= (tgf:attachments-dir)
   (let $configured (tgf:env "METTACLAW_TELEGRAM_ATTACHMENTS_DIR" "")
     (if (not (== $configured "")) $configured
       (let $base (path:dirname (tgf:env "METTACLAW_TELEGRAM_OFFSET_PATH" ""))
         (path:join (path:join (if (== $base "") "." $base) "memory") "attachments")))))
; Telegram's limit for a bot's upload.
(= (tgf:upload-limit) 52428800)

;; ------------------------------------------------------------- values

; A message field as the client parses it (JsonString, JsonNumber, ...) or
; as json:parse gives it, unwrapped; None when absent or null.
(= (tgf:at $object $key)
   (case (sf:get $object $key JsonNull)
     ((JsonNull None) ((JsonString $text) $text) ((JsonNumber $raw) (tgf:number $raw)) ((JsonBool $flag) $flag)
      ($value $value))))
(= (tgf:number $raw) (case (catch (str:string->number $raw)) (((Error $where $why) $raw) ($n $n))))
(= (tgf:truthy? $value)
   (if (== (get-type $value) Number) (not (== $value 0))
     (case $value ((None False) (False False) ("" False) ((JsonArray ()) False) ((JsonObject ()) False) ($other True)))))
(= (tgf:str $value) (if (== $value None) "None" (erv:str $value)))
(= (tgf:first-of $values) (if (== $values ()) None (if (tgf:truthy? (car-atom $values)) (car-atom $values) (tgf:first-of (cdr-atom $values)))))
(= (tgf:join $parts) (str:join "" $parts))
(= (tgf:error-kind $value) (case $value (((Error $where $why) $why) ($other None))))

;; ------------------------------------------------------------- the file a message carries

(= (tgf:single-kinds) ("document" "video" "audio" "voice" "animation" "video_note" "sticker"))
; (TgfFileV1 KIND FILE-ID NAME MIME SIZE), absent values None; None for none.
(= (tgf:file-of $message)
   (let $single (tgf:first-single $message (tgf:single-kinds))
     (if (== $single None) (tgf:photo-of $message) $single)))
(= (tgf:first-single $message $kinds)
   (if (== $kinds ()) None
     (let* (($kind (car-atom $kinds)) ($item (sf:get $message $kind JsonNull)))
       (if (tgf:truthy? (tgf:at $message $kind))
         (TgfFileV1 $kind (tgf:at $item "file_id") (tgf:at $item "file_name") (tgf:at $item "mime_type") (tgf:at $item "file_size"))
         (tgf:first-single $message (cdr-atom $kinds))))))
; The largest photo size, the first of equals.
(= (tgf:photo-of $message)
   (case (sf:get $message "photo" JsonNull)
     (((JsonArray $sizes)
       (if (== $sizes ()) None
         (let $best (tgf:largest (cdr-atom $sizes) (car-atom $sizes))
           (TgfFileV1 "photo" (tgf:at $best "file_id") None "image/jpeg" (tgf:at $best "file_size")))))
      ($other None))))
(= (tgf:largest $sizes $best)
   (if (== $sizes ()) $best
     (let $size (car-atom $sizes)
       (tgf:largest (cdr-atom $sizes) (if (> (tgf:size-of $size) (tgf:size-of $best)) $size $best)))))
(= (tgf:size-of $item) (let $size (tgf:at $item "file_size") (if (== (get-type $size) Number) $size 0)))

; "KIND NAME, MIME, SIZEb", the name the file's own, else its id, else "?".
(= (tgf:label (TgfFileV1 $kind $id $name $mime $size))
   (tgf:join ($kind " " (tgf:str (let $shown (tgf:first-of ($name $id)) (if (== $shown None) "?" $shown)))
              (if (tgf:truthy? $mime) (str:concat ", " (tgf:str $mime)) "")
              (if (tgf:truthy? $size) (tgf:join (", " (tgf:str $size) "b")) ""))))
(= (tgf:note $file $what) (tgf:join ("[attachment " (tgf:label $file) " — " $what "]")))

; A file name as saved: the base name, each character but letters, digits,
; '.', '_' and '-' made '_', '.' and '_' trimmed from its ends, at most 120
; characters; the fallback, as it is, when nothing is left.
(= (tgf:safe-name $name $fallback)
   (let* (($given (if (tgf:truthy? $name) (tgf:str $name) $fallback))
          ($base (tgf:basename $given))
          ($safe (re:replace-template (get-state &tgf-unsafe-name) $base "_"))
          ($trimmed (str:strip-python $safe "._")))
     (str:char-slice (if (== $trimmed "") $fallback $trimmed) 0 120)))
(= (tgf:basename $text) (tgf:last (str:split "/" $text)))
(= (tgf:last $items) (if (== (cdr-atom $items) ()) (car-atom $items) (tgf:last (cdr-atom $items))))

; RFC 3986 percent-encoding of a query value: unreserved characters stay.
(= (tgf:query-value $text) (str:join "" (map-atom (str:python-codepoints $text) $code (tgf:encode-point $code))))
(= (tgf:encode-point $code)
   (if (tgf:unreserved? $code) (str:from-python-codepoints ($code))
     (str:join "" (map-atom (tgf:utf8 $code) $byte (str:concat "%" (tgf:hex2 $byte))))))
(= (tgf:unreserved? $code)
   (if (and (>= $code 65) (<= $code 90)) True
     (if (and (>= $code 97) (<= $code 122)) True
       (if (and (>= $code 48) (<= $code 57)) True
         (or (or (== $code 45) (== $code 95)) (or (== $code 46) (== $code 126)))))))
(= (tgf:utf8 $code)
   (if (< $code 128) ($code)
     (if (< $code 2048) ((+ 192 (tgf:div $code 64)) (+ 128 (% $code 64)))
       (if (< $code 65536) ((+ 224 (tgf:div $code 4096)) (+ 128 (% (tgf:div $code 64) 64)) (+ 128 (% $code 64)))
         ((+ 240 (tgf:div $code 262144)) (+ 128 (% (tgf:div $code 4096) 64)) (+ 128 (% (tgf:div $code 64) 64)) (+ 128 (% $code 64)))))))
(= (tgf:div $a $b) (/ (- $a (% $a $b)) $b))
(= (tgf:hex2 $byte) (str:concat (tgf:hex-digit (tgf:div $byte 16)) (tgf:hex-digit (% $byte 16))))
(= (tgf:hex-digit $d) (index-atom ("0" "1" "2" "3" "4" "5" "6" "7" "8" "9" "A" "B" "C" "D" "E" "F") $d))

;; ------------------------------------------------------------- downloads

; The Bot API's address for a method, and for a file's bytes.
(= (tgf:method-url (TgfConfigV1 $base $token $dir $limit) $method) (tgf:join ($base "/bot" $token "/" $method)))
(= (tgf:file-url (TgfConfigV1 $base $token $dir $limit) $remote) (tgf:join ($base "/file/bot" $token "/" $remote)))

; Why a request did not answer, in words that carry no URL, token or body.
(= (tgf:failure $result)
   (case $result
     (((http:response $status $body) (tgf:join ("HTTP " (repr $status))))
      ((Error $source (http:error transport $code $message)) (tgf:join ("transport error " (repr $code))))
      ((Error $source (http:error response-too-large $limit)) (tgf:join ("exceeds " (repr $limit) "b cap")))
      ((Error $source (http:error save $errno $message)) (tgf:join ("cannot save (errno " (repr $errno) ")")))
      ((Error $source (http:error $kind)) (repr $kind))
      ((Error $source $why) "request refused")
      ($other "no answer"))))
(= (tgf:ok-status? $status) (and (>= $status 200) (< $status 300)))

; Submit a request: its handle, or None when it is refused.
(= (tgf:submit $request)
   (case (catch (io:submit $request))
     (((io:pending $id) $id)
      ($refused None))))
(= (tgf:keep $handle $stage $job $file $dest)
   (let* (($_ (add-atom &tgf (TgfInFlightV1 $handle $stage $job $file $dest)))
          ($_ (add-atom &self (idle:extra-io $handle))))
     (TgfPendingV1 $handle)))
(= (tgf:drop $handle $stage $job $file $dest)
   (let* (($_ (remove-atom &tgf (TgfInFlightV1 $handle $stage $job $file $dest)))
          ($_ (remove-atom &self (idle:extra-io $handle))))
     True))

; Begin a download for JOB, which holds MESSAGE among its fields: the answer
; is (TgfPendingV1 HANDLE), or (TgfReadyV1 JOB ANNOUNCEMENT) when nothing is
; to be fetched.
(= (tgf:dispatch $config $job $message)
   (let $file (tgf:file-of $message)
     (if (== $file None) (TgfReadyV1 $job "[attachment unavailable]")
       (let (TgfFileV1 $kind $id $name $mime $size) $file
         (if (not (tgf:truthy? $id)) (TgfReadyV1 $job (tgf:note $file "no file_id"))
           (let (TgfConfigV1 $base $token $dir $limit) $config
             (if (tgf:over? $size $limit)
               (TgfReadyV1 $job (tgf:note $file (tgf:join ("exceeds " (repr $limit) "b cap, not downloaded"))))
               (if (== $token "") (TgfReadyV1 $job (tgf:note $file "download failed: no bot token configured"))
                 (let $handle (tgf:submit (http:request "GET" (tgf:join ((tgf:method-url $config "getFile") "?file_id=" (tgf:query-value (tgf:str $id))))
                                                        () "" 0 1048576 True (http:timeouts 30000 30000 30000)))
                   (if (== $handle None) (TgfReadyV1 $job (tgf:note $file "download failed: request refused"))
                     (tgf:keep $handle getfile $job $file (tgf:message-id $message))))))))))))
; Whether SIZE is a number above LIMIT: and/or evaluate both sides, so the
; number is tested first.
(= (tgf:over? $size $limit) (if (== (get-type $size) Number) (> $size $limit) False))
(= (tgf:message-id $message) (tgf:str (let $id (tgf:at $message "message_id") (if (== $id None) "x" $id))))

; The downloads that finished since the last collection, each
; (TgfReadyV1 JOB ANNOUNCEMENT); a finished getFile starts its file's
; download, which is collected later.
(= (tgf:collect $config)
   (tgf:collect-each $config
     (collapse (match &tgf (TgfInFlightV1 $handle $stage $job $file $dest) (TgfInFlightV1 $handle $stage $job $file $dest))) ()))
(= (tgf:collect-each $config $flights $ready)
   (if (== $flights ()) $ready
     (let $outcome (tgf:advance $config (car-atom $flights))
       (tgf:collect-each $config (cdr-atom $flights)
         (case $outcome ((None $ready) ($done (cons-atom $done $ready))))))))
(= (tgf:advance $config (TgfInFlightV1 $handle $stage $job $file $dest))
   (case (io:poll $handle)
     (((io:idle) None)
      ((io:event $id $result)
       (let $_ (tgf:drop $handle $stage $job $file $dest) (tgf:finished $config $stage $job $file $dest $result)))
      ($lost (let $_ (tgf:drop $handle $stage $job $file $dest) (TgfReadyV1 $job (tgf:note $file "download failed: no answer")))))))
(= (tgf:finished $config getfile $job $file $message-id $result)
   (case $result
     (((http:response $status $body)
       (if (not (tgf:ok-status? $status)) (TgfReadyV1 $job (tgf:note $file (str:concat "download failed: " (tgf:failure $result))))
         (case (catch (json:parse $body))
           (((JsonObject $pairs)
             (let $remote (tgf:at (sf:get (JsonObject $pairs) "result" (JsonObject ())) "file_path")
               (if (not (tgf:truthy? $remote)) (TgfReadyV1 $job (tgf:note $file "getFile returned no path"))
                 (tgf:fetch $config $job $file $message-id (tgf:str $remote)))))
            ($other (TgfReadyV1 $job (tgf:note $file "download failed: unreadable getFile reply")))))))
      ($other (TgfReadyV1 $job (tgf:note $file (str:concat "download failed: " (tgf:failure $result))))))))
(= (tgf:finished $config file $job $file $dest $result)
   (case $result
     (((http:saved $status $headers $path $bytes)
       (TgfReadyV1 $job (tgf:join ("[attachment saved: " $dest " (" (tgf:label $file) ")]"))))
      ($other (TgfReadyV1 $job (tgf:note $file (str:concat "download failed: " (tgf:failure $result))))))))
; The file's own download, saved as MESSAGE-ID-NAME in the attachments directory.
(= (tgf:fetch $config $job $file $message-id $remote)
   (let* (((TgfConfigV1 $base $token $dir $limit) $config)
          ((TgfFileV1 $kind $id $name $mime $size) $file)
          ($fallback (str:char-slice (if (tgf:truthy? $id) (tgf:str $id) "file") 0 16))
          ($dest (path:join $dir (tgf:join ($message-id "-" (tgf:safe-name (tgf:first-of ($name (tgf:basename $remote))) $fallback)))))
          ($made (fs:make-directories $dir 511))
          ($handle (tgf:submit (http:request "GET" (tgf:file-url $config $remote) () "" 0 $limit True
                                             (http:timeouts 120000 120000 120000) (http:save $dest)))))
     (if (== $handle None) (TgfReadyV1 $job (tgf:note $file "download failed: request refused"))
       (let $kept (tgf:keep $handle file $job $file $dest) None))))

; The handles of downloads in flight.
(= (tgf:in-flight) (collapse (match &tgf (TgfInFlightV1 $handle $stage $job $file $dest) $handle)))

; Downloads LEDGER logged as dispatched and not as ready, newest LIMIT by
; update id, oldest first, each (UPDATE PROVENANCE) with UPDATE as the client
; parses it. A line that is not a JSON object is passed over.
(= (tgf:pending $ledger $limit)
   (if (or (== $ledger "") (not (fs:is-file $ledger))) ()
     (let $records (tgf:attachment-records (str:split "\n" (fs:read-text $ledger)) ())
       (tgf:newest (sort-atom (tgf:open-dispatches $records ())) $limit))))
(= (tgf:attachment-records $lines $kept)
   (if (== $lines ()) (tgf:reverse $kept ())
     (let* (($line (car-atom $lines))
            ($record (if (tgf:contains? $line "attachment_") (tgf:record $line) None)))
       (tgf:attachment-records (cdr-atom $lines) (if (== $record None) $kept (cons-atom $record $kept))))))
(= (tgf:contains? $text $part) (case (str:find $text $part) (((TextNotFound $missing) False) ($at True))))
(= (tgf:record $line)
   (case (catch (json-parse $line)) (((JsonObject $pairs) (JsonObject $pairs)) ($other None))))
; Fold the records in order: a dispatch with its update opens a key (chat id
; and message id), a ready closes it; the open ones, as (UPDATE-ID UPDATE PROVENANCE).
(= (tgf:open-dispatches $records $open)
   (if (== $records ()) (tgf:open-values $open)
     (let* (($record (car-atom $records))
            ($key ((tgf:str (tgf:text-of $record "chat_id")) (tgf:str (tgf:text-of $record "message_id"))))
            ($note (tgf:at $record "note"))
            ($raw (sf:get $record "raw" JsonNull))
            ($rest (tgf:without-key $open $key))
            ($next (if (and (== $note "attachment_dispatched") (tgf:truthy? (tgf:at $record "raw")))
                     (cons-atom ($key (tgf:whole (tgf:at $record "update_id")) $raw (tgf:text-or-empty (tgf:at $record "provenance"))) $rest)
                     (if (== $note "attachment_ready") $rest $open))))
       (tgf:open-dispatches (cdr-atom $records) $next))))
(= (tgf:text-of $record $key) (let $value (tgf:at $record $key) (if (== $value None) "" $value)))
(= (tgf:text-or-empty $value) (if (tgf:truthy? $value) (tgf:str $value) ""))
(= (tgf:whole $value) (if (== (get-type $value) Number) $value 0))
(= (tgf:without-key $open $key)
   (if (== $open ()) ()
     (let* (($entry (car-atom $open)) ($rest (tgf:without-key (cdr-atom $open) $key)))
       (if (== (car-atom $entry) $key) $rest (cons-atom $entry $rest)))))
(= (tgf:open-values $open)
   (if (== $open ()) ()
     (let ($key $uid $raw $provenance) (car-atom $open)
       (cons-atom ($uid $raw $provenance) (tgf:open-values (cdr-atom $open))))))
(= (tgf:newest $sorted $limit)
   (let $extra (- (size-atom $sorted) (if (< $limit 1) 1 $limit))
     (map-atom (tgf:drop-first $sorted $extra) $entry (let ($uid $raw $provenance) $entry ($raw $provenance)))))
(= (tgf:drop-first $items $count)
   (if (or (<= $count 0) (== $items ())) $items (tgf:drop-first (cdr-atom $items) (- $count 1))))
(= (tgf:reverse $items $done) (if (== $items ()) $done (tgf:reverse (cdr-atom $items) (cons-atom (car-atom $items) $done))))

;; ------------------------------------------------------------- uploads

; Send PATH to CHAT with METHOD (sendDocument or sendPhoto), its part FIELD
; (document or photo) named NAME; ALLOWED the configured chats, PRIMARY the
; operator's. (TgfSentV1 CHAT RECORD MESSAGE-ID REPLY) when Telegram took it,
; RECORD the line for the conversation ledger; else the reply alone.
(= (tgf:send $config $method $field $path $name $caption $chat $allowed $primary)
   (let (TgfConfigV1 $base $token $dir $limit) $config
     (if (not (fs:is-file $path)) (str:concat "send failed: no such file: " $path)
       (if (== $token "") "send failed: no bot token configured"
         (if (== $chat "")
           "send failed: no chat to send to — pass one explicitly, e.g. (send-image \"/path.svg\" \"caption\" \"123456789\")"
           (if (and (not (== $allowed ())) (and (not (tgf:member? $chat $allowed)) (not (== $chat $primary))))
             "send failed: target chat is not allowed"
             (let $size (fs:size $path)
               (if (not (== (get-type $size) Number)) (str:concat "send failed: cannot read " $path)
                 (if (> $size (tgf:upload-limit))
                   (tgf:join ("send failed: " $path " is " (repr $size) " bytes; Telegram's limit is 50MB"))
                   (tgf:post $config $method $field $path $name $caption $chat $size))))))))))
(= (tgf:member? $item $items)
   (if (== $items ()) False (if (== (car-atom $items) $item) True (tgf:member? $item (cdr-atom $items)))))
(= (tgf:post $config $method $field $path $name $caption $chat $size)
   (let* (($caption-part (if (== $caption "") ()
                            ((http:field "caption" (str:char-slice (str:join "\n" (str:split "\\n" $caption)) 0 1024)))))
          ($parts (cons-atom (http:field "chat_id" $chat) (tgf:append $caption-part ((http:file $field $path $name)))))
          ($handle (tgf:submit (http:request "POST" (tgf:method-url $config $method) () (cons-atom http:multipart $parts)
                                             0 1048576 False (http:timeouts 120000 120000 120000)))))
     (if (== $handle None) "send failed (request refused)"
       (case (tgf:await $handle)
         (((io:event $id $result) (tgf:sent $result $field $name $caption $chat $size))
          ($other "send failed (no answer)"))))))
; The completion of HANDLE itself (a one-argument io:wait takes a timeout and
; answers any request's completion), waited for in ten-second steps: the
; request's own timeouts bound it.
(= (tgf:await $handle)
   (case (io:wait $handle 10000)
     (((io:idle) (tgf:await $handle))
      ($done $done))))
(= (tgf:append $front $back)
   (if (== $front ()) $back (cons-atom (car-atom $front) (tgf:append (cdr-atom $front) $back))))
(= (tgf:sent $result $field $name $caption $chat $size)
   (case $result
     (((http:response $status $body)
       (let* (($payload (case (catch (json:parse $body)) (((JsonObject $pairs) (JsonObject $pairs)) ($other None))))
              ($ok (and (tgf:ok-status? $status) (if (== $payload None) False (tgf:truthy? (sf:get $payload "ok" False))))))
         (if $ok
           (let $message-id (tgf:at (sf:get $payload "result" (JsonObject ())) "message_id")
             (TgfSentV1 $chat (tgf:join ("[" $field " " $name "] " $caption)) $message-id
                        (tgf:join ("sent " $name " (" (repr $size) " bytes) to chat " $chat " (msg " (tgf:str $message-id) ")"))))
           (tgf:join ("send failed (HTTP " (repr $status) ")"
                      (if (== $payload None) ""
                        (str:concat ": " (str:char-slice (tgf:str (let $d (tgf:at $payload "description") (if (== $d None) "" $d))) 0 160))))))))
      ($other (tgf:join ("send failed (" (tgf:failure $result) ")"))))))

; An SVG drawn as PNG at OUT: True when one of the tools wrote it.
(= (tgf:rasterizers $path $out)
   (let $python (tgf:env "PYTHONHOME" "")
     (tgf:append (if (== $python "") () (((path:join (path:join $python "bin") "cairosvg") $path "-o" $out "--output-width" "1400")))
       (("cairosvg" $path "-o" $out "--output-width" "1400")
        ("rsvg-convert" "-w" "1400" "-o" $out $path)
        ("inkscape" "--export-type=png" "--export-width=1400" (str:concat "--export-filename=" $out) $path)
        ("convert" "-density" "150" $path $out)))))
(= (tgf:rasterize $path $out) (tgf:try-tools (tgf:rasterizers $path $out) $out))
(= (tgf:try-tools $tools $out)
   (if (== $tools ()) False
     (case (catch (proc:run (car-atom $tools) (system:cwd) inherit 60000 65536))
       (((proc:exited 0 $stdout $stderr) (if (fs:is-file $out) True (tgf:try-tools (cdr-atom $tools) $out)))
        ($failed (tgf:try-tools (cdr-atom $tools) $out))))))

; send-image: an image previews inline; an SVG is sent as its PNG drawing,
; named STEM.png, or as a document with a note when nothing can draw it.
(= (tgf:send-image $config $path $caption $chat $allowed $primary)
   (if (not (str:ends-with (str:lower $path) ".svg"))
     (tgf:send $config "sendPhoto" "photo" $path (tgf:basename $path) $caption $chat $allowed $primary)
     (let* (($stem (tgf:stem (tgf:basename $path)))
            ($out (path:join (tgf:env "TMPDIR" "/tmp") (tgf:join ("." $stem "-" (system:uuid4-hex) ".png")))))
       (if (tgf:rasterize $path $out)
         (let* (($result (tgf:send $config "sendPhoto" "photo" $out (str:concat $stem ".png") $caption $chat $allowed $primary))
                ($_ (fs:remove $out)))
           $result)
         (let $_ (if (fs:exists $out) (fs:remove $out) True)
           (tgf:send $config "sendDocument" "document" $path (tgf:basename $path)
                     (str:concat (if (== $caption "") "" (str:concat $caption " ")) "(SVG: no rasterizer here, sent as a file)")
                     $chat $allowed $primary))))))
; "a.b.svg" → "a.b": the name without its last extension.
(= (tgf:stem $name)
   (let $parts (str:split "." $name)
     (if (== (cdr-atom $parts) ()) $name (str:join "." (tgf:but-last $parts)))))
(= (tgf:but-last $items)
   (if (== (cdr-atom $items) ()) () (cons-atom (car-atom $items) (tgf:but-last (cdr-atom $items)))))

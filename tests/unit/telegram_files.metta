; Unit tests of channels/telegram_files.metta against a scripted HTTP peer
; (tests/fixtures/native_http_peer.c); no Python. The expected answers are
; those of the Python it replaces (channels/telegram.py's attachment
; downloads, their recovery, send_file and send_image), with the differences
; the module states. Needs an engine with http:save and http:multipart.
!(import! &self ../native_test_support.metta)
!(import! &self ../../channels/telegram_files.metta)

(= (tfu:home) (nt:required-env "HOME"))
(= (tfu:peer $name) (path:join (path:join (tfu:home) "peer") $name))
(= (tfu:script $name $text) (fs:write (tfu:peer $name) $text))
(= (tfu:count) (str:string->number (str:trim (fs:read-text (tfu:peer "count")))))
; The next request's reply body, by its number.
(= (tfu:next-reply $text) (tfu:script (format-args "response-{}.body" ((+ (tfu:count) 1))) $text))
(= (tfu:request $n $part) (fs:read-text (tfu:peer (format-args "request-{}.{}" ($n $part)))))
(= (tfu:wait-file $path $left)
   (if (fs:exists $path) True (if (<= $left 0) False (let $_ (sleep 0.02) (tfu:wait-file $path (- $left 1))))))
(= (tfu:base) (str:concat "http://127.0.0.1:" (str:trim (fs:read-text (tfu:peer "port")))))
(= (tfu:dir) (path:join (tfu:home) "attachments"))
(= (tfu:config) (TgfConfigV1 (tfu:base) "123456:fixture-token" (tfu:dir) 20971520))
(= (tfu:passed $block) (let $_ (add-atom &self (tfu:block-passed $block)) (println! $block)))
(= (tfu:message $text) (json-parse $text))
; Collect until a download is ready: its announcement.
(= (tfu:ready $left)
   (let $done (tgf:collect (tfu:config))
     (if (not (== $done ())) (case (car-atom $done) (((TgfReadyV1 $job1 $announcement1) $announcement1)))
       (if (<= $left 0) timed-out (let $_ (sleep 0.02) (tfu:ready (- $left 1)))))))
(= (tfu:ready-with $config $left)
   (let $done (tgf:collect $config)
     (if (not (== $done ())) (case (car-atom $done) (((TgfReadyV1 $job2 $announcement2) $announcement2)))
       (if (<= $left 0) timed-out (let $_ (sleep 0.02) (tfu:ready-with $config (- $left 1)))))))
(= (tfu:download $message)
   (case (tgf:dispatch (tfu:config) job (tfu:message $message))
     (((TgfReadyV1 $job3 $announcement3) $announcement3)
      ((TgfPendingV1 $handle) (tfu:ready 1000)))))
(= (tfu:document $id $name $size)
   (str:join "" ("{\"message_id\": 7, \"chat\": {\"id\": -1001, \"type\": \"group\"}, \"document\": {"
                 $id "\"file_name\": \"" $name "\", \"mime_type\": \"application/pdf\", \"file_size\": " $size "}}")))
(= (tfu:clean $text) (not (nt:contains $text "fixture-token")))
(= (tfu:digest $path) (case (fs:sha256-file $path) (((fs:hashed-v1 $hash $info) $hash) ($other $other))))
(= (tfu:sh $command) (nt:exit-ok (proc:run ("sh" "-c" $command) (system:cwd) (nt:environment (tfu:home)) 30000 65536)))

!(let* (($home (tfu:home)) ($root (system:cwd))
        ($peer (path:join $home "native-http-peer"))
        ($_ (nt:exit-ok (proc:run ("cc" "-std=c11" "-O2" "-Wall" "-Wextra" "-Werror" "tests/fixtures/native_http_peer.c" "-o" $peer)
                                  $root (nt:environment $home) 30000 65536)))
        ($_ (fs:make-directories (path:join $home "peer") 448))
        ($_ (tfu:script "status" "200")) ($_ (tfu:script "delay-ms" "0")) ($_ (tfu:script "response.body" "{}"))
        ($server (proc:spawn ($peer (path:join $home "peer")) ((cwd $root) (env (nt:environment $home)) (max-bytes 65536))))
        ($_ (add-atom &self (tfu:server $server)))
        ($listening (tfu:wait-file (tfu:peer "port") 1500))
        ($_ (if $listening True (println! (PEER_NOT_LISTENING $server (proc:poll $server)))))
        ($_ (nt:require peer-listens $listening)))
   (tfu:passed UNIT_TELEGRAM_FILES_PEER_OK))

;; ------------------------------------------------------------- values

!(let* (($document (tfu:message (tfu:document "\"file_id\": \"AgAD7\", " "report.pdf" "1234")))
        ($_ (nt:require document-file
              (nt:equal (tgf:file-of $document) (TgfFileV1 "document" "AgAD7" "report.pdf" "application/pdf" 1234))))
        ($_ (nt:require document-label (nt:equal (tgf:label (tgf:file-of $document)) "document report.pdf, application/pdf, 1234b")))
        ($photo (tfu:message "{\"message_id\": 9, \"photo\": [{\"file_id\": \"s\", \"file_size\": 10}, {\"file_id\": \"l\", \"file_size\": 90}, {\"file_id\": \"m\", \"file_size\": 90}]}"))
        ($_ (nt:require largest-photo-first-of-equals (nt:equal (tgf:file-of $photo) (TgfFileV1 "photo" "l" None "image/jpeg" 90))))
        ($_ (nt:require photo-label (nt:equal (tgf:label (tgf:file-of $photo)) "photo l, image/jpeg, 90b")))
        ($sticker (tfu:message "{\"sticker\": {\"file_id\": \"st\"}, \"photo\": [{\"file_id\": \"p\"}]}"))
        ($_ (nt:require single-kind-before-photo (nt:equal (tgf:label (tgf:file-of $sticker)) "sticker st")))
        ($_ (nt:require no-file (nt:equal (tgf:file-of (tfu:message "{\"photo\": [], \"document\": null}")) None)))
        ($_ (nt:require nameless-label (nt:equal (tgf:label (TgfFileV1 "voice" None None None None)) "voice ?")))
        ($_ (nt:require safe-names
              (nt:equal (map-atom (("../my report (final).pdf" "abc") ("žluťoučký kůň.txt" "x") ("..." "AgAD7") ("__a.b__" "f")
                                   ("dir/sub/" "fb") (None "fb") ("a/b/.hidden" "f")) $case
                          (let ($name $fallback) $case (tgf:safe-name $name $fallback)))
                        ("my_report__final_.pdf" "lu_ou_k__k__.txt" "AgAD7" "a.b" "fb" "fb" "hidden"))))
        ($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13) $k "abcdefghij")))
        ($_ (nt:require safe-name-cut (nt:equal (tgf:safe-name $long "f") (str:char-slice $long 0 120))))
        ($_ (nt:require query-values
              (nt:equal (map-atom ("AgAD-x_1.~" "a b/ž") $t (tgf:query-value $t)) ("AgAD-x_1.~" "a%20b%2F%C5%BE"))))
        ($_ (nt:require stems (nt:equal (map-atom ("diagram.svg" "a.b.svg" "plain") $n (tgf:stem $n)) ("diagram" "a.b" "plain")))))
   (tfu:passed UNIT_TELEGRAM_FILES_VALUES_OK))

;; ------------------------------------------------------------- downloads

!(let* (($_ (tfu:sh (str:concat "head -c 300000 /dev/urandom > " (tfu:peer "file.bin"))))
        ($_ (tfu:sh (str:join "" ("printf 'tail\\000\\r\\n' >> " (tfu:peer "file.bin")))))
        ($first (+ (tfu:count) 1))
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_id\": \"AgAD7\", \"file_path\": \"documents/file_7.bin\"}}"))
        ($_ (tfu:sh (str:join "" ("cp " (tfu:peer "file.bin") " " (tfu:peer (format-args "response-{}.body" ((+ $first 1))))))))
        ($announcement (tfu:download (tfu:document "\"file_id\": \"AgAD7\", " "report.pdf" "1234")))
        ($dest (path:join (tfu:dir) "7-report.pdf"))
        ($_ (nt:require saved-announcement
              (nt:equal $announcement (str:join "" ("[attachment saved: " $dest " (document report.pdf, application/pdf, 1234b)]")))))
        ($_ (nt:require saved-byte-for-byte (nt:equal (tfu:digest $dest) (tfu:digest (tfu:peer "file.bin")))))
        ($_ (nt:require asked-get-file
              (nt:contains (tfu:request $first "headers") "GET /bot123456:fixture-token/getFile?file_id=AgAD7 HTTP")))
        ($_ (nt:require fetched-the-file
              (nt:contains (tfu:request (+ $first 1) "headers") "GET /file/bot123456:fixture-token/documents/file_7.bin HTTP")))
        ($_ (nt:require nothing-in-flight (nt:equal (tgf:in-flight) ())))
        ($_ (nt:require wait-released (nt:equal (collapse (match &self (idle:extra-io $h) $h)) ())))
        ; A photo is named by its remote file.
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_path\": \"photos/file_9.jpg\"}}"))
        ($photo (tfu:download "{\"message_id\": 9, \"photo\": [{\"file_id\": \"s\", \"file_size\": 10}, {\"file_id\": \"l\", \"file_size\": 90}]}"))
        ; Downloads run side by side: neither waits for the other.
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_path\": \"documents/a.bin\"}}"))
        ($a (tgf:dispatch (tfu:config) a (tfu:message (tfu:document "\"file_id\": \"A\", " "a.pdf" "10"))))
        ($b (tgf:dispatch (tfu:config) b (tfu:message (tfu:document "\"file_id\": \"B\", " "b.pdf" "10"))))
        ($_ (nt:require both-in-flight (nt:equal (size-atom (tgf:in-flight)) 2)))
        ($_ (tfu:ready 1000)) ($_ (tfu:ready 1000))
        ($_ (nt:require both-collected (nt:equal (tgf:in-flight) ())))
        ; Past the cap, nothing is left behind; a file announced as larger is not asked for.
        ($small (TgfConfigV1 (tfu:base) "123456:fixture-token" (tfu:dir) 100))
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_path\": \"documents/file_7.bin\"}}"))
        ($_ (tfu:sh (str:join "" ("cp " (tfu:peer "file.bin") " " (tfu:peer (format-args "response-{}.body" ((+ (tfu:count) 2))))))))
        ($capped (case (tgf:dispatch $small job (tfu:message "{\"message_id\": 12, \"document\": {\"file_id\": \"big\", \"file_name\": \"big.bin\"}}"))
                   (((TgfPendingV1 $hc) (tfu:ready-with $small 1000)) ($pc $pc))))
        ($_ (nt:require capped-download-fails
              (nt:equal $capped "[attachment document big.bin — download failed: exceeds 100b cap]")))
        ($_ (nt:require capped-download-leaves-nothing
              (nt:equal (filter-atom (fs:entries (tfu:dir)) $entry (or (str:starts-with $entry "12-") (str:starts-with $entry "."))) ())))
        ($before-big (tfu:count))
        ($_ (nt:require announced-size-over-the-cap
              (nt:equal (case (tgf:dispatch $small job (tfu:message (tfu:document "\"file_id\": \"big\", " "big.bin" "101")))
                          (((TgfReadyV1 $jd $ad) $ad) ($pd $pd)))
                        "[attachment document big.bin, application/pdf, 101b — exceeds 100b cap, not downloaded]")))
        ($_ (nt:require announced-size-asks-nothing (nt:equal (tfu:count) $before-big)))
        ($_ (nt:require photo-saved
              (nt:equal $photo (str:join "" ("[attachment saved: " (path:join (tfu:dir) "9-file_9.jpg") " (photo l, image/jpeg, 90b)]"))))))
   (tfu:passed UNIT_TELEGRAM_FILES_DOWNLOAD_OK))

!(let* (($report (tfu:document "\"file_id\": \"AgAD7\", " "report.pdf" "1234"))
        ($before (tfu:count))
        ($_ (nt:require no-file-id
              (nt:equal (tfu:download (tfu:document "" "report.pdf" "1234")) "[attachment document report.pdf, application/pdf, 1234b — no file_id]")))
        ($_ (nt:require over-the-cap
              (nt:equal (tfu:download (tfu:document "\"file_id\": \"AgAD7\", " "big.mp4" "20971521"))
                        "[attachment document big.mp4, application/pdf, 20971521b — exceeds 20971520b cap, not downloaded]")))
        ($_ (nt:require nothing-to-fetch (nt:equal (tfu:download "{\"text\": \"no file\"}") "[attachment unavailable]")))
        ($_ (nt:require no-token
              (nt:equal (case (tgf:dispatch (TgfConfigV1 (tfu:base) "" (tfu:dir) 20971520) job (tfu:message $report))
                          (((TgfReadyV1 $j4 $a4) $a4) ($p4 $p4)))
                        "[attachment document report.pdf, application/pdf, 1234b — download failed: no bot token configured]")))
        ($_ (nt:require immediate-cases-ask-nothing (nt:equal (tfu:count) $before)))
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_id\": \"AgAD7\"}}"))
        ($_ (nt:require no-file-path
              (nt:equal (tfu:download $report) "[attachment document report.pdf, application/pdf, 1234b — getFile returned no path]")))
        ($_ (tfu:next-reply "<html>not json</html>"))
        ($unreadable (tfu:download $report))
        ($_ (nt:require unreadable-reply
              (nt:equal $unreadable "[attachment document report.pdf, application/pdf, 1234b — download failed: unreadable getFile reply]")))
        ($_ (tfu:script "status" "404"))
        ($missing (tfu:download $report))
        ($_ (nt:require get-file-refused
              (nt:equal $missing "[attachment document report.pdf, application/pdf, 1234b — download failed: HTTP 404]")))
        ($_ (tfu:script "status" "200"))
        ; The file's own request refused: its directory cannot be made.
        ($blocked (path:join (tfu:home) "not-a-directory")) ($_ (fs:write $blocked "x"))
        ($_ (tfu:next-reply "{\"ok\": true, \"result\": {\"file_path\": \"documents/file_7.bin\"}}"))
        ($refused (case (tgf:dispatch (TgfConfigV1 (tfu:base) "123456:fixture-token" (path:join $blocked "sub") 20971520) job (tfu:message $report))
                    (((TgfPendingV1 $h5) (tfu:ready-with (TgfConfigV1 (tfu:base) "123456:fixture-token" (path:join $blocked "sub") 20971520) 1000)) ($p5 $p5))))
        ($_ (nt:require file-request-refused
              (nt:equal $refused "[attachment document report.pdf, application/pdf, 1234b — download failed: request refused]")))
        ; A transport failure: nothing listens at the address.
        ($dead (case (tgf:dispatch (TgfConfigV1 "http://127.0.0.1:1" "123456:fixture-token" (tfu:dir) 20971520) job (tfu:message $report))
                 (((TgfPendingV1 $h6) (tfu:ready-with (TgfConfigV1 "http://127.0.0.1:1" "123456:fixture-token" (tfu:dir) 20971520) 1000)) ($p6 $p6))))
        ($_ (nt:require transport-failure
              (nt:equal $dead "[attachment document report.pdf, application/pdf, 1234b — download failed: transport error 7]")))
        ($_ (nt:require no-token-in-any-note
              (and (tfu:clean $unreadable) (and (tfu:clean $missing) (tfu:clean $dead))))))
   (tfu:passed UNIT_TELEGRAM_FILES_FAILURES_OK))

;; ------------------------------------------------------------- the ledger's open dispatches

(= (tfu:record $uid $chat $message-id $note $provenance)
   (str:join "" ("{\"received_at\":\"2026-11-13T10:00:00+0100\",\"update_id\":" $uid ",\"kind\":\"message\",\"note\":\"" $note
                 "\",\"chat_id\":\"" $chat "\",\"message_id\":" $message-id ",\"text\":null,\"caption\":\"c\","
                 "\"raw\":{\"update_id\":" $uid ",\"message\":{\"message_id\":" $message-id ",\"chat\":{\"id\":" $chat
                 "},\"document\":{\"file_id\":\"f" $message-id "\"}}}"
                 (if (== $provenance "") "" (str:join "" (",\"provenance\":\"" $provenance "\""))) "}\n")))
!(let* (($ledger (path:join (tfu:home) "updates.jsonl"))
        ($_ (fs:write $ledger
              (str:join "" ((tfu:record "5" "42" "1" "attachment_dispatched" "")
                            "not json at all, attachment_dispatched\n"
                            (tfu:record "6" "42" "2" "attachment_dispatched" "trusted friend")
                            (tfu:record "6" "42" "2" "attachment_ready" "")
                            (tfu:record "8" "-77" "3" "attachment_dispatched" "")
                            (tfu:record "7" "42" "4" "attachment_dispatched" "")
                            (tfu:record "9" "42" "5" "own_send" "")))))
        ($pending (tgf:pending $ledger 100))
        ($_ (nt:require open-dispatches-oldest-first
              (nt:equal (map-atom $pending $entry (let ($update $provenance) $entry ((tgf:at $update "update_id") $provenance)))
                        ((5 "") (7 "") (8 "")))))
        ($_ (nt:require raw-update-as-parsed
              (case (car-atom $pending) ((((JsonObject $pairs) "") (nt:equal (tgf:at (JsonObject $pairs) "update_id") 5)) ($o False)))))
        ($_ (nt:require newest-limit
              (nt:equal (map-atom (tgf:pending $ledger 2) $entry (let ($update $provenance) $entry (tgf:at $update "update_id"))) (7 8))))
        ($_ (fs:write $ledger (str:concat (fs:read-text $ledger) (tfu:record "10" "42" "6" "attachment_dispatched" "a friend"))))
        ($_ (nt:require provenance-kept
              (nt:equal (map-atom (tgf:pending $ledger 1) $entry (let ($update $provenance) $entry $provenance)) ("a friend"))))
        ($_ (nt:require no-ledger (nt:equal (tgf:pending (path:join (tfu:home) "absent.jsonl") 100) ())))
        ($_ (nt:require no-ledger-path (nt:equal (tgf:pending "" 100) ()))))
   (tfu:passed UNIT_TELEGRAM_FILES_RECOVERY_OK))

;; ------------------------------------------------------------- uploads

(= (tfu:send $path $caption $chat)
   (tgf:send (tfu:config) "sendDocument" "document" $path (tgf:basename $path) $caption $chat ("-1001" "42") "42"))
!(let* (($file (path:join (tfu:home) "notes ž.txt"))
        ($_ (fs:write $file "first line\nžluťoučký kůň\n"))
        ($_ (tfu:script "response.body" "{\"ok\": true, \"result\": {\"message_id\": 42}}"))
        ($at (+ (tfu:count) 1))
        ($sent (tfu:send $file "line one\\nline two" "-1001"))
        ($_ (nt:require sent
              (nt:equal $sent (TgfSentV1 "-1001" "[document notes ž.txt] line one\\nline two" 42
                                         "sent notes ž.txt (31 bytes) to chat -1001 (msg 42)"))))
        ($body (tfu:request $at "body"))
        ($_ (nt:require posted-to-send-document
              (nt:contains (tfu:request $at "headers") "POST /bot123456:fixture-token/sendDocument HTTP")))
        ($_ (nt:require multipart-chat (nt:contains $body "name=\"chat_id\"\r\n\r\n-1001\r\n")))
        ($_ (nt:require multipart-caption-lines (nt:contains $body "name=\"caption\"\r\n\r\nline one\nline two\r\n")))
        ($_ (nt:require multipart-file
              (and (nt:contains $body "name=\"document\"; filename=\"notes ž.txt\"") (nt:contains $body "first line\nžluťoučký kůň\n"))))
        ($_ (nt:require fields-in-order
              (< (case (str:find $body "name=\"chat_id\"") (($n12 $n12))) (case (str:find $body "name=\"caption\"") (($m13 $m13))))))
        ; A slow reply is still awaited, and is this upload's own: a timer that
        ; completes first is another request's answer.
        ($_ (tfu:script "delay-ms" "300"))
        ($timer (case (io:submit (timer:after 50)) (((io:pending $tid) $tid))))
        ($slow (tfu:send $file "slow" "42"))
        ($_ (tfu:script "delay-ms" "0"))
        ($_ (nt:require other-completion-left-alone
              (case (io:poll $timer) (((io:event $tid2 $fired) True) ($tother False)))))
        ($_ (nt:require slow-reply-awaited
              (case $slow (((TgfSentV1 "42" "[document notes ž.txt] slow" 42 $ts) True) ($os False)))))
        ; No caption, no caption part; a caption is cut at 1024 characters.
        ($bare-at (+ (tfu:count) 1))
        ($_ (tfu:send $file "" "42"))
        ($_ (nt:require no-caption-part (not (nt:contains (tfu:request $bare-at "body") "name=\"caption\""))))
        ($long-at (+ (tfu:count) 1))
        ($long (str:join "" (map-atom (1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95 96 97 98 99 100 101 102 103) $k "ž123456789")))
        ($_ (tfu:send $file $long "42"))
        ($_ (nt:require caption-cut
              (nt:contains (tfu:request $long-at "body") (str:concat (str:char-slice $long 0 1024) "\r\n--"))))
        ; Telegram's refusal, and an answer that is no JSON.
        ($_ (tfu:script "status" "400"))
        ($_ (tfu:script "response.body" "{\"ok\": false, \"error_code\": 400, \"description\": \"Bad Request: chat not found\"}"))
        ($_ (nt:require refused-with-description (nt:equal (tfu:send $file "" "42") "send failed (HTTP 400): Bad Request: chat not found")))
        ($_ (tfu:script "status" "502")) ($_ (tfu:script "response.body" "bad gateway"))
        ($_ (nt:require refused-without-json (nt:equal (tfu:send $file "" "42") "send failed (HTTP 502)")))
        ($_ (tfu:script "status" "200")) ($_ (tfu:script "response.body" "{\"ok\": false}"))
        ($_ (nt:require not-ok-is-refused (nt:equal (tfu:send $file "" "42") "send failed (HTTP 200): ")))
        ($_ (tfu:script "response.body" "{\"ok\": true, \"result\": {\"message_id\": 42}}"))
        ; The checks before any request.
        ($before (tfu:count))
        ($_ (nt:require missing-file
              (nt:equal (tfu:send (path:join (tfu:home) "absent.txt") "" "42") (str:concat "send failed: no such file: " (path:join (tfu:home) "absent.txt")))))
        ($_ (nt:require no-chat
              (nt:equal (tfu:send $file "" "")
                        "send failed: no chat to send to — pass one explicitly, e.g. (send-image \"/path.svg\" \"caption\" \"123456789\")")))
        ($_ (nt:require chat-not-allowed (nt:equal (tfu:send $file "" "-5") "send failed: target chat is not allowed")))
        ($_ (nt:require primary-always-allowed
              (case (tgf:send (tfu:config) "sendDocument" "document" $file "n.txt" "" "77" ("-1001") "77") (((TgfSentV1 $c7 $r7 $i7 $t7) True) ($o7 False)))))
        ($_ (nt:require no-allowed-list-allows-any
              (case (tgf:send (tfu:config) "sendDocument" "document" $file "n.txt" "" "-5" () "") (((TgfSentV1 $c8 $r8 $i8 $t8) True) ($o8 False)))))
        ($after (tfu:count))
        ($_ (nt:require checks-ask-nothing (nt:equal $after (+ $before 2))))
        ($_ (nt:require no-token
              (nt:equal (tgf:send (TgfConfigV1 (tfu:base) "" (tfu:dir) 20971520) "sendDocument" "document" $file "n.txt" "" "42" () "")
                        "send failed: no bot token configured")))
        ($big (path:join (tfu:home) "big.bin"))
        ($_ (nt:exit-ok (proc:run ("truncate" "-s" "52428801" $big) (system:cwd) (nt:environment (tfu:home)) 10000 4096)))
        ($_ (nt:require over-telegram-limit
              (nt:equal (tfu:send $big "" "42") (str:concat "send failed: " (str:concat $big " is 52428801 bytes; Telegram's limit is 50MB")))))
        ($_ (fs:remove $big))
        ($_ (nt:require transport-failure
              (nt:equal (tgf:send (TgfConfigV1 "http://127.0.0.1:1" "123456:fixture-token" (tfu:dir) 20971520) "sendDocument" "document" $file "n.txt" "" "42" () "")
                        "send failed (transport error 7)"))))
   (tfu:passed UNIT_TELEGRAM_FILES_UPLOAD_OK))

; send-image: an image as a photo; an SVG drawn as PNG by the first tool that
; can, sent under its own stem, the drawing removed; without one, a document.
!(let* (($image (path:join (tfu:home) "picture.png")) ($_ (fs:write $image "PNG-ish"))
        ($at (+ (tfu:count) 1))
        ($_ (nt:require photo-sent
              (case (tgf:send-image (tfu:config) $image "" "42" () "42") (((TgfSentV1 $c9 "[photo picture.png] " 42 $t9) True) ($o9 False)))))
        ($_ (nt:require photo-part
              (and (nt:contains (tfu:request $at "headers") "/sendPhoto HTTP")
                   (nt:contains (tfu:request $at "body") "name=\"photo\"; filename=\"picture.png\""))))
        ($svg (path:join (tfu:home) "diagram.v2.svg")) ($_ (fs:write $svg "<svg/>"))
        ($_ (fs:make-directories (system:getenv-or-default "TMPDIR" "") 448))
        ; The Python runtime's cairosvg is tried first (a scripted one here).
        ($cairosvg (path:join (system:getenv-or-default "PYTHONHOME" "") "bin/cairosvg"))
        ($_ (fs:make-directories (path:dirname $cairosvg) 448))
        ($_ (fs:write $cairosvg "#!/bin/sh\n# cairosvg IN -o OUT --output-width 1400\nprintf 'drawn %s' \"$1\" > \"$3\"\necho \"cairosvg $*\" >> \"$HOME/rasterized\"\n"))
        ($_ (fs:set-mode $cairosvg 448))
        ($svg-at (+ (tfu:count) 1))
        ($drawn (tgf:send-image (tfu:config) $svg "a diagram" "42" () "42"))
        ($_ (nt:require svg-drawn-and-sent
              (case $drawn (((TgfSentV1 "42" "[photo diagram.v2.png] a diagram" 42 $t10) (str:starts-with $t10 "sent diagram.v2.png (")) ($o10 False)))))
        ($_ (nt:require svg-as-photo-under-its-stem
              (and (nt:contains (tfu:request $svg-at "headers") "/sendPhoto HTTP")
                   (and (nt:contains (tfu:request $svg-at "body") "name=\"photo\"; filename=\"diagram.v2.png\"")
                        (nt:contains (tfu:request $svg-at "body") (str:concat "drawn " $svg))))))
        ($_ (nt:require drawn-by-cairosvg-at-width (nt:contains (fs:read-text (path:join (tfu:home) "rasterized")) "--output-width 1400")))
        ($_ (nt:require drawing-removed
              (nt:equal (filter-atom (fs:entries (system:getenv-or-default "TMPDIR" "")) $n (str:starts-with $n ".diagram.v2-")) ())))
        ($_ (fs:remove $cairosvg))
        ; Then the command-line tools, rsvg-convert first among them.
        ($tool (path:join (tfu:home) "bin/rsvg-convert"))
        ($_ (fs:make-directories (path:dirname $tool) 448))
        ($_ (fs:write $tool "#!/bin/sh\n# rsvg-convert -w 1400 -o OUT IN\nprintf 'drawn %s' \"$5\" > \"$4\"\necho \"rsvg $*\" >> \"$HOME/rasterized\"\n"))
        ($_ (fs:set-mode $tool 448))
        ($_ (nt:require drawn-by-rsvg
              (case (tgf:send-image (tfu:config) $svg "" "42" () "42") (((TgfSentV1 "42" "[photo diagram.v2.png] " 42 $t11) True) ($o11 False)))))
        ($_ (nt:require rsvg-at-width (nt:contains (fs:read-text (path:join (tfu:home) "rasterized")) "rsvg -w 1400 -o ")))
        ($_ (fs:remove $tool))
        ($plain-at (+ (tfu:count) 1))
        ($_ (tgf:send-image (tfu:config) $svg "a diagram" "42" () "42"))
        ($_ (nt:require svg-without-a-tool-as-document
              (and (nt:contains (tfu:request $plain-at "headers") "/sendDocument HTTP")
                   (and (nt:contains (tfu:request $plain-at "body") "name=\"document\"; filename=\"diagram.v2.svg\"")
                        (nt:contains (tfu:request $plain-at "body") "a diagram (SVG: no rasterizer here, sent as a file)"))))))
   (tfu:passed UNIT_TELEGRAM_FILES_IMAGES_OK))

!(let $blocks (collapse (match &self (tfu:block-passed $block) $block))
   (if (== (size-atom $blocks) 7) (println! UNIT_TELEGRAM_FILES_OK)
     (println! (UNIT_TELEGRAM_FILES_INCOMPLETE $blocks))))

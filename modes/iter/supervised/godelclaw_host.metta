;; godelclaw's host for Iter in a supervised agent, in MeTTa.
;;
;; The vendored iter.metta and utils.metta run unchanged. This module is
;; imported by the supervised iter_llm.metta, so it is loaded in the loop and
;; in every worker after utils.metta's JSON helpers (SWI json terms). It
;; defines no top-level effects and imports no library that utils.metta
;; collides with (CeTTa's json library defines json-parse too).
;;
;; It provides:
;;   - the operator gate: the lifecycle latch and the recycle witnesses of
;;     src/lifecycle.py and src/engine_modes.py, read from the same files;
;;   - records in runtime.jsonl and active-loop.json;
;;   - the Telegram channel over the durable CWP service: receive, the
;;     acknowledgement of saved input, durable send intents and routes;
;;   - the model request through godelclaw's native provider, run as a
;;     CeTTa process of its own (provider.metta).
;;
;; Input is acknowledged when a provider turn starts whose saved history
;; contains it: receive keeps no delivery record, so neither a failed save nor
;; a worker killed mid-receive can acknowledge input the loop never kept.
!(import! &self (library cwp))
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library math))
!(import! &self (library system))
!(import! &self (library proc))

;; --------------------------------------------------------------------
;; Values
;; --------------------------------------------------------------------
(= (host:env $name $default) (system:getenv-or-default $name $default))
(= (host:string? $value) (== (get-type $value) String))
(= (host:number? $value) (== (get-type $value) Number))

;; A JSON value read by utils.metta: objects are json([key=value, ...]),
;; arrays are lists, null, true and false are symbols.
(= (host:get $object $key) (if (json-is-object $object) (json-get $object $key null) null))

;; Python's truth of a JSON value.
(= (host:truthy $value)
   (if (host:string? $value) (not (== $value ""))
     (if (host:number? $value) (not (== $value 0))
       (case $value
         ((null False) (false False) (true True) (() False)
          ($other (if (json-is-object $other) (not (== (json-pairs $other) ())) True)))))))

;; value or {}, value or other
(= (host:object $value) (if (host:truthy $value) $value (json-object ())))
(= (host:either $value $other) (if (host:truthy $value) $value $other))

;; str() of a JSON scalar, "" for null.
(= (host:text $value)
   (if (host:string? $value) $value
     (if (host:number? $value) (str:number->string $value)
       (case $value
         ((null "") (true "True") (false "False") ($other (json-text $other)))))))

(= (host:join $separator $items) (join-text $separator $items))
(= (host:replace $text $old $new) (str:join $new (str:split $old $text)))

;; json.dumps(text, ensure_ascii=False): SWI writes "</" as "<\/".
(= (host:quote $text) (host:replace (json-text $text) "<\\/" "</"))

(= (host:zeros $digits $width)
   (if (< (str:char-length $digits) $width) (host:zeros (str:concat "0" $digits) $width) $digits))

;; --------------------------------------------------------------------
;; Files
;; --------------------------------------------------------------------
(= (host:parent $path) (progn (translatePredicate (file_directory_name $path $parent)) $parent))
(= (host:base $path) (progn (translatePredicate (file_base_name $path $base)) $base))
(= (host:ok? $value)
   (if (not (== (get-metatype $value) Expression)) True
     (if (== $value ()) True
       (let $head (car-atom $value) (not (or (== $head fs:failed) (== $head Error)))))))

(= (host:sync-path $path)
   (case (catch (fs:open-read $path))
     (((NativeHandle $kind $id) (let $synced (fs:sync (NativeHandle $kind $id)) (let $closed (fs:close (NativeHandle $kind $id)) $synced)))
      ($other $other))))

;; Write a whole file: a temporary in the same directory, fsync, rename,
;; fsync of the directory. Answers True or the failure.
(= (host:write-durable $path $text)
   (let* (($directory (host:parent $path))
          ($made (fs:make-directories $directory 448))
          ($temporary (str:join "" ($directory "/." (host:base $path) "." (str:number->string (system:pid))
                                   "." (str:number->string (system:monotonic-ns)) ".tmp")))
          ($owner (fs:open-write $temporary ("create" "exclusive") 384)))
     (if (not (host:ok? $owner)) $owner
       (let* (($written (fs:write-all $owner $text))
              ($synced (fs:sync $owner))
              ($closed (fs:close $owner)))
         (if (and (host:ok? $written) (host:ok? $synced))
           (let $renamed (fs:rename $temporary $path)
             (if (host:ok? $renamed) (let $dir (host:sync-path $directory) True)
               (let $removed (catch (fs:remove $temporary)) $renamed)))
           (let $removed (catch (fs:remove $temporary)) $written))))))

(= (host:append-durable $path $text)
   (let $owner (fs:open-write $path ("append" "create") 384)
     (if (not (host:ok? $owner)) $owner
       (let* (($written (fs:write-all $owner $text)) ($synced (fs:sync $owner)) ($closed (fs:close $owner)))
         $written))))

(= (host:read-json $path $default)
   (if (file-exists $path)
     (case (collapse (catch (once (json-parse (read-text $path)))))
       ((($value) (case $value (((Error $a $b) $default) ((Error $a) $default) ($ok $ok))))
        ($none $default)))
     $default))

;; sorted names ending in $suffix in a directory
(= (host:files $directory $suffix)
   (if (directory-exists $directory)
     (sort-atom (filter-atom (progn (translatePredicate (directory_files $directory $names)) $names) $name
                             (succeeds (collapse (progn (translatePredicate (string_concat $stem $suffix $name)) yes)))))
     ()))

;; --------------------------------------------------------------------
;; Places
;; --------------------------------------------------------------------
(= (host:mode) (host:env "METTACLAW_ACTIVE_LOOP_MODE" "iter"))
(= (host:runtime) (host:env "METTACLAW_MODE_RUNTIME" "."))
(= (host:path $directory $name) (str:concat $directory (str:concat "/" $name)))
(= (host:runtime-file $name) (host:path (host:runtime) $name))
(= (host:state-home) (host:env "XDG_STATE_HOME" (system:expanduser "~/.local/state")))

;; mode_channel.state()
(= (host:state)
   (let $explicit (host:env "METTACLAW_MODE_STATE_ROOT" "")
     (if (not (== $explicit "")) $explicit
       (str:concat (host:parent (host:env "METTACLAW_ENGINE_STATE_PATH" "")) "/modes"))))
(= (host:state-file $name) (host:path (host:state) $name))

;; --------------------------------------------------------------------
;; Records (runtime.jsonl; event metadata only)
;; --------------------------------------------------------------------
(= (host:record $kind $pairs)
   (let* (($object (json-object (cons-atom (json-pair time (system:wall-time))
                                  (cons-atom (json-pair kind $kind)
                                    (cons-atom (json-pair mode (host:mode)) $pairs)))))
          ($line (str:concat (json-text $object) "\n")))
     (let $written (catch (fs:append-text (host:runtime-file "runtime.jsonl") $line)) True)))

;; loop_modes.record_active
(= (host:active-loop-path)
   (let $engine (host:env "METTACLAW_ENGINE_STATE_PATH" "")
     (if (not (== $engine "")) (str:concat (host:parent $engine) "/active-loop.json")
       (host:path (host:path (host:state-home) (host:env "METTACLAW_INSTANCE" "default")) "active-loop.json"))))
(= (host:record-active)
   (let* (($pairs (cons-atom (json-pair mode (host:canonical-mode (host:mode)))
                    (cons-atom (json-pair pid (system:pid))
                      (cons-atom (json-pair started_at (system:wall-time))
                        (cons-atom (json-pair state_directory (host:runtime)) ())))))
          ($text (json-text (json-object $pairs))))
     (host:write-durable (host:active-loop-path) $text)))

;; runtime_host.recover_outbox: intents left by an older adapter keep their key.
(= (host:recover-outbox)
   (map-atom (host:files (host:runtime-file "outbox") ".json") $name
     (let* (($path (host:path (host:runtime-file "outbox") $name))
            ($item (host:read-json $path null)))
       (if (host:string? (host:get $item key))
         (let $stored (host:write-durable (host:path (host:state-file "channel-outbox") (str:concat (host:text (host:get $item key)) ".json"))
                                          (json-text $item))
           (if (== $stored True) (catch (fs:remove $path)) $stored))
         False))))

;; --------------------------------------------------------------------
;; Operator gate: src/lifecycle.py, src/engine_modes.py, src/loop_modes.py
;; --------------------------------------------------------------------
(= (host:lifecycle-path)
   (let $configured (host:env "METTACLAW_LIFECYCLE_PATH" "")
     (if (not (== $configured "")) $configured
       (host:path (host:path (host:state-home) (host:env "METTACLAW_INSTANCE" "pettaclaw-godel")) "lifecycle.json"))))

;; lifecycle.cognition_enabled: exactly {"schema": 1, "state": "running"}
(= (host:cognition-enabled?)
   (let $value (host:read-json (host:lifecycle-path) null)
     (if (not (json-is-object $value)) False
       (and (== (size-atom (json-pairs $value)) 2)
            (and (host:one? (json-get $value schema null))
                 (== (json-get $value state null) "running"))))))
(= (host:one? $value) (if (host:number? $value) (== $value 1) (== $value true)))

;; str(name or "").strip().lower(), then the module's aliases
(= (host:lower $text) (progn (translatePredicate (string_lower $text $lower)) $lower))
(= (host:clean-name $value) (host:lower (strip (if (host:truthy $value) (host:text $value) ""))))
(= (host:engine-alias $name) (case $name (("swi" "petta") ("swi-petta" "petta") ($other $other))))
(= (host:engine? $name) (or (== $name "petta") (== $name "cetta")))
(= (host:mode-alias $name)
   (case $name (("agent" "godelclaw") ("default" "godelclaw") ("generic" "godelclaw")
                ("claw23" "godelclaw") ("iter-coding" "coding") ($other $other))))
(= (host:mode? $name)
   (or (== $name "godelclaw") (or (== $name "coding") (or (== $name "iter") (== $name "omega")))))
(= (host:canonical-mode $value) (host:mode-alias (host:clean-name $value)))

(= (host:engine-path)
   (let $configured (host:env "METTACLAW_ENGINE_STATE_PATH" "")
     (if (not (== $configured "")) $configured
       (host:path (host:path (host:state-home) (host:env "METTACLAW_INSTANCE" "default")) "engine"))))
(= (host:first-line $text) (car-atom (str:split "\n" $text)))

;; engine_modes.selected_engine / active_engine
(= (host:selected-engine)
   (let* (($fallback0 (host:engine-alias (host:clean-name (host:env "METTACLAW_ENGINE" "petta"))))
          ($fallback (if (host:engine? $fallback0) $fallback0 "petta"))
          ($path (host:engine-path))
          ($selected (if (file-exists $path)
                       (case (collapse (catch (once (read-text $path))))
                         ((($text) (if (host:string? $text) (host:engine-alias (host:clean-name (host:first-line $text))) $fallback))
                          ($none $fallback)))
                       $fallback)))
     (if (host:engine? $selected) $selected "petta")))
(= (host:active-engine)
   (let $active (host:engine-alias (host:clean-name (host:env "METTACLAW_ACTIVE_ENGINE" (host:env "METTACLAW_ENGINE" "petta"))))
     (if (host:engine? $active) $active "petta")))

;; loop_modes.current_mode / process_mode
(= (host:current-mode)
   (let* (($data (host:read-json (host:env "METTACLAW_LOOP_MODE_PATH" "memory/loop_mode.json") null))
          ($mode (if (json-is-object $data) (host:canonical-mode (json-get $data mode "godelclaw")) "godelclaw")))
     (if (host:mode? $mode) $mode "godelclaw")))
(= (host:process-mode)
   (let $active (host:canonical-mode (host:env "METTACLAW_ACTIVE_LOOP_MODE" ""))
     (if (host:mode? $active) $active (host:current-mode))))

;; engine_modes.recycle_requested: three independent witnesses
(= (host:recycle-requested?)
   (let $flag (host:env "METTACLAW_RECYCLE_REQUEST_PATH" "")
     (if (and (not (== $flag "")) (file-exists $flag)) True
       (if (and (not (== (host:env "METTACLAW_ACTIVE_ENGINE" "") ""))
                (not (== (host:selected-engine) (host:active-engine)))) True
         (and (not (== (host:env "METTACLAW_ACTIVE_LOOP_MODE" "") ""))
              (not (== (host:current-mode) (host:process-mode))))))))

(= (host:hold-input?) (or (host:recycle-requested?) (not (host:cognition-enabled?))))

(= (host:recycle-exit)
   (progn (host:record "recycle" ())
          (println! "[mode-runtime] recycle requested; exiting")
          (system:exit 0)))

;; process_supervision's progress file, refreshed by proc:run together with
;; the service watchdog.
(= (host:progress-path)
   (let $explicit (host:env "METTACLAW_PROGRESS_PATH" "")
     (if (not (== $explicit "")) $explicit
       (let $engine (host:env "METTACLAW_ENGINE_STATE_PATH" "")
         (if (== $engine "") "" (str:concat (host:parent $engine) "/progress.json"))))))
(= (host:watchdog)
   (let* (($progress (host:progress-path))
          ($options (proc:options "separate" $progress))
          ($observed (catch (proc:run ("true") "." inherit 10000 0 $options))))
     (system:wall-time)))

;; The wait while cognition is stopped runs in a short process of its own
;; (godelclaw_pause.metta): it exits 0 when cognition is enabled again and 3
;; when a recycle is requested.
(= (host:pause-child $last-ping)
   (if (host:recycle-requested?) (system:exit 3)
     (if (host:cognition-enabled?) (system:exit 0)
       (let $ping (if (>= (- (system:wall-time) $last-ping) 30) (host:watchdog) $last-ping)
         (progn (translatePredicate (sleep 1))
                (cut)
                (host:pause-child $ping))))))

(= (host:cetta) (progn (translatePredicate (read_link "/proc/self/exe" $link $target)) $target))
(= (host:exit-code $status)
   (case (json-list $status) (((exit $code) $code) ($other -1))))

(= (host:pause)
   (let* (($cetta (host:cetta))
          ($argv (cons-atom "--lang" (cons-atom "petta" (cons-atom "--no-results"
                   (cons-atom "utils.metta" (cons-atom "godelclaw_pause.metta" ()))))))
          ($options (cons-atom (Predicate (stdin null)) (cons-atom (Predicate (process $p)) ())))
          ($pid (progn (translatePredicate (process_create $cetta $argv $options)) $p))
          ($status (progn (translatePredicate (process_wait $pid $s)) $s))
          ($code (host:exit-code $status)))
     (if (== $code 3) (host:recycle-exit)
       (translatePredicate (throw (iter_gate (resumed $code)))))))

;; Admit a provider turn, exit for a recycle, or wait out a stop and then
;; restart the iteration so that it receives first.
(= (host:gate)
   (if (host:recycle-requested?) (host:recycle-exit)
     (if (host:cognition-enabled?) admitted (host:pause))))

;; --------------------------------------------------------------------
;; Telegram: rendering, as channels/telegram.py's _format_message
;; --------------------------------------------------------------------
(= (host:compact $value $limit)
   (let $flat (str:join " " (str:split-whitespace (host:text $value)))
     (if (> (str:char-length $flat) $limit)
       (str:concat (str:char-slice $flat 0 (- $limit 1)) "…")
       $flat)))
(= (host:field $key $value)
   (let $text (host:compact $value 160)
     (if (== $text "") "" (str:concat $key (str:concat "=" (host:quote $text))))))
(= (host:flag $key $truth) (str:concat $key (if $truth "=true" "=false")))
(= (host:add $header $part) (if (== $part "") $header (str:concat $header (str:concat " " $part))))

(= (host:display-user $user)
   (if (not (host:truthy $user)) ""
     (let $username (host:get $user username)
       (if (host:truthy $username) (str:concat "@" (host:text $username))
         (host:text (host:either (host:get $user first_name) (host:text (host:get $user id))))))))
(= (host:display-chat $chat)
   (if (not (host:truthy $chat)) ""
     (let $title (host:get $chat title)
       (if (host:truthy $title) (host:text $title)
         (let $username (host:get $chat username)
           (if (host:truthy $username) (str:concat "@" (host:text $username))
             (host:text (host:either (host:get $chat first_name) (host:text (host:get $chat id))))))))))
(= (host:excerpt $message)
   (if (not (host:truthy $message)) ""
     (host:compact (host:either (host:get $message text) (host:either (host:get $message caption) "")) 180)))

;; Telegram's send time in the local zone, "" for no time.
(= (host:clock $epoch)
   (if (not (and (host:number? $epoch) (host:truthy $epoch))) ""
     (case (catch (system:format-time "%Y-%m-%d %H:%M:%S" (math:trunc $epoch) "local"))
       (((Error $call $why) "") ($text $text)))))

(= (host:forward-fields $message)
   (let $origin (host:object (host:get $message forward_origin))
     (if (host:truthy $origin)
       (let* (($type (host:text (host:get $origin type)))
              ($f1 (host:add (host:flag "forwarded" True) (host:field "forward_type" $type)))
              ($f2 (if (== $type "user")
                     (let $user (host:object (host:get $origin sender_user))
                       (host:add (host:add $f1 (host:field "forward_from" (host:display-user $user)))
                                 (host:field "forward_from_id" (host:get $user id))))
                     (if (== $type "hidden_user")
                       (host:add $f1 (host:field "forward_from" (host:get $origin sender_user_name)))
                       (if (or (== $type "chat") (== $type "channel"))
                         (let $chat (host:object (host:either (host:get $origin sender_chat) (host:get $origin chat)))
                           (host:add (host:add $f1 (host:field "forward_from_chat" (host:display-chat $chat)))
                                     (host:field "forward_from_chat_id" (host:get $chat id))))
                         $f1)))))
         (host:add (host:add $f2 (host:field "forward_message_id" (host:get $origin message_id)))
                   (host:field "forward_signature" (host:get $origin author_signature))))
       (let* (($user (host:object (host:get $message forward_from)))
              ($chat (host:object (host:get $message forward_from_chat)))
              ($name (host:get $message forward_sender_name)))
         (if (or (host:truthy $user) (or (host:truthy $chat) (host:truthy $name)))
           (let* (($g1 (host:add (host:flag "forwarded" True)
                                 (host:field "forward_from" (host:either (host:display-user $user) $name))))
                  ($g2 (host:add $g1 (host:field "forward_from_id" (host:get $user id))))
                  ($g3 (host:add $g2 (host:field "forward_from_chat" (host:display-chat $chat))))
                  ($g4 (host:add $g3 (host:field "forward_from_chat_id" (host:get $chat id)))))
             (host:add $g4 (host:field "forward_signature" (host:get $message author_signature))))
           (host:flag "forwarded" False))))))

;; external_reply.origin reads as JSON here (channels/telegram.metta does the
;; same); channels/telegram.py shows a Python dict.
(= (host:reply-fields $message)
   (let* (($reply (host:object (host:get $message reply_to_message)))
          ($external (host:object (host:get $message external_reply)))
          ($quote (host:object (host:get $message quote)))
          ($r1 (if (host:truthy $reply)
                 (let* (($sender (host:object (host:get $reply from)))
                        ($chat (host:object (host:get $reply chat)))
                        ($s1 (host:add (host:flag "is_reply" True) (host:field "reply_to_message_id" (host:get $reply message_id))))
                        ($s2 (host:add $s1 (host:field "reply_to_from" (host:display-user $sender))))
                        ($s3 (host:add $s2 (host:field "reply_to_from_id" (host:get $sender id))))
                        ($s4 (host:add $s3 (host:field "reply_to_chat" (host:display-chat $chat)))))
                   (host:add $s4 (host:field "reply_to_excerpt" (host:excerpt $reply))))
                 (host:flag "is_reply" False)))
          ($r2 (if (host:truthy $external)
                 (host:add (host:add $r1 (host:field "external_reply_origin" (host:get $external origin)))
                           (host:field "external_reply_excerpt" (host:excerpt $external)))
                 $r1)))
     (if (host:truthy $quote) (host:add $r2 (host:field "quote" (host:get $quote text))) $r2)))

(= (host:format-message $update $kind $message $text)
   (let* (($chat (host:object (host:get $message chat)))
          ($sender (host:object (host:get $message from)))
          ($sender-chat (host:object (host:get $message sender_chat)))
          ($via (host:object (host:get $message via_bot)))
          ($h01 (host:add "telegram" (host:field "update_id" (host:get $update update_id))))
          ($h02 (host:add $h01 (host:field "kind" $kind)))
          ($h03 (host:add $h02 (host:field "chat_id" (host:get $chat id))))
          ($h04 (host:add $h03 (host:field "chat_type" (host:get $chat type))))
          ($h05 (host:add $h04 (host:field "chat_title" (host:display-chat $chat))))
          ($h06 (host:add $h05 (host:field "message_id" (host:get $message message_id))))
          ($h07 (host:add $h06 (host:field "sent_at" (host:clock (host:get $message date)))))
          ($h08 (host:add $h07 (host:field "edited_at" (host:clock (host:get $message edit_date)))))
          ($h09 (host:add $h08 (host:field "thread_id" (host:get $message message_thread_id))))
          ($h10 (host:add $h09 (host:flag "is_topic" (host:truthy (host:get $message is_topic_message)))))
          ($h11 (host:add $h10 (host:field "from" (host:display-user $sender))))
          ($h12 (host:add $h11 (host:field "from_id" (host:get $sender id))))
          ($h13 (host:add $h12 (host:flag "from_is_bot" (host:truthy (host:get $sender is_bot)))))
          ($h14 (host:add $h13 (host:field "sender_chat" (host:display-chat $sender-chat))))
          ($h15 (host:add $h14 (host:field "sender_chat_id" (host:get $sender-chat id))))
          ($h16 (host:add $h15 (host:field "via_bot" (host:display-user $via))))
          ($h17 (host:add $h16 (host:flag "automatic_forward" (host:truthy (host:get $message is_automatic_forward)))))
          ($h18 (host:add $h17 (host:forward-fields $message)))
          ($h19 (host:add $h18 (host:reply-fields $message))))
     (str:join "" ("[" $h19 "]\n" $text))))

;; telegram._extract_message: (kind message), message null when none
(= (host:extract $update) (host:extract-from $update (message edited_message channel_post edited_channel_post)))
(= (host:extract-from $update $kinds)
   (if (== $kinds ()) (cons-atom "unsupported" (cons-atom null ()))
     (let $message (host:get $update (car-atom $kinds))
       (if (host:truthy $message)
         (cons-atom (str:symbol->string (car-atom $kinds)) (cons-atom $message ()))
         (host:extract-from $update (cdr-atom $kinds))))))

;; --------------------------------------------------------------------
;; Telegram: routes (src/mode_channel.py)
;; --------------------------------------------------------------------
(= (host:route-name $chat $thread)
   (str:join "" ("telegram_" (host:replace (str:number->string $chat) "-" "m") "_" (str:number->string $thread))))
(= (host:integer $value)
   (if (host:number? $value) (math:trunc $value)
     (if (host:string? $value)
       (case (catch (str:string->number (str:trim $value)))
         (((Error $a $b) none) ($n (if (host:number? $n) (math:trunc $n) none))))
       none)))

;; (route chat thread) of a message, or none
(= (host:route-of $message)
   (let* (($chat (host:integer (host:get (host:object (host:get $message chat)) id)))
          ($given (host:get $message message_thread_id))
          ($thread (if (== $given null) 0 (host:integer $given))))
     (if (host:valid-lane? $chat $thread) (route $chat $thread) none)))
(= (host:valid-lane? $chat $thread)
   (if (== $chat none) False (if (== $thread none) False (and (not (== $chat 0)) (>= $thread 0)))))

;; mode_channel.remember_route, in json.dump's spacing so that the route
;; context lines stay mode_channel.context()'s.
(= (host:remember-route $message $chat $thread)
   (let* (($object (host:object (host:get $message chat)))
          ($kind (if (json-is-object $object) (json-get $object type "unknown") "unknown"))
          ($title (if (json-is-object $object) (json-get $object title "") ""))
          ($text (str:join "" ("{\"chat\": " (host:quote (str:number->string $chat))
                               ", \"thread\": " (str:number->string $thread)
                               ", \"type\": " (host:quote (host:text $kind))
                               ", \"title\": " (host:quote (host:text $title)) "}"))))
     (host:write-durable (host:path (host:state-file "channel-routes") (str:concat (host:route-name $chat $thread) ".json")) $text)))

(= (host:first-set $names)
   (if (== $names ()) ""
     (let $value (host:env (car-atom $names) "")
       (if (== $value "") (host:first-set (cdr-atom $names)) $value))))
(= (host:primary)
   (let $given (host:first-set ("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID" "METTACLAW_TELEGRAM_CHAT_ID" "TELEGRAM_CHAT_ID"))
     (if (== $given "") "" (str:trim (car-atom (str:split "," $given))))))

(= (host:digits? $text) (and (not (== $text "")) (str:is-all-in digit $text)))
(= (host:route-name? $name)
   (if (not (host:string? $name)) False
    (if (not (str:starts-with $name "telegram_")) False
     (let $parts (str:split "_" $name)
       (if (not (== (size-atom $parts) 3)) False
         (let* (($chat (index-atom $parts 1))
                ($digits (if (str:starts-with $chat "m") (str:byte-slice $chat 1 (str:byte-length $chat)) $chat)))
           (and (host:digits? $digits) (host:digits? (index-atom $parts 2)))))))))

;; mode_channel.resolve: (route chat thread) or (Error send why)
(= (host:resolve $name)
   (if (== $name "telegram")
     (let $chat (host:integer (host:primary))
       (if (== $chat none) (Error send "no operator chat is configured") (route $chat 0)))
     (if (not (host:route-name? $name))
       (Error send "unknown channel; use a destination from the route context")
       (let* (($value (host:read-json (host:path (host:state-file "channel-routes") (str:concat $name ".json")) null))
              ($chat (host:integer (host:get $value chat)))
              ($thread (host:integer (host:get $value thread))))
         (if (not (host:valid-lane? $chat $thread)) (Error send "unknown Telegram destination")
           (if (== (host:route-name $chat $thread) $name) (route $chat $thread)
             (Error send "unknown Telegram destination")))))))

;; mode_channel.context
(= (host:route-context)
   (let* (($fixed ("CHANNEL ROUTES (host configuration):"
                   "\"telegram\" addresses the private operator only; reading a group does not change it."
                   "Choose the destination explicitly for every group or thread reply."
                   "A sender, quoted sender and intended recipient are different people."
                   "Sibling conversation belongs in an explicit group route, not the operator's private chat."
                   "A send result means durably queued, not confirmed delivered."
                   "/mode, /model, /start and /stop are handled by the independent control responder."))
          ($primary (host:primary))
          ($operator (if (== $primary "") () (cons-atom (str:concat "telegram: operator chat=" $primary) ())))
          ($folder (host:state-file "channel-routes"))
          ($routes (filter-atom (map-atom (host:files $folder ".json") $name (host:route-line $folder $name))
                                $line (not (== $line "")))))
     (host:join "\n" (append $fixed (append $operator $routes)))))
(= (host:route-line $folder $name)
   (let* (($path (str:join "/" ($folder $name)))
          ($value (host:read-json $path null)))
     (if (not (host:truthy $value)) ""
       (str:concat (str:byte-slice $name 0 (- (str:byte-length $name) 5))
                   (str:concat ": " (strip (read-text $path)))))))

;; --------------------------------------------------------------------
;; Telegram: the durable service (src/durable_telegram.py)
;; --------------------------------------------------------------------
(= (host:telegram-socket) (host:env "METTACLAW_TELEGRAM_SERVICE_SOCKET" ""))
(= (host:telegram-call $code $name $body)
   (let $socket (host:telegram-socket)
     (if (== $socket "") (cwp:unavailable unconfigured) (cwp:call $socket $code $name $body 1000))))

;; Pending tasks after a task, oldest first: ((task body) ...)
(= (host:telegram-scan $after $limit)
   (if (== $limit 0) ()
     (case (host:telegram-call 1 $after "")
       (((cwp:reply 65 $task $body) (cons-atom ($task $body) (host:telegram-scan $task (- $limit 1))))
        ($other ())))))

;; An item: (item TASK KIND TEXT VALUE) with KIND input, receipt, delete or
;; skip. TEXT is what the loop is offered; VALUE is the parsed body.
(= (host:attachment-text) "[attachment received; use the agent attachment tools to inspect it]")
(= (host:receipt-text $body)
   (str:concat "[channel receipt; this is delivery status, not a user message]\n" $body))

(= (host:raw-input-text $body) (str:concat "[telegram update]\n" $body))
(= (host:input-item $task $update $body)
   (let ($kind $message) (host:extract $update)
     (if (not (host:truthy $message))
       (item $task input (host:raw-input-text $body) null)
       (let $route (host:route-of $message)
         (if (== $route none)
           (item $task input (host:raw-input-text $body) null)
           (let* (((route $chat $thread) $route)
                  ($given (host:either (host:get $message text) (host:either (host:get $message caption) "")))
                  ($text (if (host:truthy $given) (host:text $given) (host:attachment-text)))
                  ($rendered (str:join "" ("[reply_channel=" (host:route-name $chat $thread) "]\n"
                                           (host:format-message $update $kind $message $text)))))
             (item $task input $rendered $message)))))))

(= (host:classify $task $body)
   (case (collapse (catch (once (json-parse $body))))
     ((($value)
       (if (or (json-is-object $value) (or (host:string? $value) (or (host:number? $value) (== $value ()))))
         (item $task skip "" null)
         (case $value
           (((Error $a $b) (item $task skip "" null))
            ((Error $a) (item $task skip "" null))
            ($list
             (let $head (car-atom $list)
               (if (== $head "input")
                 (if (>= (size-atom $list) 5) (host:input-item $task (index-atom $list 4) $body) (item $task skip "" null))
                 (if (== $head "command")
                   (if (< (size-atom $list) 3) (item $task skip "" null)
                     (if (== (index-atom $list 2) "/delete") (item $task delete "" null) (item $task skip "" null)))
                   (if (== $head "callback") (item $task skip "" null)
                     (item $task receipt (host:receipt-text $body) $list))))))))))
      ($none (item $task skip "" null)))))

;; Unacknowledged inputs kept from the one-poller handover
;; (mode_channel.handover_inputs), before the service's own tasks.
(= (host:handover-folder) (host:state-file "channel-handover"))
(= (host:handover-items)
   (if (== (host:env "METTACLAW_ENGINE_STATE_PATH" "") "") ()
     (filter-atom (map-atom (host:files (host:handover-folder) ".json") $name
                            (let* (($path (host:path (host:handover-folder) $name))
                                   ($raw (host:read-json $path null))
                                   ($uid (str:byte-slice $name 0 (- (str:byte-length $name) 5))))
                              (if (host:truthy $raw)
                                (host:input-item (str:concat "handover:" $uid) $raw (json-text $raw))
                                (item none skip "" null))))
                  $item (not (== (host:item-kind $item) skip)))))

(= (host:item-kind (item $task $kind $text $value)) $kind)
(= (host:item-task (item $task $kind $text $value)) $task)
(= (host:item-text (item $task $kind $text $value)) $text)
(= (host:offered? $item) (let $kind (host:item-kind $item) (or (== $kind input) (== $kind receipt))))

(= (host:telegram-items)
   (append (host:handover-items)
           (map-atom (host:telegram-scan "" 128) $entry
                     (let ($task $body) $entry (host:classify $task $body)))))

;; The user messages of the saved history.
(= (host:saved-texts)
   (let $history (host:read-json "experience.json" ())
     (if (json-is-object $history) ()
       (map-atom (filter-atom $history $message (host:user-message? $message))
                 $message (json-get $message content "")))))
(= (host:user-message? $message)
   (if (not (json-is-object $message)) False
     (and (== (json-get $message role null) "user") (host:string? (json-get $message content null)))))
(= (host:saved? $text $saved)
   (if (== $saved ()) False
     (case (str:find (car-atom $saved) $text)
       (((TextNotFound $needle) (host:saved? $text (cdr-atom $saved)))
        ($at True)))))

;; /delete belongs to the framework loop's client.
(= (host:answer-delete $task)
   (let $body (json-text (cons-atom "answer" (cons-atom "Message deletion is unavailable in iter mode; switch to godelclaw." ())))
     (host:telegram-call 2 $task $body)))

(= (host:note-delivery (item $task input $text $message))
   (case (host:route-of $message)
     (((route $chat $thread) (host:remember-route $message $chat $thread))
      ($none True))))
(= (host:note-delivery (item $task receipt $text $value))
   (host:append-durable (host:runtime-file "channel-receipts.jsonl")
                        (str:concat (json-text (json-object (cons-atom (json-pair task $task) (cons-atom (json-pair receipt $value) ())))) "\n")))

;; The channel's receive: pending input that the saved history does not hold.
;; Nothing is offered while cognition is stopped or a recycle is requested.
(= (host:telegram-receive)
   (if (host:hold-input?) ""
     (let* (($items (host:telegram-items))
            ($deleted (map-atom (filter-atom $items $item (== (host:item-kind $item) delete)) $item
                                (host:answer-delete (host:item-task $item))))
            ($offers (filter-atom $items $item (host:offered? $item)))
            ($saved (if (== $offers ()) () (host:saved-texts)))
            ($fresh (filter-atom $offers $item (not (host:saved? (host:item-text $item) $saved))))
            ($noted (map-atom $fresh $item (host:note-delivery $item)))
            ($recorded (if (== $fresh ()) True
                         (host:record "input" (cons-atom (json-pair count (size-atom $fresh)) ())))))
       (host:join "\n" (map-atom $fresh $item (host:item-text $item))))))

;; Acknowledge what a provider turn is about to use: pending input whose text
;; the saved history holds. The history is synced to disk first.
(= (host:acknowledge (item $task $kind $text $value))
   (if (str:starts-with $task "handover:")
     (let* (($uid (str:byte-slice $task 9 (str:byte-length $task)))
            ($path (host:path (host:handover-folder) (str:concat $uid ".json"))))
       (if (and (host:digits? $uid) (file-exists $path))
         (fs:rename $path (host:path (host:handover-folder) (str:concat $uid ".done")))
         True))
     (host:telegram-call 2 $task "[]")))

(= (host:sync-history)
   (let $owner (fs:open-write "experience.json" ("append") 384)
     (if (not (host:ok? $owner)) $owner
       (let* (($synced (fs:sync $owner)) ($closed (fs:close $owner)) ($directory (host:sync-path ".")))
         $synced))))

(= (host:telegram-commit)
   (if (== (host:telegram-socket) "") 0
     (let $offers (filter-atom (host:telegram-items) $item (host:offered? $item))
       (if (== $offers ()) 0
         (let* (($saved (host:saved-texts))
                ($done (filter-atom $offers $item (host:saved? (host:item-text $item) $saved))))
           (if (== $done ()) 0
             (let* (($synced (host:sync-history))
                    ($acknowledged (map-atom $done $item (host:acknowledge $item)))
                    ($count (size-atom $done))
                    ($recorded (host:record "input-acknowledged" (cons-atom (json-pair count $count) ()))))
               $count)))))))

;; --------------------------------------------------------------------
;; Telegram: send intents (mode_channel.enqueue)
;; --------------------------------------------------------------------
;; durable_telegram.chunks: pieces of at most 4096 scalars, cut at the last
;; newline within reach when there is one.
(= (host:chunks $text)
   (if (<= (str:char-length $text) 4096)
     (if (== $text "") () (cons-atom $text ()))
     (let* (($window (str:char-slice $text 0 4097))
            ($lines (str:split "\n" $window))
            ($last (str:char-length (index-atom $lines (- (size-atom $lines) 1))))
            ($found (if (== (size-atom $lines) 1) -1 (- (- (str:char-length $window) $last) 1)))
            ($cut (if (<= $found 0) 4096 $found))
            ($piece (str:char-slice $text 0 $cut))
            ($rest (str:char-slice $text $cut (str:char-length $text))))
       (cons-atom $piece (host:chunks (if (str:starts-with $rest "\n") (host:lstrip-newlines $rest) $rest))))))
(= (host:lstrip-newlines $text)
   (if (str:starts-with $text "\n") (host:lstrip-newlines (str:byte-slice $text 1 (str:byte-length $text))) $text))

;; durable_telegram.Channel.key: CHAT.THREAD.%020d, strictly increasing
;; across writers sharing the outbox's sequence file.
(= (host:sequence-value $path)
   (if (file-exists $path)
     (case (collapse (catch (once (str:string->number (strip (read-text $path))))))
       ((($n) (if (host:number? $n) $n 0)) ($none 0)))
     0))
(= (host:key $chat $thread)
   (let* (($folder (host:state-file "channel-outbox"))
          ($made (fs:make-directories $folder 448))
          ($path (str:concat $folder "/sequence"))
          ($lock (fs:open-write (str:concat $path ".lock") ("append" "create") 438)))
     (if (not (host:ok? $lock)) (Error send "could not open the send sequence lock")
       (host:key-locked $chat $thread $path $lock))))
(= (host:key-locked $chat $thread $path $lock)
   (let* (($held (fs:lock $lock exclusive))
          ($now (math:trunc (* (system:wall-time) 1000000000.0)))
          ($next (+ (host:sequence-value $path) 1))
          ($sequence (if (> $next $now) $next $now))
          ($saved (host:write-durable $path (str:concat (str:number->string $sequence) "\n")))
          ($released (fs:unlock $lock))
          ($closed (fs:close $lock)))
     (if (== $saved True)
       (str:concat (str:number->string $chat)
         (str:concat "." (str:concat (str:number->string $thread) (str:concat "." (host:zeros (str:number->string $sequence) 20)))))
       (Error send "could not reserve a send key"))))

(= (host:telegram-send $destination $text)
   (let $resolved (host:resolve $destination)
     (case $resolved
       (((route $chat $thread)
         (let* (($content (host:text $text))
                ($key (host:key $chat $thread)))
           (case $key
             (((Error $what $why) $key)
              ($ready
               (let* (($commands (map-atom (host:chunks $content) $piece
                                           (cons-atom "send" (cons-atom $piece (cons-atom "plain" ())))))
                      ($intent (json-object (cons-atom (json-pair key $ready) (cons-atom (json-pair commands $commands) ()))))
                      ($stored (host:write-durable (host:path (host:state-file "channel-outbox") (str:concat $ready ".json"))
                                                   (json-text $intent))))
                 (if (== $stored True)
                   (let $recorded (host:record "send-queued" (cons-atom (json-pair destination $destination)
                                                               (cons-atom (json-pair characters (str:char-length $content)) ())))
                     (str:concat "durably queued: " $ready))
                   (Error send "could not store the send intent"))))))))
        ($error $error)))))

;; The supervised send tool: Telegram routes are durable intents; any other
;; name is a channel module, as the upstream tool sends.
(= (host:telegram-name? $channel)
   (if (host:string? $channel) (or (== $channel "telegram") (str:starts-with $channel "telegram_")) False))
(= (host:send-tool $channel $content)
   (if (host:telegram-name? $channel)
     (host:telegram-send $channel $content)
     (let $path (str:join "" ("channels/" (host:text $channel) ".metta"))
       (if (not (file-exists $path))
         (str:concat "Unknown channel: " (host:text $channel))
         (case (collapse (import! &self $path))
           (((true) (let* (($call (cons-atom send (cons-atom $content ())))
                           ($sent (eval $call)))
                      (if (== $sent $call)
                        (str:join "" ("Channel " (host:text $channel) " cannot send"))
                        "SUCCESS")))
            ($none (Error $path "the channel module could not be loaded"))))))))

;; --------------------------------------------------------------------
;; The model request (godelclaw's native provider in a process of its own)
;; --------------------------------------------------------------------
(= (host:boot)
   (progn (change-state! &host-saved none)
          (host:record "boot" (cons-atom (json-pair pid (system:pid))
                                (cons-atom (json-pair engine (host:env "METTACLAW_ACTIVE_ENGINE" "cetta")) ())))
          (host:record-active)
          (host:recover-outbox)
          (println! "[mode-runtime] started iter")
          true))

;; A save since the previous request is a turn boundary: the previous turn's
;; results, or received input, are in the history.
(= (host:history-stamp)
   (if (file-exists "experience.json")
     (stamp (progn (translatePredicate (time_file "experience.json" $time)) $time)
            (progn (translatePredicate (size_file "experience.json" $size)) $size))
     none))
(= (host:boundary)
   (let* (($now (host:history-stamp))
          ($before (get-state &host-saved)))
     (if (== $now $before) "0"
       (progn (change-state! &host-saved $now)
              (host:record "boundary" ())
              "1"))))

;; iter_host.request_plan's request: the route context first, then the loop's
;; messages, its tools, required tool choice and thinking.
(= (host:request $messages $tools $model $max_tokens)
   (let* (($context (json-text (json-object (cons-atom (json-pair role "system")
                                              (cons-atom (json-pair content (host:route-context)) ())))))
          ($rest (str:byte-slice $messages 1 (str:byte-length $messages))))
     (str:join "" ("{\"model\":" (json-text (host:text $model)) ",\"messages\":[" $context "," $rest
                   ",\"tools\":" $tools ",\"tool_choice\":\"required\",\"max_tokens\":" (str:number->string $max_tokens)
                   ",\"extra_body\":{\"enable_thinking\":true}}"))))

(= (host:provider-deadline)
   (let $hard (case (catch (str:string->number (host:env "METTACLAW_LLM_HARD_SECONDS" "1800")))
                (((Error $a $b) 1800) ($n (if (host:number? $n) $n 1800))))
     (+ $hard 900)))

(= (host:await $pid $deadline)
   (let $status (progn (translatePredicate (process_wait $pid $s ((Predicate (timeout 5))))) $s)
     (if (== $status timeout)
       (if (< (system:wall-time) $deadline) (host:await $pid $deadline)
         (progn (catch (translatePredicate (process_kill $pid 9)))
                (translatePredicate (process_wait $pid $k))
                timed-out))
       $status)))

(= (host:remove-quietly $path) (if (file-exists $path) (catch (fs:remove $path)) True))

(= (host:without-nulls $object)
   (json-object (filter-atom (json-pairs $object) $pair (not (== (json-pair-value $pair) null)))))

;; (message_json finish_reason response usage), as the fork's iter_llm.metta
(= (host:answer $reply $text)
   (let* (($message (host:without-nulls (json-get $reply message (json-object ()))))
          ($finish (string-to-atom (host:text (host:either (json-get $reply finish null) "stop"))))
          ($usage (json-get $reply usage null)))
     (cons-atom (json-text $message)
       (cons-atom $finish
         (cons-atom $text
           (cons-atom (if (or (== $usage null) (not (host:truthy $usage))) "None" (json-text $usage)) ()))))))

(= (host:answered? $reply)
   (if (json-is-object $reply) (not (== (json-get $reply message null) null)) False))

(= (host:provider-call $request $settle)
   (let* (($directory (host:runtime-file ".provider"))
          ($made (fs:make-directories $directory 448))
          ($stem (str:join "" ($directory "/" (str:number->string (system:pid)) "-" (str:number->string (system:monotonic-ns)))))
          ($request-file (str:concat $stem "-request.json"))
          ($reply-file (str:concat $stem "-reply.json"))
          ($written (host:write-durable $request-file $request))
          ($environment (cons-atom (json-pair ITER_PROVIDER_REQUEST $request-file)
                          (cons-atom (json-pair ITER_PROVIDER_REPLY $reply-file)
                            (cons-atom (json-pair ITER_PROVIDER_SETTLE $settle)
                              (cons-atom (json-pair METTACLAW_LLM_TRANSPORT "native") ())))))
          ($argv (cons-atom "--lang" (cons-atom "petta" (cons-atom "--import-mode" (cons-atom "ancestor-walk"
                   (cons-atom "--no-results" (cons-atom (host:env "ITER_PROVIDER_ENTRY" "") ())))))))
          ($cetta (host:cetta))
          ($options (cons-atom (Predicate (environment $environment))
                      (cons-atom (Predicate (stdin null))
                        (cons-atom (Predicate (process $p)) ()))))
          ($pid (progn (translatePredicate (process_create $cetta $argv $options)) $p))
          ($status (host:await $pid (+ (system:wall-time) (host:provider-deadline))))
          ($text (if (file-exists $reply-file) (read-text $reply-file) ""))
          ($removed (host:remove-quietly $request-file))
          ($removed2 (host:remove-quietly $reply-file))
          ($reply (if (== $text "") null
                    (case (collapse (catch (once (json-parse $text))))
                      ((($value) $value) ($none null))))))
     (if (host:answered? $reply)
       (host:answer $reply $text)
       (if (json-is-object $reply)
         (translatePredicate (throw (iter_provider_error (host:text (json-get $reply error "provider failed")))))
         (if (host:recycle-requested?) (host:recycle-exit)
           (translatePredicate (throw (iter_provider_error (str:concat "the provider process ended without a reply: " (repr $status))))))))))

;; utils.metta's llm calls this: gate, boundary, acknowledgement, request.
(= (host:chat-completion $messages $tools $model $max_tokens)
   (let* (($admitted (host:gate))
          ($settle (host:boundary))
          ($acknowledged (host:telegram-commit))
          ($request (host:request $messages $tools $model $max_tokens)))
     (host:provider-call $request $settle)))

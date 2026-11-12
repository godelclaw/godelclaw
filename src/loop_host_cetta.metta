; Host settings and the conversation journal that the loop reads and writes
; every turn, without Python. Everything here is Python-free.
!(import! &self (library fs))
!(import! &self (library path))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self (library system))

!(bind! &loop-host-integer
   (new-state (re:compile "\\A[ \\t\\n\\r\\f\\v]*([+-]?[0-9](?:_?[0-9])*)[ \\t\\n\\r\\f\\v]*\\z")))

; Python int() of an integer setting: ASCII digits with single underscores,
; an optional sign and surrounding whitespace. Unset, empty or other text
; gives the default.
(: loop-host:int-text (-> %Undefined% %Undefined% %Undefined%))
(= (loop-host:int-text $text $default)
   (case (re:captures (get-state &loop-host-integer) $text)
     ((re:no-match $default)
      ($match (str:string->number (str:join "" (str:split "_" (re:group-text $match $text 1))))))))
(: loop-host:int-env (-> %Undefined% %Undefined% %Undefined%))
(= (loop-host:int-env $name $default)
   (loop-host:int-text (system:getenv-or-default $name "") $default))

(= (loop-host:heartbeat-interval) (loop-host:int-env "METTACLAW_HEARTBEAT_INTERVAL_SECONDS" 1800))
(= (loop-host:reasoning-effort) (system:getenv-or-default "METTACLAW_REASONING_EFFORT" "default"))
(= (loop-host:prompt-path) (system:getenv-or-default "METTACLAW_PROMPT_PATH" "./memory/soul.md"))
(= (loop-host:history-path) (system:getenv-or-default "METTACLAW_HISTORY_PATH" "./memory/history.metta"))

; The managed-loop admission limit; a setting that is not positive keeps the
; policy's own limit.
(: loop-host:command-batch-limit (-> %Undefined% %Undefined%))
(= (loop-host:command-batch-limit $default)
   (let $limit (loop-host:int-env "METTACLAW_COMMAND_BATCH_LIMIT" $default)
     (if (> $limit 0) $limit $default)))

; Whether a turn value carries text: neither empty text nor an empty expression.
(: loop-host:nonempty? (-> %Undefined% Bool))
(= (loop-host:nonempty? $value) (not (or (== $value "") (== $value ()))))

; One turn appended durably to the configured journal: parent directories
; made, the file created 0600, a final newline ensured and fsync before
; success. This is an observation sink with a deployment-fixed path, not the
; public append-file skill. Answers 1, or 0 after reporting the failure.
(: loop-host:history-append (-> %Undefined% Number))
(= (loop-host:history-append $text) (loop-host:append-line (loop-host:history-path) $text))
(: loop-host:append-line (-> %Undefined% %Undefined% Number))
(= (loop-host:append-line $path $text)
   (let $payload (if (str:ends-with $text "\n") $text (str:concat $text "\n"))
     (case (catch (loop-host:append-durably $path $payload))
       ((1 1)
        ($failed (let $_ (system:write stderr (format-args "[loop] history append error: {}\n" ((repr $failed)))) 0))))))
(: loop-host:append-durably (-> %Undefined% %Undefined% %Undefined%))
(= (loop-host:append-durably $path $payload)
   (let* (($_ (fs:make-directories (path:dirname $path) 511))
          ($owner (fs:open-write $path ("append" "create") 384)))
     (case $owner
       (((NativeHandle $kind $id)
         (let* (($written (fs:write-all $owner $payload))
                ($synced (case $written (((fs:written $n) (fs:sync $owner)) ($other $other))))
                ($closed (fs:close $owner)))
           (case ($written $synced)
             ((((fs:written $n) fs:synced) (if (== $n (str:byte-length $payload)) 1 (Incomplete $n)))
              ($other $other)))))
        ($refused $refused)))))

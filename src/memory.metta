; Select engine-specific definitions here, independent of import order.
!(import! &self ./src/engine_modes.py)
(= (memory:cetta? $engine)
   (case $engine ((cetta True) ("cetta" True) ($other False))))

;Configured at runtime memory and history in amount of characters:
(= (maxFeedback) (empty))
(= (maxRecallItems) (empty))
(= (maxRecentTurns) (empty))

(= (initMemory)
   (progn (println! "Initializing memory")
          (configure maxFeedback 200000)
          (configure maxRecallItems 20)
          (configure maxRecentTurns 8)))

(= (getPrompt)
   (read-file (py-call (helper.path_from_env "METTACLAW_PROMPT_PATH" "./memory/soul.md"))))

;; The running proposal window: what the model requested over the last few
;; substantive turns. Broker receipts, not this transcript, say what ran.
;; No-op turns do not consume a slot.
!(if (memory:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/action_window_cetta)
     (import! &self ./src/action_window_python))

(= (recentActions)
   (action-window:recent (action-window:history-path) (maxRecentTurns)))

(= (relevantFiles)
   (action-window:files (action-window:history-path) (maxRecentTurns)))

(= (addToHistory $lastmessage $response $msgnew)
   (if $msgnew
       (if (== (get-state &error) ())
           (appendToHistory ((get_time_as_string) (newline) "HUMAN_MESSAGE: " $lastmessage (newline) $response (newline)))
           (appendToHistory ((get_time_as_string) (newline) "HUMAN_MESSAGE: " $lastmessage (newline) $response (newline) ERROR_FEEDBACK: (get-state &error))))
       (if (== (get-state &error) ())
           (appendToHistory ((get_time_as_string) (newline) $response (newline)))
           (appendToHistory ((get_time_as_string) (newline) $response (newline) ERROR_FEEDBACK: (get-state &error))))))

(= (appendToHistory $addition)
   (py-call (helper.history_append (swrite $addition))))

; The native path owns memory controls and durable write retries in MeTTa.
; The older SWI-PeTTa adapter remains available without a second new policy.
!(if (memory:cetta? (py-call (engine_modes.active_engine)))
     (import! &self ./src/semantic_memory_cetta)
     (import! &self ./src/semantic_memory_legacy))

(= (legacy-remember $str)
   (let $memory (utils:decode-safe $str)
        (py-call (qwen_embed.remember_guarded (string-safe $memory) (get_time_as_string)))))

(= (legacy-query $str $count)
   (let $query (utils:decode-safe $str)
        (py-call (qwen_embed.query_guarded (string-safe $query) $count))))

(= (remember $str) (semantic:remember $str))
(= (query $str) (query $str (maxRecallItems)))
(= (query $str $count) (semantic:query $str $count))

(= (query $str $count $mode) (semantic:query $str $count $mode))

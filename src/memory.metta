; Select engine-specific definitions here, independent of import order.

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
!(import! &self ./src/action_window_cetta)

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

; The native memory service owns memory controls and durable write retries in MeTTa.
!(import! &self ./src/semantic_memory_cetta)

(= (remember $str) (semantic:remember $str))
(= (query $str) (query $str (maxRecallItems)))
(= (query $str $count) (semantic:query $str $count))

(= (query $str $count $mode) (semantic:query $str $count $mode))

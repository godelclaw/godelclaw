; The selected model, read on every call: the first durable
; (active-model NAME) line of the model state file, else SYNTHETIC_MODEL, else
; the caller's default. The command responder records a switch from its own
; process, so the file, not this process's environment, is the authority.
; A missing, unreadable or non-UTF-8 file is no recorded choice.
!(import! &self (library fs))
!(import! &self (library re))
!(import! &self (library str))
!(import! &self (library system))

!(bind! &model-choice (new-state (re:compile "(?m)^\\s*\\(active-model\\s+([^\\s()]+)\\s*\\)")))

; The recorded choice in a state file's text, or ().
(: model:choice-in (-> %Undefined% %Undefined%))
(= (model:choice-in $text)
   (case (re:captures (get-state &model-choice) $text)
     ((re:no-match ())
      ($match (re:group-text $match $text 1)))))

(: model:select (-> %Undefined% %Undefined% %Undefined%))
(= (model:select $recorded $configured)
   (if (== $recorded ()) $configured $recorded))

(: model:state-path (-> %Undefined%))
(= (model:state-path)
   (system:getenv-or-default "METTACLAW_MODEL_STATE_PATH" "memory/persistent.metta"))

(: model:recorded (-> %Undefined% %Undefined%))
(= (model:recorded $path)
   (case (catch (fs:read-text $path))
     (((Error $where $why) ())
      ((Error $why) ())
      ($text (model:choice-in $text)))))

(: model:current (-> %Undefined% %Undefined%))
(= (model:current $default)
   (model:select (model:recorded (model:state-path))
                 (system:getenv-or-default "SYNTHETIC_MODEL" (format-args "{}" ($default)))))

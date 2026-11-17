; Native source registry: immutable specifications and captured supplied views.
!(import! &self (library path))
!(import! &self (library re))
!(import! &self ./undelivered_prose_unicode.metta)
!(import! &self ./context_markers_cetta.metta)
!(import! &self ./pins_context_cetta.metta)
!(import! &self ./action_window_cetta.metta)
!(import! &self ./turn_frame_cetta.metta)

!(bind! &ctx-blank (new-state
   (re:compile (str:join "" ("\\A" (prose:pattern space) "*\\z")))))
!(bind! &ctx-first-space (new-state (re:compile (prose:pattern space))))
(= (ctx:blank? $raw)
   (if (== $raw "") True
     (if (re:is-match (get-state &ctx-first-space) (str:char-slice $raw 0 1))
       (re:is-match (get-state &ctx-blank) $raw) False)))

(= (ctx:specs)
   (("work-state" 8000 False) ("commitment-state" 2000 False)
    ("task-phase" 3000 False) ("active-query-declaration" 3000 False)
    ("affect-gestalt" 2000 False) ("pins" 6000 True)
    ("effect-receipts" 14000 True) ("development-state" 10000 True)
    ("recent-proposals" 10000 True) ("relevant-files" 4000 False)
    ("project-evidence" 8000 False) ("coding-sessions" 2000 False)
    ("project-capabilities" 5000 False) ("conversation" 30000 True)))
(= (ctx:supplied $views $id)
   (if (== $views ()) Missing
     (let $later (ctx:supplied (cdr-atom $views) $id)
       (if (!= $later Missing) $later
         (case (car-atom $views)
           ((($key $text)
             (if (and (== $key $id) (== (get-type $text) String))
               (if (ctx:blank? $text) Missing (CtxSuppliedV1 $text)) Missing))
            ($other Missing)))))))
(= (ctx:source-text $answers)
   (case $answers
     ((($text) (if (== (get-type $text) String) ("value" $text) ("unavailable")))
      ($other ("unavailable")))))
(= (ctx:source $source)
   (ctx:source-text (collapse (catch (ctx:retained $source)))))
(= (ctx:pins $views)
   (case (ctx:supplied $views "pins-body")
     (((CtxSuppliedV1 $text) (mark:pins $text))
      ($none (ctx:pins-read (pinsctx:read))))))
; A read diagnostic remains visible without becoming a new pins observation.
; Use the producer's typed outcome, rather than classifying diagnostic text.
(= (ctx:pins-read $read)
   (case $read
     (((PinsctxUnavailable $_) (pinsctx:render-read $read))
      ((PinsctxText $_) (mark:pins (pinsctx:render-read $read)))
      ((Error $where $why) (Error ctx:pins $why))
      ($bad (Error ctx:pins unsupported-read-outcome)))))
(= (ctx:retained (CtxSourceV1 $views $conversation $id))
   (case $id
     (("pins" (ctx:pins $views))
      ("conversation" $conversation)
      ("recent-proposals"
        (if (strategy:enabled?) "Task proposals and their dispositions are in TASK_CONTEXT."
          (case (ctx:supplied $views $id)
            (((CtxSuppliedV1 $text) $text)
             ($none (action-window:recent (action-window:history-path)
                      (str:string->number (system:getenv-or-default "METTACLAW_RECENT_ACTION_TURNS" "12"))))))))
      ("relevant-files"
        (case (ctx:supplied $views $id)
          (((CtxSuppliedV1 $text) $text)
           ($none (action-window:files (action-window:history-path)
                    (str:string->number (system:getenv-or-default "METTACLAW_RECENT_ACTION_TURNS" "12")))))))
      ($unknown (Error ctx:source unknown-source)))))
(= (ctx:project-root)
   (let* (($cwd (system:getcwd))
          ($configured (system:getenv-or-default "METTACLAW_PROJECT_ROOT" $cwd))
          ($absolute (if (path:is-absolute $configured) $configured (path:join $cwd $configured))))
     (case (catch (fs:canonical-path $absolute))
       (($resolved (if (== (get-type $resolved) String) $resolved (path:normalize $absolute)))))))
(= (ctx:native-epoch)
   (if (not (strategy:enabled?)) "unmanaged"
     (let* (($subject (turn:focus))
            ($workspace (ctx:project-root))
            ($path (system:getenv-or-default "METTACLAW_PROMPT_PATH" "memory/soul.md"))
            ($read (catch (fs:sha256-file $path)))
            ($instructions (case $read
               (((fs:hashed-v1 $hex $_) $hex)
                ($unavailable (str:concat "unavailable:" (system:uuid4-hex))))))
            ($body (json:stringify-python-with (JsonArray
                     ((JsonString $subject) (JsonString $workspace) (JsonString $instructions))) ((ensure-ascii True)))))
       (str:concat "sha256:" (str:sha256 $body)))))
(= (ctx:native-prepare $views $conversation)
   ((CtxLoadersV1 $views $conversation) (ctx:specs) (ctx:native-epoch)))

;; Named, replaceable additions surrounding the two-equation process core.
;; This file does not decide success or failure and owns no process root.
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/model_commentary.py)
!(import! &self ./src/strategy_host.py)
!(import! &self ./src/instance_hooks.metta)
!(import! &self ./src/interop_flags_cetta.metta)
;; The native pins context view needs its own libraries, not whichever module
;; happened to load them first. On SWI-PeTTa it is never asked (see below).
!(import! &self ./src/pins_context_cetta.metta)
!(import! &self ./src/engine_modes.metta)
!(case (engine:active)
   (("cetta" (import! &self ./src/context_sources_cetta.metta))
    (cetta (import! &self ./src/context_sources_cetta.metta))
    ($other (import! &self ./src/context_sources_python.metta))))

;; action-window:context-views comes from memory.metta's engine switch: the
;; proposal window and touched files computed natively on CeTTa, () on SWI.
;; Views that fail or yield nothing are left to the bundle's own loaders, so
;; they cost those two sources at most, never the bundle.
(= (addition-action-views)
   (let $computed (collapse (catch (action-window:context-views)))
     (if (== $computed ()) ()
       (case (car-atom $computed) (((Error $call $why) ()) ($views $views))))))

;; The pins context view: computed natively on CeTTa, left to pins.py's own
;; loader on SWI-PeTTa. Only exactly (("pins-body" <text>)) is accepted: the
;; marker-free body, to which context_sources applies the one change marker. A failed
;; read, an unreduced call or any other shape yields no view, so the bundle's
;; pins loader answers exactly as it did before; the view is never invented
;; as empty. The action views above are unaffected either way.
(= (addition-pins-views)
   (case (engine:active)
     (("cetta" (addition-pins-views-native)) (cetta (addition-pins-views-native)) ($other ()))))
(= (addition-pins-views-native)
   (let $computed (collapse (catch (pinsctx:context-views)))
     (case $computed
       ((((("pins-body" $text))) (addition-pins-view $text))
        ($other ())))))
(= (addition-pins-view $text)
   (if (== (get-type $text) String) (("pins-body" $text)) ()))
(= (addition-context-views)
   (let* (($action (addition-action-views))
          ($pins (addition-pins-views)))
     (union-atom $action $pins)))
(= (addition-chat-mode?)
   (== (host:text (py-call (strategy_host.conversation_mode))) "chat"))
(= (addition-context-sources)
   (let $views (addition-context-views)
     (if (tg:client-metta?)
         (let $conversation
              (if (addition-chat-mode?)
                  (tg:conversation-window-for (tg:conversation-events) (tg:get reply-chat ""))
                  (tg:route-view))
           (context-bundle:with-views $views $conversation))
         (context-bundle:with-views $views))))

(= (addition-effect-turn-begin $turn)
   (let $_ (addition-reap-work) (py-call (effect_frontier.begin $turn))))

;; Coding sessions that ended without their report are recorded and their
;; requester told, at the next turn. Coding mode runs on CeTTa only.
(= (addition-reap-work)
   (case (engine:active) (("cetta" (work:reap)) (cetta (work:reap)) ($other 0))))

(= (addition-strategy-begin $turn $source $message)
   (let $receipt (py-call (strategy_host.begin $turn $source $message))
     (let* (($_ (hierarchy:begin $turn $source (py-call (strategy_host.view))))
            ($_ (ws:begin (hierarchy:task) $source
                  (if (and (== $source "operator-event") (tg:client-metta?))
                    (tg:get reply-chat "") "")))) $receipt)))

(= (addition-protected-request $request)
   (addition-protected-request $request 32768))

(= (addition-protected-request $request $tokens)
   (let* (($formatted (py-call (model_commentary.prompt $request)))
          ($context (hierarchy:context)))
     (if (== $context "") (state:prepare-context $formatted $tokens)
       (state:prepare-context (hierarchy:append-context $formatted $context) $tokens))))

(= (addition-prepare-request
      $selected $legacy $system $activity $advertised)
   (coding-prepare-request
     $selected $legacy $system $activity $advertised))

(= (addition-model-call $model $tokens $reasoning $request $wake)
   (case $request
     (((ContextPreparationFailed $reason) (py-call (strategy_host.preparation_failed $reason)))
      ($ready
        (if (== (py-call (strategy_host.capture $ready $wake)) 1)
            (llm:command $model $tokens $reasoning $ready)
            (progn (py-call (cognitive_health.turn_failed "strategy-not-ready")) "()"))))))

(= (addition-request-observation $request)
   (case $request
     (((ContextPreparationFailed $reason) (println! (CONTEXT_PREPARATION_FAILED: $reason)))
      ($ready (println! (CHARS_SENT: (string_length $ready) sha256 (str:sha256 $ready)))))))

(= (addition-balance-response $raw)
   (model-response-balance (py-call (strategy_host.normalize_response $raw))))
(= (addition-envelope-response $balanced)
   (model-response-envelope $balanced))
(= (addition-read-response $response)
   (model-response-read $response))
(= (addition-command-sequence $parsed)
   (model-response-commands $parsed))

(= (addition-effect-broker $turn $limit $commands)
   (let* (($broker-turn (py-call (strategy_host.broker_turn $turn)))
          ($_ (addition-effect-turn-begin $broker-turn))
          ($effective (py-call (strategy_host.batch_limit $limit (quote $commands))))
          ($admitted (py-call (strategy_host.broker_begin $broker-turn (quote $commands) $effective))))
     (if (== $admitted 1)
       (let $records (run-command-batch-once $broker-turn $effective (quote $commands))
         (py-call (strategy_host.broker_end $broker-turn (quote $records))))
       ((COMMAND_BATCH_WITHHELD: (py-call (strategy_host.broker_feedback)))))))

(= (addition-command-errors $turn)
   (command-batch-errors (py-call (strategy_host.broker_turn $turn))))

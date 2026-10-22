;; Named, replaceable additions surrounding the two-equation process core.
;; This file does not decide success or failure and owns no process root.
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/model_commentary.py)
!(import! &self ./src/strategy_host.py)
!(import! &self ./src/instance_hooks.metta)

;; action-window:context-views comes from memory.metta's engine switch: the
;; proposal window and touched files computed natively on CeTTa, () on SWI.
;; Views that fail or yield nothing are left to the bundle's own loaders, so
;; they cost those two sources at most, never the bundle.
(= (addition-action-views)
   (let $computed (collapse (catch (action-window:context-views)))
     (if (== $computed ()) ()
       (case (car-atom $computed) (((Error $call $why) ()) ($views $views))))))
(= (addition-context-sources)
   (let $views (addition-action-views)
     (if (tg:client-metta?)
         (let $conversation
              (if (== (py-call (strategy_host.conversation_mode)) "chat")
                  (tg:conversation-window-for (tg:conversation-events) (tg:get reply-chat ""))
                  (tg:route-view))
           (py-call (context_sources.bundle_with_views $views $conversation)))
         (py-call (context_sources.bundle_with_views $views)))))

(= (addition-effect-turn-begin $turn)
   (py-call (effect_frontier.begin $turn)))

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

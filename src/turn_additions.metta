;; Named, replaceable additions surrounding the two-equation process core.
;; This file does not decide success or failure and owns no process root.
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/model_commentary.py)
!(import! &self ./src/strategy_host.py)
!(import! &self ./src/instance_hooks.metta)

(= (addition-context-sources)
   (if (tg:client-metta?)
       (let $conversation
            (if (== (py-call (strategy_host.conversation_mode)) "chat")
                (tg:conversation-window-for (tg:conversation-events) (tg:get reply-chat ""))
                (tg:route-view))
         (py-call (context_sources.bundle $conversation)))
       (py-call (context_sources.bundle))))

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
   (let $context (hierarchy:context)
     (if (== $context "") (state:prepare-context $request $tokens)
       (state:prepare-context (hierarchy:append-context $request $context) $tokens))))

(= (addition-prepare-request
      $selected $legacy $system $activity $advertised)
   (coding-prepare-request
     $selected $legacy $system $activity $advertised))

(= (addition-model-call $model $tokens $reasoning $request $wake)
   (case $request
     (((ContextPreparationFailed $reason) (py-call (strategy_host.preparation_failed $reason)))
      ($ready
        (let $wire-request (py-call (model_commentary.prompt $ready))
          (if (== (py-call (strategy_host.capture $wire-request $wake)) 1)
              (llm:command $model $tokens $reasoning $wire-request)
              (progn (py-call (cognitive_health.turn_failed "strategy-not-ready")) "()")))))))

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

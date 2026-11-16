;; Named, replaceable additions surrounding the two-equation process core.
;; This file does not decide success or failure and owns no process root.
!(import! &self ./src/native_llm.metta)
!(import! &self ./src/model_commentary_cetta.metta)
!(import! &self ./src/turn_frame_cetta.metta)
!(import! &self ./src/strategy_broker_cetta.metta)
!(import! &self ./src/strategy_attempts_cetta.metta)
!(import! &self ./src/runtime_support_cetta.metta)
!(import! &self ./src/instance_hooks.metta)
!(import! &self ./src/interop_flags_cetta.metta)
;; The native pins context view needs its own libraries, not whichever module
;; happened to load them first. On SWI-PeTTa it is never asked (see below).
!(import! &self ./src/pins_context_cetta.metta)
!(import! &self ./src/context_sources_cetta.metta)

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
   (addition-pins-views-native))
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
   (== (host:text (turn:conversation-mode)) "chat"))
(= (addition-context-sources)
   (let* (($views (addition-context-views))
          ($conversation
            (if (addition-chat-mode?)
                (tg:conversation-window-for (tg:conversation-events) (tg:get reply-chat ""))
                (tg:route-view))))
     (context-bundle:with-views $views $conversation)))

(= (addition-effect-turn-begin $turn)
   (let* (($_ (addition-reap-work))
          ($backend (system:getenv-or-default "METTACLAW_EFFECT_BACKEND" ""))
          ($state (system:getenv-or-default "METTACLAW_EFFECT_BACKEND_STATE" "")))
     (if (and (== $backend "") (== $state ""))
       (let $_ (tg:set effect-turn (turn:text $turn)) "LIVE_EFFECT_TURN_READY")
       (Error addition-effect-turn-begin (Forbidden qualification-backend-requires-oracle-runtime)))))

;; Coding sessions that ended without their report are recorded and their
;; requester told, at the next turn. Coding mode runs on CeTTa only.
(= (addition-reap-work)
   (case (collapse (catch (work:reap)))
     ((() 0) (($value) $value)
      ($invalid (Error coding-reap (Incomplete ambiguous-reap-result))))))

!(import! &self ./src/input_scope_cetta.metta)
(= (addition-strategy-begin $turn $source $message)
   (let* (($_ (turn:put "reply-chat" (if (== (commchannel) telegram) (tg:get reply-chat "") "")))
          ($receipt
       (if (input-scope:known?)
         (let $ids (input-scope:json) (turn:begin $turn $source $message $ids))
         (turn:begin $turn $source $message))))
     (if (turn:failed? $receipt)
       (let $_ (turn:put "phase" "failed") $receipt)
       (let* (($_ (hierarchy:begin $turn $source (turn:view)))
              ($_ (ws:begin (hierarchy:task) $source
                    (if (== $source "operator-event")
                      (tg:get reply-chat "") "")))) $receipt))))

(= (addition-protected-request $request)
   (addition-protected-request $request 32768))

(= (addition-protected-request $request $tokens)
   (let* (($formatted (request:format $request))
          ($context (hierarchy:context)))
     (if (== $context "") (state:prepare-context $formatted $tokens)
       (state:prepare-context (request:append $formatted $context) $tokens))))

(= (addition-prepare-request
      $selected $legacy $system $activity $advertised)
   (coding-prepare-request
     $selected $legacy $system $activity $advertised))

(= (addition-model-call $model $tokens $reasoning $request $wake)
   (let $begun (health:begin-turn)
     (case $request
       (((ContextPreparationFailed $reason) (health:paced (turn:preparation-failed $reason)))
        ($ready
          (if (== (turn:capture $ready $wake) 1)
              (llm:command $model $tokens $reasoning $ready)
              (let* (($recorded (health:fail "strategy-not-ready" 0)) ($failed (health:turn-failed))) "()")))))))

(= (addition-request-observation $request)
   (case $request
     (((ContextPreparationFailed $reason) (println! (CONTEXT_PREPARATION_FAILED: $reason)))
      ($ready (println! (CHARS_SENT: (string_length $ready) sha256 (str:sha256 $ready)))))))

(= (addition-balance-response $raw)
   (model-response-balance (model-response-normalize $raw)))
(= (addition-envelope-response $balanced)
   (model-response-envelope $balanced))
(= (addition-read-response $response)
   (model-response-read $response))
(= (addition-command-sequence $parsed)
   (model-response-commands $parsed))

; An admitted batch is open, under the broker's ticket, while its commands
; run (src/strategy_attempts_cetta.metta).
(= (addition-effect-broker $turn $limit $commands)
   (let* (($broker-turn (strategy:broker-turn $turn))
          ($_ (addition-effect-turn-begin $broker-turn))
          ($effective (strategy:batch-limit $limit (quote $commands)))
          (($admitted $ticket) (turn:broker-begin $broker-turn (quote $commands) $effective)))
     (if (== $admitted 1)
       (let* (($opened (strategy:open-batch $broker-turn $ticket))
              ($records (run-command-batch-once $broker-turn $effective (quote $commands)))
              ($reported (turn:broker-end $broker-turn (quote $records)))
              ($closed (strategy:close-batch)))
         (if (turn:failed? $reported) $reported $records))
       ((COMMAND_BATCH_WITHHELD: (turn:broker-feedback))))))

(= (addition-command-errors $turn)
   (command-batch-errors (strategy:broker-turn $turn)))

;; Named, replaceable additions surrounding the two-equation process core.
;; This file does not decide success or failure and owns no process root.
!(import! &self ./native_llm.metta)

(= (addition-context-sources)
   (if (tg:client-metta?)
       (let $conversation (tg:conversation-window (tg:conversation-events))
         (py-call (context_sources.bundle $conversation)))
       (py-call (context_sources.bundle))))

(= (addition-effect-turn-begin $turn)
   (py-call (effect_frontier.begin $turn)))

(= (addition-prepare-request
      $selected $legacy $system $activity $advertised)
   (coding-prepare-request
     $selected $legacy $system $activity $advertised))

(= (addition-model-call $model $tokens $reasoning $request)
   (llm:command $model $tokens $reasoning $request))

(= (addition-balance-response $raw)
   (model-response-balance $raw))
(= (addition-envelope-response $balanced)
   (model-response-envelope $balanced))
(= (addition-read-response $response)
   (model-response-read $response))
(= (addition-command-sequence $parsed)
   (model-response-commands $parsed))

(= (addition-effect-broker $turn $limit $commands)
   (run-command-batch-once $turn $limit (quote $commands)))

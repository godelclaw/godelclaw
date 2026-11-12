; The command broker's fixed answers, natively. strategy_host keeps the
; observed-strategy state (the captured sample, the effect ledger); these
; answers never read it.
!(import! &self (library system))

; Whether the observed strategy (the effect broker) is on for this process.
(= (strategy:enabled?) (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1"))
(= (strategy:enabled-flag) (if (strategy:enabled?) 1 0))

; Every call of an answer runs, in order: the batch limit is its length. The
; calls arrive quoted and are taken as written: they must stay unevaluated.
(: strategy:batch-limit (-> %Undefined% Atom %Undefined%))
(= (strategy:batch-limit $requested $commands)
   (case $commands (((quote $calls) (size-atom $calls)) ($calls (size-atom $calls)))))

(= (strategy:batch-description $requested)
   "Output a ((skillName args) ...) S-expression. Each call runs in order and returns its own result. Use another model step when choosing an action requires a result, or express conditional work in a script. ")

(= (strategy:description)
   "TASK_CONTEXT contains the request, earlier calls and their results. Resume the task after interruptions. task-history and task-read retrieve older evidence; task-note is an optional scratchpad. No disposition or resolution bookkeeping is required. context-policy adjusts the task context target. ")

; Without the observed strategy a broker turn is the loop's own turn; with it,
; the turn follows the captured sample the host keeps.
(= (strategy:broker-turn $turn)
   (if (strategy:enabled?) (py-call (strategy_host.broker_turn $turn)) $turn))

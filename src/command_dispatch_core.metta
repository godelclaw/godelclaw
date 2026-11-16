; Sequential MeTTa dispatcher. Callees precede callers for both engines.
; Command syntax and retained records remain data across helper calls.
(: dispatch:receipt (-> %Undefined% Atom Atom %Undefined% %Undefined%))
(: dispatch:result (-> %Undefined% Atom Atom %Undefined%))
(: dispatch:ordinary-invoke (-> Atom %Undefined%))
(: dispatch:invoke (-> Atom %Undefined%))
(: dispatch:ordinary (-> Atom %Undefined% %Undefined% %Undefined%))
(: dispatch:effect (-> Atom %Undefined%))
(: dispatch:command (-> %Undefined% Atom %Undefined%))
(: dispatch:forget (-> Atom %Undefined%))
(: dispatch:new (-> %Undefined% Atom Atom %Undefined%))
(= (dispatch:append $first $second) (if (== $first ()) $second (cons-atom (car-atom $first) (dispatch:append (cdr-atom $first) $second))))

(= (dispatch:receipt $turn $status $command $result) (eval (dispatch:record $turn $status (quote $command) $result)))

(= (dispatch:result $turn $command $value) (let* (($empty (== $value $command)) ($status (if $empty no_result (case $value (((Error $_ $_) exception) ($other returned))))) ($result (if $empty (Error command_failed "Unknown tool or no result; use (help) for available tools" $command) $value)) ($text (eval (dispatch:normalize (quote $result)))) ($errors (if (== $status returned) () (if (== $status no_result) ((command_returned_no_result $command "command returned no result")) ((command_raised_exception $command $text))))) ($_ (dispatch:receipt $turn $status $command $text))) (dispatch:one (quote (COMMAND_RETURN: ($command $text))) (quote $errors))))

(= (dispatch:ordinary-invoke $command)
   (let $error (eval (dispatch:argument-error (quote $command)))
     (if (== (eval (dispatch:length $error)) 0) (eval $command) $error)))

; Inspect raw command syntax; a rest call in a pattern can itself reduce in
; the PeTTa profile. Scheduling starts only after its arguments are bound.
(= (dispatch:rest-args $command $arguments)
   (if (== (size-atom $arguments) 2)
     (let* (($seconds (car-atom $arguments)) ($continuation (car-atom (cdr-atom $arguments))))
       (if (== (get-metatype $continuation) Expression)
         (let $body (if (== (car-atom $continuation) quote)
                        (car-atom (cdr-atom $continuation)) $continuation)
           (eval (rest-command $seconds (quote $body))))
         (dispatch:ordinary-invoke $command)))
     (dispatch:ordinary-invoke $command)))

(= (dispatch:invoke $command)
   (if (== (car-atom $command) rest)
       (dispatch:rest-args $command (cdr-atom $command))
       (dispatch:ordinary-invoke $command)))

(= (dispatch:ordinary $command $backend $state) (if (== (eval (dispatch:length $backend)) 0) (if (== (eval (dispatch:length $state)) 0) (dispatch:invoke $command) (Error command_failed "inconsistent shadow effect configuration")) (Error command_failed "unknown effect backend")))

(= (dispatch:effect $command) (let* (($backend (eval (dispatch:env "METTACLAW_EFFECT_BACKEND" ""))) ($state (eval (dispatch:env "METTACLAW_EFFECT_BACKEND_STATE" "")))) (if (== (eval (dispatch:compare $backend "tmux-shadow")) 0) (case (eval (dispatch:shadow (quote $command))) (((handled $value) $value) (("handled" $value) $value) ($error (Error invalid_shadow_effect_outcome $error)))) (dispatch:ordinary $command $backend $state))))

(: dispatch:reserved-command (-> %Undefined% Atom %Undefined%))
(= (dispatch:reserved-command $turn $command)
   (if (== (eval (dispatch:stopped)) 1)
     (let* (($reason "operator stopped cognition") ($_ (dispatch:receipt $turn withheld $command $reason)))
       (dispatch:one (quote (COMMAND_NOT_RUN: ($command $reason))) (quote ())))
     (let $answers (collapse (catch (dispatch:effect $command)))
       (let $value (if (== $answers ()) $command (car-atom $answers))
         (dispatch:result $turn $command $value)))))
(= (dispatch:command $turn $command)
   (let $reservation
       (if (== (eval (dispatch:compare (eval (dispatch:env "METTACLAW_OBSERVED_STRATEGY" "0")) "1")) 0)
         (eval (dispatch:reserve $turn (quote $command))) 0)
     (case $reservation
       (((Error $where $reason)
         (dispatch:admission-failed (quote (COMMAND_NOT_RUN: ($command (Error $where $reason))))
                                    (quote ((command_admission_failed $command $reason)))))
        ($accepted (dispatch:reserved-command $turn $command))))))

(= (dispatch:list $turn $commands)
   (if (== $commands ()) (dispatch:ran (quote ()) (quote ()))
     (let* (($command (car-atom $commands)) ($outcome (dispatch:command $turn $command)))
       (if (== (car-atom $outcome) dispatch:admission-failed)
         (let (dispatch:admission-failed (quote $record) (quote $errors)) $outcome
           (dispatch:ran (quote ($record)) (quote $errors)))
         (let* (((dispatch:one (quote $record) (quote $errors)) $outcome)
                ((dispatch:ran (quote $records) (quote $rest-errors)) (dispatch:list $turn (cdr-atom $commands)))
                ($joined-records (cons-atom $record $records))
                ($joined-errors (dispatch:append $errors $rest-errors)))
           (dispatch:ran (quote $joined-records) (quote $joined-errors)))))))

(= (dispatch:forget $cache) (case $cache ((((dispatch:cached $turn $commands $records $errors)) (remove-atom &self (dispatch:cache $turn $commands $records $errors))) ($other ()))))

(= (dispatch:new $turn $commands $cache) (let (dispatch:ran (quote $records) (quote $errors)) (let $_ (eval (dispatch:capture $turn)) (dispatch:list $turn $commands)) (progn (dispatch:forget $cache) (add-atom &self (dispatch:cache $turn (quote $commands) (quote $records) (quote $errors))) $records)))

(= (run-command-batch-once $turn $unused $commands) (let $cache (collapse (match &self (dispatch:cache $t $cs $rs $es) (dispatch:cached $t $cs $rs $es))) (case $cache ((((dispatch:cached $turn (quote $previous) (quote $records) $_)) (if (== (quote $previous) (quote $commands)) (quote $records) ((COMMAND_BATCH_REJECTED: turn_arguments_changed)))) ($other (dispatch:new $turn $commands $cache))))))

(= (command-batch-errors $turn) (let $errors (collapse (match &self (dispatch:cache $turn $_ $_ (quote $errors)) (quote $errors))) (if (== $errors ()) () (let (quote $result) (car-atom $errors) $result))))

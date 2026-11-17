; Context capacity and calibration use the existing persisted policy files.
!(import! &self ./state_ledger.metta)
!(import! &self ./turn_frame_cetta.metta)
!(import! &self ./model_selection_cetta.metta)
!(import! &self ./model_menu_cetta.metta)
!(import! &self ./llm_policy_cetta.metta)
!(import! &self (library math))
(= (budget:model) (model:current ""))
(= (budget:reported $rows $selected)
   (if (== $rows ()) JsonNull
     (case (car-atom $rows)
       (((JsonObject $pairs)
         (if (== (sf:get (JsonObject $pairs) "id" JsonNull) $selected)
           (sf:get (JsonObject $pairs) "context" JsonNull)
           (budget:reported (cdr-atom $rows) $selected)))
        ($other (budget:reported (cdr-atom $rows) $selected))))))
; Match the retained policy's truth test before int(value). A missing limit
; differs from a known limit that leaves zero input tokens. Convert once at
; this boundary; invalid nonempty data remains an explicit failure.
(= (budget:integer $value)
   (case (lp:python-int $value)
     (((PythonException $class $detail) (Error turn:context-capacity (PythonException $class $detail)))
      ($integer (if (== (get-type $integer) Number) $integer
                  (Error turn:context-capacity unsupported-window-integer))))))
(= (budget:window $value)
   (if (model:truthy? $value) (budget:integer $value) BudgetUnknownV1))
(= (budget:capacity $configured $reported $output)
   (let* (($a (budget:window $configured)) ($b (budget:window $reported)))
     (if (turn:failed? $a) $a
       (if (turn:failed? $b) $b
         (if (and (== $a BudgetUnknownV1) (== $b BudgetUnknownV1)) ""
           (let* (($limit (if (== $a BudgetUnknownV1) $b
                            (if (== $b BudgetUnknownV1) $a (min $a $b))))
                  ($reserve (budget:integer $output)))
             (if (turn:failed? $reserve) $reserve (max 0 (- $limit $reserve)))))))))
(= (turn:context-capacity $output)
   (let* (($model (budget:model))
          ($observations (model:observations))
          ($catalog (if (str:starts-with $model "kimi:") "kimi-models" "models"))
          ($listed (budget:reported (model:rows $observations $catalog) $model))
          ($window (sf:get (model:record $observations "model-window") "value" JsonNull))
          ($reported (if (!= $listed JsonNull) $listed
                       (if (== (sf:get $window "id" JsonNull) $model) (sf:get $window "context" JsonNull) JsonNull)))
          ($_ (if (== $listed JsonNull)
                 (model:refresh (if (== $catalog "kimi-models") ("models") ("model-window"))) True))
          ($configured (system:getenv-or-default "METTACLAW_CONTEXT_WINDOW_TOKENS" "")))
     (budget:capacity $configured $reported $output)))
(= (turn:context-input $key) (turn:context-input $key 32768))
(= (turn:context-input $key $tokens)
   (case $key
     (("focus" (turn:focus)) ("trigger" (turn:trigger))
      ("capacity" (turn:context-capacity $tokens)) ("model" (budget:model))
      ($other (Error turn:context-input $other)))))

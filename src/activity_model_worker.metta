!(import! &self ./activity_model_host.py)
!(py-call (activity_model_host.arm))
!(import! &self ./native_llm.metta)
; The captured request is prepared natively and owned like any agent call: one
; admission, one submission, and its state read back from the native ledger.
(= (activity:field $request $key)
   (case (llm:v1:get $request $key) (((JsonString $text) $text) ((JsonNumber $text) (parse $text)) ($other $other))))
; A worker can await serially: the separate durable owner keeps living.
; once avoids enumerating the successful submission a second time.
!(once (let* (($request (json-parse (fs:read-text (system:getenv-or-default "METTACLAW_ACTIVITY_INPUT" ""))))
              ($selected (model:current (activity:field $request "model")))
              ($plan (pcall:command-plan $selected (activity:field $request "tokens")
                                         (activity:field $request "effort") (activity:field $request "prompt")))
              ($result (llm:run $plan)))
          (py-call (activity_model_host.finish (call:host (llm:owner $plan)) $result))))

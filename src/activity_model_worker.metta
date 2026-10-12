!(import! &self ./activity_model_host.py)
!(py-call (activity_model_host.arm))
!(import! &self ./native_llm.metta)
; A worker can await serially: the separate durable owner keeps living.
; once avoids enumerating the successful submission a second time.
!(once (let* (($plan (py-call (activity_model_host.prepare)))
              ($result (llm:run $plan)))
          (py-call (activity_model_host.finish $plan $result))))

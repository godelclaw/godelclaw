;; One model request of a supervised Iter loop, through godelclaw's native
;; provider. The loop writes the request as JSON text to ITER_PROVIDER_REQUEST
;; and reads {"message", "finish", "usage"} or {"error"} from
;; ITER_PROVIDER_REPLY. With ITER_PROVIDER_SETTLE=1 the previous turn is first
;; recorded as settled. This process is the only Python the loop reaches.
!(import! &self ../../../src/native_llm.metta)
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))

(= (provider:env $name) (system:getenv-or-default $name ""))

;; A Python error as its type and message.
(= (provider:error-text $where $why)
   (case $where
     (((python_error $type $exception)
       (case (catch (py-call (builtins.str $exception)))
         (((Error $a $b) (repr $type))
          ($message (format-args "{}: {}" ($type $message))))))
      ($other (str:join " " ((repr $where) (repr $why)))))))

(= (provider:fail $text)
   (fs:write (provider:env "ITER_PROVIDER_REPLY")
             (json-stringify (JsonObject ((JsonPair "error" (JsonString $text)))))))

(= (provider:settle)
   (if (== (provider:env "ITER_PROVIDER_SETTLE") "1") (py-call (cognitive_health.turn_settled)) 0))

(= (provider:request $text)
   (let $plan (llm:structured-json-plan $text)
     (case $plan
       (((Error $where $why) (provider:fail (provider:error-text $where $why)))
        ($ready
         (let $box (llm:run $ready)
           (py-call (native_llm_host.structured_json_result $box (provider:env "ITER_PROVIDER_REPLY")))))))))

!(let* (($settled (catch (provider:settle)))
        ($text (fs:read-text (provider:env "ITER_PROVIDER_REQUEST")))
        ($outcome (catch (provider:request $text))))
   (case $outcome
     (((Error $where $why) (provider:fail (provider:error-text $where $why)))
      ($done $done))))

; Preserve safe native transport evidence without exporting the Error source,
; URL, headers, or peer-controlled text. PeTTa retains nested event errors;
; HE can lift the same error to the surrounding function boundary.
!(import! &self (library io))

(= (llm-transport:failure $code)
   (let $kind (if (== $code 28) "timeout" "transport")
     (NativeHttpFailureV1 $kind $code)))

;; A body the engine received and refused to keep: past the call's response
;; bound, past the response memory budget, not text, or carrying a
;; credential. The provider answered, so none of these is a transport failure.
(= (llm-transport:refusal? $reason)
   (or (== $reason response-budget-exceeded) (or (== $reason non-text-response) (== $reason credential-in-response))))
(= (llm-transport:refused $id $reason)
   (case $reason
     (((http:error response-too-large $bound) (io:event $id (NativeHttpRefusedV1 response-too-large $bound)))
      ((http:error $named)
       (if (llm-transport:refusal? $named) (io:event $id (NativeHttpRefusedV1 $named 0)) (io:failed)))
      ($other (io:failed)))))

(: llm-transport:wait (-> Number Number %Undefined%))
(= (llm-transport:wait $id $milliseconds)
   (function (case (io:wait $id $milliseconds)
     (((io:idle) (return (io:idle)))
      ((io:event $received (Error $source (http:error transport $code $text)))
       (let $failure (llm-transport:failure $code)
         (return (io:event $received $failure))))
      ((Error $source (http:error transport $code $text))
       (let $failure (llm-transport:failure $code)
         (return (io:event $id $failure))))
      ((io:event $received (Error $source $reason))
       (let $refused (llm-transport:refused $received $reason) (return $refused)))
      ((io:event $received $outcome) (return (io:event $received $outcome)))
      ((Error $source $reason) (let $refused (llm-transport:refused $id $reason) (return $refused)))
      ($other (return (io:failed)))))))

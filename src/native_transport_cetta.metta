; Preserve safe native transport evidence without exporting the Error source,
; URL, headers, or peer-controlled text. PeTTa retains nested event errors;
; HE can lift the same error to the surrounding function boundary.
!(import! &self (library io))

(= (llm-transport:failure $code)
   (let $kind (if (== $code 28) "timeout" "transport")
     (NativeHttpFailureV1 $kind $code)))

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
      ((io:event $received (Error $source $reason)) (return (io:failed)))
      ((io:event $received $outcome) (return (io:event $received $outcome)))
      ((Error $source $reason) (return (io:failed)))
      ($other (return (io:failed)))))))

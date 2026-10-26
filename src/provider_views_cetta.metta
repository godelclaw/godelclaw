; Pure projections of the native provider records. Storage selection and
; recovery run before these projections; no SQLite or Python is imported.
; Immutable ordinals preserve insertion order through record replacement.
!(import! &self (library rel))
!(import! &self (library json))
!(import! &self (library str))

(= (provider-view:call
     (ProviderCallV1 $id $ordinal $epoch $pid $start $created $updated $state $guard
       $transport $soft $bytes $quiet $result $reason $activity $lane $purpose $notified))
   (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
     $reason $purpose $notified $result))

(= (provider-view:turn?
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
       $reason $purpose $notified $result))
   (if (== $purpose "turn") RelTrue RelFalse))

(= (provider-view:newer?
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet $reason $purpose $notified $result)
     (ProviderCallView $id2 $ordinal2 $created2 $updated2 $state2 $soft2 $bytes2 $quiet2 $reason2 $purpose2 $notified2 $result2))
   (if (> $created $created2) True
     (if (== $created $created2) (< $ordinal $ordinal2) False)))

(= (provider-view:older?
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet $reason $purpose $notified $result)
     (ProviderCallView $id2 $ordinal2 $created2 $updated2 $state2 $soft2 $bytes2 $quiet2 $reason2 $purpose2 $notified2 $result2))
   (if (< $created $created2) True
     (if (== $created $created2) (< $ordinal $ordinal2) False)))

(= (provider-view:status-row
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
       $reason $purpose $notified $result))
   (JsonObject ((JsonPair "id" $id) (JsonPair "state" $state)
     (JsonPair "created" $created) (JsonPair "updated" $updated)
     (JsonPair "soft_elapsed" $soft) (JsonPair "bytes" $bytes)
     (JsonPair "quiet_ms" $quiet) (JsonPair "reason" $reason))))

(= (provider-view:status $records)
   (let* (($calls (rel:project provider-view:call $records))
          ($turns (rel:select provider-view:turn? $calls))
          ($ordered (rel:order provider-view:newer? $turns))
          ($recent (rel:page 0 12 $ordered))
          ($rows (rel:project provider-view:status-row $recent)))
     (JsonArray $rows)))

(= (provider-view:notice?
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
       $reason $purpose $notified $result))
   (if (== $purpose "turn")
     (if (== $notified 0)
       (if (or (== $state "late") (== $state "uncertain")) RelTrue RelFalse) RelFalse)
     RelFalse))

(= (provider-view:notice-row
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
       $reason $purpose $notified $result)) ($id $state))

(= (provider-view:notices $records)
   (let* (($calls (rel:project provider-view:call $records))
          ($pending (rel:select provider-view:notice? $calls))
          ($ordered (rel:order provider-view:older? $pending)))
     (rel:project provider-view:notice-row $ordered)))

(= (provider-view:field $object $key $default)
   (case $object (((JsonObject $pairs) (provider-view:lookup $pairs $key $default))
     ($other $default))))
(= (provider-view:lookup $pairs $key $default)
   (if (== $pairs ()) $default
     (let (JsonPair $name $value) (car-atom $pairs)
       (provider-view:lookup (cdr-atom $pairs) $key (if (== $name $key) $value $default)))))
(= (provider-view:array $value)
   (case $value (((JsonArray $items) $items) ($other ()))))

; Rejected structured output exposes only public assistant text. Provider
; reasoning parts and opaque replay data are never made into public prose.
(= (provider-view:text-parts $parts)
   (if (== $parts ()) ()
     (let* (($part (car-atom $parts)) ($rest (provider-view:text-parts (cdr-atom $parts)))
            ($text (provider-view:field $part "text" JsonNull)))
       (if (== (provider-view:field $part "kind" "") "text")
         (if (== (get-type $text) String) (cons-atom $text $rest) $rest) $rest))))
(= (provider-view:public-items $items)
   (if (== $items ()) ()
     (let* (($item (car-atom $items)) ($rest (provider-view:public-items (cdr-atom $items))))
       (if (== (provider-view:field $item "kind" "") "message")
         (if (== (provider-view:field $item "role" "") "assistant")
           (let $texts (provider-view:text-parts
               (provider-view:array (provider-view:field $item "parts" (JsonArray ()))))
             (rel:union-all $texts $rest)) $rest) $rest))))

(= (provider-view:result-row
     (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
       $reason $purpose $notified $result) $offset)
   (let* (($full (if (== $purpose "turn") $result JsonNull))
          ($rejected (if (== $full JsonNull) False
            (if (== (get-type $reason) String) (str:starts-with $reason "command-output:") False)))
          ($representation (if $rejected "assistant-text" "canonical"))
          ($text (if $rejected
            (str:join "\n" (provider-view:public-items
              (provider-view:array (provider-view:field (json:parse $full) "items" (JsonArray ())))))
            $full))
          ($count (if (== $text JsonNull) 0 (str:char-length $text)))
          ($digest (str:sha256 (if (== $text JsonNull) "" $text)))
          ($chunk (if (== $text JsonNull) JsonNull
            (if (>= $offset $count) "" (str:char-slice $text $offset (+ $offset 2400))))))
     (provider-view:result-chunk $id $state $purpose $reason $representation
       (not (== $text JsonNull)) $digest $count $offset $chunk)))

(= (provider-view:result-chunk $id $state $purpose $reason $representation
     $retained $digest $total $offset $chunk)
   (let* (($length (if (== $chunk JsonNull) 0 (str:char-length $chunk)))
          ($end (+ $offset $length))
          ($value (JsonObject ((JsonPair "id" $id) (JsonPair "state" $state)
            (JsonPair "purpose" $purpose) (JsonPair "result" $chunk)
            (JsonPair "representation" $representation) (JsonPair "offset" $offset)
            (JsonPair "evidence_retained" $retained) (JsonPair "reason" $reason)
            (JsonPair "action_authority" False) (JsonPair "result_digest" $digest)
            (JsonPair "result_chars" $total)
            (JsonPair "next_offset" (if (< $end $total) $end JsonNull))
            (JsonPair "truncated" (or (> $offset 0) (< $end $total))))))
          ($encoded (json:stringify-python $value)))
     (if (<= (str:char-length $encoded) 2400) $encoded
       (if (== $length 0) $encoded
         (provider-view:result-chunk $id $state $purpose $reason $representation
           $retained $digest $total $offset (str:char-slice $chunk 0 (/ (- $length (% $length 2)) 2)))))))

; Identity and offset are validated at the public command boundary. A
; missing identity is JsonNull, preserving the archived-job fallback signal.
(= (provider-view:result $records $identity $offset)
   (if (< $offset 0) (ProviderViewError "result offset must be nonnegative")
     (if (== $records ()) JsonNull
       (let $row (provider-view:call (car-atom $records))
         (let (ProviderCallView $id $ordinal $created $updated $state $soft $bytes $quiet
             $reason $purpose $notified $result) $row
           (if (== $id $identity) (provider-view:result-row $row $offset)
             (provider-view:result (cdr-atom $records) $identity $offset)))))))

; A pure usage observation. Missing counters are JsonNull, never zero.
; Ordinary JSON values retain Int/BigInt versus Float/Bool distinctions.
!(case (import! &self (library json))
     (((Error $where $why)
       (let $reason (format-args "{}" ($why))
         (if (== $reason "import! expects a module name")
           (import! &self json) (Error $where $why))))
      ($loaded $loaded)))
!(case (import! &self (library str))
     (((Error $where $why)
       (let $reason (format-args "{}" ($why))
         (if (== $reason "import! expects a module name")
           (import! &self str) (Error $where $why))))
      ($loaded $loaded)))

; Compare computed values, including HE's syntax-taking equality boundary.
(: ue:equal (-> %Undefined% %Undefined% Bool))
(= (ue:equal $left $right) (== $left $right))

(: ue:field (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (ue:field $object $key $default)
   (case $object
     (((JsonObject $pairs) (ue:lookup $pairs $key $default))
      ($other $default))))
(: ue:object? (-> %Undefined% %Undefined%))
(= (ue:object? $value)
   (case $value (((JsonObject $ignored0) True) ($other False))))
(: ue:lookup (-> %Undefined% %Undefined% %Undefined% %Undefined%))
(= (ue:lookup $pairs $key $default)
   (if (ue:equal $pairs ()) $default
     (let (JsonPair $name $child) (car-atom $pairs)
       (ue:lookup (cdr-atom $pairs) $key (if (ue:equal $name $key) $child $default)))))
(: ue:truthy (-> %Undefined% %Undefined%))
(= (ue:truthy $value)
   (case $value
     ((JsonNull False) (False False) ("" False)
      ((JsonArray ()) False) ((JsonObject ()) False)
      ($other (if (ue:equal (get-type $other) Number) (not (ue:equal $other 0)) True)))))
(: ue:count (-> %Undefined% %Undefined%))
(= (ue:count $value)
   (if (ue:equal (get-type $value) Number)
     (let $spelling (json:stringify $value)
       (if (and (not (ue:equal $spelling "")) (str:is-all-in digit $spelling)) $value JsonNull)) JsonNull))
(: ue:append (-> %Undefined% %Undefined% %Undefined%))
(= (ue:append $first $second)
   (if (ue:equal $first ()) $second
     (let* (($head (car-atom $first)) ($rest (ue:append (cdr-atom $first) $second)))
       (cons-atom $head $rest))))
(: ue:count-pairs (-> %Undefined% %Undefined% %Undefined%))
(= (ue:count-pairs $object $keys)
   (if (ue:equal $keys ()) ()
     (let* (($key (car-atom $keys)) ($count (ue:count (ue:field $object $key JsonNull)))
            ($rest (ue:count-pairs $object (cdr-atom $keys))))
       (if (ue:equal $count JsonNull) $rest (cons-atom (JsonPair $key $count) $rest)))))
(: ue:detail-pairs (-> %Undefined% %Undefined% %Undefined%))
(= (ue:detail-pairs $object $keys)
   (if (ue:equal $keys ()) ()
     (let* (($key (car-atom $keys)) ($value (ue:field $object $key JsonNull))
            ($pairs (ue:count-pairs $value
                     ("cached_tokens" "reasoning_tokens" "audio_tokens"
                      "accepted_prediction_tokens" "rejected_prediction_tokens")))
            ($rest (ue:detail-pairs $object (cdr-atom $keys))))
       (if (ue:equal $pairs ()) $rest (cons-atom (JsonPair $key (JsonObject $pairs)) $rest)))))
(: ue:reported (-> %Undefined% %Undefined%))
(= (ue:reported $object)
   (JsonObject (ue:append
     (ue:count-pairs $object
       ("prompt_tokens" "input_tokens" "completion_tokens" "output_tokens" "total_tokens"
        "cache_read_input_tokens" "cache_creation_input_tokens" "prompt_cache_hit_tokens"
        "prompt_cache_miss_tokens" "reasoning_tokens"))
     (ue:detail-pairs $object
       ("input_tokens_details" "prompt_tokens_details" "output_tokens_details" "completion_tokens_details")))))
(: ue:incoming (-> %Undefined% %Undefined% %Undefined%))
(= (ue:incoming $original $reported)
   (let $input (ue:field $reported "prompt_tokens" (ue:field $reported "input_tokens" JsonNull))
     (if (ue:equal (ue:field $original "cache_read_input_tokens" JsonMissingV1) JsonMissingV1) $input
       (let* (($read (ue:field $reported "cache_read_input_tokens" JsonNull))
              ($write (ue:count (ue:field $original "cache_creation_input_tokens" 0))))
         (if (or (ue:equal $input JsonNull) (or (ue:equal $read JsonNull) (ue:equal $write JsonNull)))
           JsonNull (+ $input (+ $read $write)))))))
(: ue:cached (-> %Undefined% %Undefined% %Undefined%))
(= (ue:cached $reported $incoming)
   (let* (($details (ue:field $reported "prompt_tokens_details" (ue:field $reported "input_tokens_details" (JsonObject ()))))
          ($cached (ue:field $details "cached_tokens"
                    (ue:field $reported "cache_read_input_tokens" (ue:field $reported "prompt_cache_hit_tokens" JsonNull)))))
     (if (or (ue:equal $incoming JsonNull) (ue:equal $cached JsonNull)) $cached
       (if (> $cached $incoming) JsonNull $cached))))
(: ue:evidence (-> %Undefined% %Undefined%))
(= (ue:evidence $object)
   (let* (($reported (ue:reported $object)) ($incoming (ue:incoming $object $reported))
          ($cached (ue:cached $reported $incoming))
          ($outgoing (ue:field $reported "completion_tokens" (ue:field $reported "output_tokens" JsonNull)))
          ($all (and (not (ue:equal $incoming JsonNull)) (and (not (ue:equal $cached JsonNull)) (not (ue:equal $outgoing JsonNull)))))
          ($any (or (not (ue:equal $incoming JsonNull)) (or (not (ue:equal $cached JsonNull)) (not (ue:equal $outgoing JsonNull)))))
          ($status (if $all "reported" (if $any "partial" "unknown"))))
     (JsonObject
       ((JsonPair "counts" (JsonObject ((JsonPair "cached_tokens" $cached)
                    (JsonPair "input_tokens" $incoming) (JsonPair "output_tokens" $outgoing))))
        (JsonPair "reported_usage" $reported)
        (JsonPair "usage_status" $status)))))

; Completion effects of a provider call, recorded natively once its completion
; is committed in the ledger: the completion observation and its usage line,
; the context-token calibration of the model the call captured, and the
; Anthropic usage tally. Each is an observation for status, budgeting and quota
; views; failing to record one never discards the committed answer.
;
; Values are ordinary JSON (json:parse); files are written as Python's
; json.dumps wrote them (", " and ": ", ASCII escapes), so their readers and
; existing files are unchanged. In both extended dialects constructors and
; cons-atom keep their arguments as written: every computed part is bound
; first.
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library path))
!(import! &self (library str))
!(import! &self (library fs))
!(import! &self ./llm_policy_cetta.metta)

; A member of an ordinary JSON object; the last occurrence wins, as in Python.
(= (effects:get $object $key $default)
   (case $object
     (((JsonObject $pairs) (effects:lookup $pairs $key $default)) ($other $default))))
(= (effects:lookup $pairs $key $found)
   (if (== $pairs ()) $found
     (let (JsonPair $name $value) (car-atom $pairs)
       (effects:lookup (cdr-atom $pairs) $key (if (== $name $key) $value $found)))))
(= (effects:has? $object $key)
   (not (== (effects:get $object $key EffectsMissingV1) EffectsMissingV1)))
(= (effects:object? $value) (case $value (((JsonObject $pairs) True) ($other False))))
; Python truth of a JSON value.
(= (effects:truthy $value)
   (case $value
     ((JsonNull False) (False False) (True True) ("" False)
      ((JsonArray ()) False) ((JsonObject ()) False)
      ($other (if (== (get-type $other) Number) (not (== $other 0)) True)))))
(= (effects:or $first $second) (if (effects:truthy $first) $first $second))

; A token count: an integer from 0 to 10^12, as provider_protocol counted one.
(= (effects:count? $value)
   (and (== (get-type $value) Number) (== (effects:count $value) $value)))
(= (effects:count $value)
   (if (== (get-type $value) Number)
     (let $spelling (json:stringify $value)
       (if (and (not (== $spelling "")) (and (str:is-all-in digit $spelling) (<= $value 1000000000000)))
         $value JsonNull))
     JsonNull))

; provider_protocol.cache_observation: the reported input and cached prompt
; tokens, or an unknown status.
(= (effects:cache-observation $usage)
   (if (not (effects:object? $usage)) (JsonObject ((JsonPair "status" "unknown")))
     (let* (($details (effects:or (effects:or (effects:get $usage "prompt_tokens_details" JsonNull)
                                              (effects:get $usage "input_tokens_details" JsonNull))
                                  (JsonObject ())))
            ($detailed (if (effects:object? $details) (effects:count (effects:get $details "cached_tokens" JsonNull)) JsonNull))
            ($cached (if (== $detailed JsonNull)
                       (effects:count (effects:get $usage "cache_read_input_tokens"
                                        (effects:get $usage "prompt_cache_hit_tokens" JsonNull)))
                       $detailed))
            ($reported (effects:count (effects:get $usage "prompt_tokens" (effects:get $usage "input_tokens" JsonNull))))
            ($total (if (and (effects:has? $usage "cache_read_input_tokens") (not (== $reported JsonNull)))
                      (let $write (effects:count (effects:get $usage "cache_creation_input_tokens" 0))
                        (if (or (== $cached JsonNull) (== $write JsonNull)) JsonNull (+ $reported (+ $cached $write))))
                      $reported)))
       ; `or` evaluates every side: compare only known counts.
       (if (if (== $total JsonNull) True (if (== $cached JsonNull) True (> $cached $total)))
         (JsonObject ((JsonPair "status" "unknown")))
         (let $fraction (if (== $total 0) 0.0 (/ (* 1.0 $cached) $total))
           (JsonObject ((JsonPair "status" "reported") (JsonPair "input_tokens" $total)
                        (JsonPair "cached_tokens" $cached) (JsonPair "fraction" $fraction))))))))

(= (effects:append $front $back)
   (if (== $front ()) $back
     (let* (($head (car-atom $front)) ($tail (cdr-atom $front)) ($rest (effects:append $tail $back)))
       (cons-atom $head $rest))))
(= (effects:pairs $object) (case $object (((JsonObject $pairs) $pairs) ($other ()))))
; The usage evidence's counts that are known, in their order.
(= (effects:known-counts $pairs)
   (if (== $pairs ()) ()
     (let* (((JsonPair $key $count) (car-atom $pairs)) ($rest (effects:known-counts (cdr-atom $pairs))))
       (if (== $count JsonNull) $rest (cons-atom (JsonPair $key $count) $rest)))))

; A file replaced at once: written beside its target, then renamed over it.
(= (effects:replace $path $text)
   (let* (($id (system:uuid4-hex)) ($suffix (format-args ".{}.tmp" ($id)))
          ($temporary (str:concat $path $suffix))
          ($written (fs:write $temporary $text)))
     (case (fs:rename $temporary $path)
       ((() True) ($failed (let $_ (fs:remove $temporary) False))))))

; The completion observation (llm-completion.json) and one usage line per call
; (llm-usage.jsonl, created 0600). No message, tool argument or credential.
(= (effects:completion-path)
   (let $configured (system:getenv-or-default "METTACLAW_LLM_COMPLETION_PATH" "")
     (if (not (== $configured "")) $configured
       (let $health (system:getenv-or-default "METTACLAW_COGNITIVE_HEALTH_PATH" "")
         (if (== $health "") "" (path:join (path:dirname $health) "llm-completion.json"))))))
(= (effects:completion $result $layout $context $evidence)
   (let* (($now (system:wall-time)) ($path (effects:completion-path)))
     (effects:completion-at $result $layout $context $evidence $now $path)))
; $now is the observation time; the files are written under $path's directory.
(= (effects:completion-at $result $layout $context $evidence $now $path)
   (if (== $path "") True
     (let* (($usage (effects:object-or-empty (effects:get $result "usage" (JsonObject ()))))
            ($choice (case (effects:get $result "choices" (JsonArray ()))
                       (((JsonArray $items) (if (== $items ()) (JsonObject ()) (car-atom $items))) ($other (JsonObject ())))))
            ($reason (effects:get $choice "finish_reason" JsonNull))
            ($stop (effects:get $result "provider_stop" JsonNull))
            ($cache (effects:cache-observation $usage))
            ($base ((JsonPair "finish_reason" $reason) (JsonPair "provider_stop" $stop) (JsonPair "usage" $usage)
                    (JsonPair "transport" "native") (JsonPair "observed_at" $now) (JsonPair "cache" $cache)))
            ($fields (if (== $layout JsonNull) $base (effects:append $base ((JsonPair "prompt_layout" $layout)))))
            ($value (JsonObject $fields))
            ($parent (path:dirname $path))
            ($made (catch (fs:make-directories $parent 511)))
            ($replaced (catch (effects:replace $path (json:stringify-python $value))))
            ($output (effects:get $usage "output_tokens" (effects:get $usage "completion_tokens" JsonNull)))
            ($head ((JsonPair "observed_at" $now) (JsonPair "transport" "native")
                    (JsonPair "provider_stop" $stop) (JsonPair "output_tokens" $output)))
            ; dict(...) then line[key] = count and line.update(context): a
            ; known key keeps its place, a new one is appended.
            ($counts (effects:known-counts (effects:pairs (effects:get $evidence "counts" (JsonObject ())))))
            ($line (effects:put-all (effects:put-all (effects:put-all (JsonObject $head) (effects:pairs $cache)) $counts)
                                    (effects:pairs $context)))
            ($appended (catch (effects:append-line (path:join $parent "llm-usage.jsonl") (json:stringify-python $line)))))
       True)))
(= (effects:object-or-empty $value) (if (effects:object? $value) $value (JsonObject ())))
(= (effects:put-all $object $pairs)
   (if (== $pairs ()) $object
     (let (JsonPair $key $value) (car-atom $pairs)
       (effects:put-all (effects:put $object $key $value) (cdr-atom $pairs)))))
(= (effects:append-line $path $text)
   (let $owner (fs:open-write $path ("append" "create") 384)
     (case $owner
       (((NativeHandle $kind $id)
         (let* (($written (fs:write-all $owner (str:concat $text "\n"))) ($closed (fs:close $owner))) True))
        ($refused False)))))

; context_budget.observe_bytes: the captured model's bytes per input token,
; beside the engine state, when the provider reported a positive count.
(= (effects:calibration-path)
   (let $state (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH"
                 (system:getenv-or-default "METTACLAW_STRATEGY_PATH" ""))
     (if (== $state "") "" (path:join (path:dirname $state) "context-token-calibration.json"))))
(= (effects:calibrate $bytes $usage $model) (effects:calibrate-at $bytes $usage $model (effects:calibration-path)))
(= (effects:calibrate-at $bytes $usage $model $path)
   (let* (($observed (effects:get (effects:cache-observation $usage) "input_tokens" JsonNull))
          ($count (effects:or (effects:or $observed (effects:get $usage "prompt_tokens" JsonNull))
                              (effects:get $usage "input_tokens" JsonNull))))
     (if (or (== $path "") (not (effects:positive-int? $count))) True
       (let* (($made (catch (fs:make-directories (path:dirname $path) 511)))
              ($values (case (catch (json:parse (fs:read-text $path)))
                         (((JsonObject $pairs) (JsonObject $pairs)) ($other (JsonObject ())))))
              ($ratio (/ (* 1.0 $bytes) (lp:python-int $count)))
              ($entry (JsonObject ((JsonPair "bytes_per_token" $ratio) (JsonPair "input_tokens" $count)))))
         (if (> $ratio 0)
           (let* (($updated (effects:put $values $model $entry)) ($text (json:stringify-python $updated)))
             (catch (effects:replace $path $text)))
           True)))))
; isinstance(value, int) and value > 0, as Python sees JSON: true is 1.
(= (effects:positive-int? $value)
   (if (== (get-type $value) Bool) $value
     (if (== (get-type $value) Number)
       (if (== (lp:number-kind $value) NumberInt) (> $value 0) False) False)))
; A dictionary update: an existing key keeps its position.
(= (effects:put $object $key $value)
   (let* (($pairs (effects:pairs $object)) ($updated (effects:put-pairs $pairs $key $value)))
     (JsonObject $updated)))
(= (effects:put-pairs $pairs $key $value)
   (if (== $pairs ()) ((JsonPair $key $value))
     (let* (($head (car-atom $pairs)) ($tail (cdr-atom $pairs)) ((JsonPair $name $old) $head))
       (if (== $name $key) (cons-atom (JsonPair $key $value) $tail)
         (let $rest (effects:put-pairs $tail $key $value) (cons-atom $head $rest))))))

; synthetic_llm._tally_anthropic: today's and total input and output tokens.
; Each count is read as Python's int() reads it: an absent count is 0, a
; cache count's false value is 0, and a value int() refuses (null, a list, a
; non-numeric text) leaves the tally unchanged.
(= (effects:tally-path)
   (system:getenv-or-default "METTACLAW_ANTHROPIC_USAGE_PATH" "memory/anthropic_usage.json"))
(= (effects:tokens $usage $key $or-zero)
   (let $value (effects:get $usage $key 0)
     (if (and $or-zero (not (effects:truthy $value))) 0
       (case (effects:python-int $value)
         (((PythonException $class $why) EffectsInvalidCountV1) ($n $n))))))
(= (effects:python-int $value)
   (if (or (== (get-type $value) Number) (or (== (get-type $value) Bool) (== (get-type $value) String)))
     (lp:python-int $value)
     (PythonException TypeError "int() argument must be a string or a number")))
(= (effects:tally $usage)
   (let* (($now (system:wall-time)) ($today (system:format-time "%Y-%m-%d" $now "local")))
     (effects:tally-at $usage $today (effects:tally-path))))
(= (effects:tally-at $usage $today $path)
   (let* (($input (effects:tokens $usage "input_tokens" False))
          ($written (effects:tokens $usage "cache_creation_input_tokens" True))
          ($read (effects:tokens $usage "cache_read_input_tokens" True))
          ($out (effects:tokens $usage "output_tokens" False)))
     (if (or (== $input EffectsInvalidCountV1) (or (== $written EffectsInvalidCountV1)
            (or (== $read EffectsInvalidCountV1) (== $out EffectsInvalidCountV1)))) False
       (let* (($in (+ $input (+ $written $read)))
              ($old (case (catch (json:parse (fs:read-text $path)))
                      (((JsonObject $pairs) (JsonObject $pairs)) ($other (JsonObject ())))))
              ($since (effects:get $old "since" $today))
              ($old-in (effects:get $old "total_in" 0)) ($old-out (effects:get $old "total_out" 0))
              ($day (if (== (effects:get $old "today" JsonNull) $today) $old
                      (JsonObject ((JsonPair "today" $today) (JsonPair "in" 0) (JsonPair "out" 0)
                                   (JsonPair "since" $since) (JsonPair "total_in" $old-in)
                                   (JsonPair "total_out" $old-out)))))
              ($day-in (+ (effects:get $day "in" 0) $in)) ($day-out (+ (effects:get $day "out" 0) $out))
              ($total-in (+ (effects:get $day "total_in" 0) $in)) ($total-out (+ (effects:get $day "total_out" 0) $out))
              ($value (effects:put-all $day ((JsonPair "in" $day-in) (JsonPair "out" $day-out)
                                            (JsonPair "total_in" $total-in) (JsonPair "total_out" $total-out)))))
         (catch (effects:replace $path (json:stringify-python $value)))))))

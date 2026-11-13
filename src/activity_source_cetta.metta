; Settling a checkpointed activity batch at its source, natively. A batch is
; its ids joined by commas: runtime occurrences ("runtime:..."), settled once
; the input ledger has committed them, and channel handover inputs (Bot API
; update ids), marked done in the mode handover folder and processed in the
; ledger. The whole batch is validated before anything is settled, so an
; invalid id settles nothing. Each entry answers 1 or 0, as the Telegram
; client's acknowledgement expects.
!(import! &self ./src/ledger_client.metta)
!(import! &self (library fs))
!(import! &self (library path))

; Where a batch settles: the ledger socket ("" when the agent keeps none),
; whether observed strategy settles inputs through it, and the mode state
; folder that holds handover inputs ("" when the engine keeps no state).
(= (activity-source:config)
   (ActivitySourceConfigV1
     (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" "")
     (== (system:getenv-or-default "METTACLAW_OBSERVED_STRATEGY" "0") "1")
     (activity-source:mode-root)))
(= (activity-source:mode-root)
   (let $engine (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH" "")
     (if (== $engine "") ""
       (let $configured (system:getenv-or-default "METTACLAW_MODE_STATE_ROOT" "")
         (if (== $configured "") (path:join (path:dirname $engine) "modes") $configured)))))

;; ------------------------------------------------------------ the ids

; A runtime occurrence: "runtime:" and 1 to 160 of [A-Za-z0-9_.:-].
(= (activity-source:runtime-char? $c)
   (or (and (>= $c 48) (<= $c 57))
       (or (and (>= $c 65) (<= $c 90))
           (or (and (>= $c 97) (<= $c 122))
               (or (== $c 95) (or (== $c 46) (or (== $c 58) (== $c 45))))))))
(= (activity-source:runtime-chars? $codes)
   (if (== $codes ()) True
     (if (activity-source:runtime-char? (car-atom $codes))
       (activity-source:runtime-chars? (cdr-atom $codes)) False)))
(= (activity-source:runtime-id? $id)
   (let $rest (str:byte-slice $id 8 (str:byte-length $id))
     (and (and (>= (str:byte-length $rest) 1) (<= (str:byte-length $rest) 160))
          (activity-source:runtime-chars? (str:python-codepoints $rest)))))
; A handover input: a Bot API update id, a canonical decimal below 2^63.
(= (activity-source:update-id? $id)
   (let $length (str:byte-length $id)
     (and (and (>= $length 1) (<= $length 19))
       (and (str:is-all-in digit $id)
         (and (or (== $id "0") (not (str:starts-with $id "0")))
              (or (< $length 19) (< (str:compare $id "9223372036854775808") 0)))))))

; The batch's runtime ids and its update ids, each in order and the update
; ids without repeats (unique-atom keeps each first occurrence in linear
; expected time), or ActivityBatchRefusedV1.
(= (activity-source:batch $text)
   (activity-source:classify (if (== $text "") () (str:split "," $text)) () ()))
(= (activity-source:classify $ids $runtime $updates)
   (if (== $ids ())
     (let* (($in-order (activity-source:reversed $runtime ()))
            ($distinct (unique-atom (activity-source:reversed $updates ()))))
       (ActivityBatchV1 $in-order $distinct))
     (let* (($id (car-atom $ids)) ($rest (cdr-atom $ids)))
       (if (str:starts-with $id "runtime:")
         (if (activity-source:runtime-id? $id)
           (activity-source:classify $rest (cons-atom $id $runtime) $updates)
           ActivityBatchRefusedV1)
         (if (activity-source:update-id? $id)
           (activity-source:classify $rest $runtime (cons-atom $id $updates))
           ActivityBatchRefusedV1)))))
(= (activity-source:reversed $items $out)
   (if (== $items ()) $out (activity-source:reversed (cdr-atom $items) (cons-atom (car-atom $items) $out))))

(= (activity-source:nonempty $items $out)
   (if (== $items ()) (activity-source:reversed $out ())
     (let $item (car-atom $items)
       (activity-source:nonempty (cdr-atom $items) (if (== $item "") $out (cons-atom $item $out))))))

;; ------------------------------------------------------------ settlement

; Whether the ledger has committed every one of the ids. Without observed
; strategy nothing is settled through the ledger, so nothing is held back;
; with it, only the ledger's own answer settles, and no ledger settles nothing.
(= (activity-source:committed? (ActivitySourceConfigV1 $socket $observed $modes) $ids)
   (if (not $observed) True
     (if (== $socket "") False
       (case (catch (ledger-client:call $socket "committed" ($ids)))
         ((True True) (true True) ($other False))))))

; Handover inputs are marked done in their folder, then processed in the
; ledger. A missing input file was settled before; a failed rename or a
; refused ledger write settles nothing more and answers 0, and the next
; boundary retries this idempotent transition.
(= (activity-source:mark-done $folder $ids)
   (if (== $ids ()) True
     (let* (($json (path:join $folder (str:concat (car-atom $ids) ".json")))
            ($done (path:join $folder (str:concat (car-atom $ids) ".done")))
            ($moved (if (fs:is-file $json) (activity-source:renamed (fs:rename $json $done)) True)))
       (if $moved (activity-source:mark-done $folder (cdr-atom $ids)) False))))
(= (activity-source:renamed $result)
   (case $result (((fs:failed $kind $op $path $code $progress $why) False) ($done True))))
(= (activity-source:processed (ActivitySourceConfigV1 $socket $observed $modes) $ids)
   (if (or (== $ids ()) (== $socket "")) True
     (let $now (system:wall-time)
       (case (catch (ledger-client:call $socket "mark-processed" ($now $ids)))
         ((True True) (true True) ($other False))))))
(= (activity-source:handover (ActivitySourceConfigV1 $socket $observed $modes) $ids)
   (let $marked (if (== $modes "") True
                  (activity-source:mark-done (path:join $modes "channel-handover") $ids))
     (if $marked
       (if (activity-source:processed (ActivitySourceConfigV1 $socket $observed $modes) $ids) 1 0)
       0)))

; Settle one batch under one configuration.
(= (activity-source:ack-with $config $text)
   (case (activity-source:batch $text)
     (((ActivityBatchV1 $runtime $updates)
       (if (and (not (== $runtime ())) (not (activity-source:committed? $config $runtime))) 0
         (if (== $updates ()) 1 (activity-source:handover $config $updates))))
      ($refused 0))))
(= (activity-source:ack $ids) (activity-source:ack-with (activity-source:config) (activity-source:text $ids)))
; Ids arrive as text; a single update id may arrive as a number.
(= (activity-source:text $value)
   (if (== (get-type $value) Number) (repr $value) $value))

; Whether one input, or every input of a batch, is committed in the ledger:
; 1 or 0, as the Telegram client reads it. An input is never committed
; without observed strategy and a ledger.
(= (activity-source:committed $value)
   (let* (($config (activity-source:config)) ($text (activity-source:text $value)))
     (case $config
       (((ActivitySourceConfigV1 $socket $observed $modes)
         (if (not $observed) 1
           (let $ids (activity-source:nonempty (str:split "," $text) ())
             (if (== $socket "") 0 (if (activity-source:committed? $config $ids) 1 0)))))))))
(= (activity-source:already-committed $value)
   (let $id (activity-source:text $value)
   (case (activity-source:config)
     (((ActivitySourceConfigV1 $socket $observed $modes)
       (if (or (not $observed) (== $id "")) 0
         (if (activity-source:committed? (ActivitySourceConfigV1 $socket $observed $modes) ($id)) 1 0)))))))

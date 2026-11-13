; The turn-failure notice: when model turns fail, the operator is told once
; per failure episode, by the command responder, without a model call, and no
; crash between its steps loses the notice or sends a second one.
;
; - An episode is the turn health record's failure_episode, open until a
;   completed turn resolves it (src/turn_health_cetta.metta). A new outage
;   after that is a new episode with a notice of its own.
; - The notice is reserved in the ledger before it is published: the ledger
;   keeps the first intent (key and bytes) written under the episode's id and
;   answers it to every later attempt, so a retry publishes the same key with
;   the same bytes. Publishing writes the intent into the mode outbox, which
;   the responder's pump submits (channels/telegram_control_io.metta); the
;   ledger then marks it queued, and a queued notice is never published again,
;   after a restart either. This process remembers the queued episodes it has
;   seen, so it does not ask the ledger again every cycle.
; - Without a ledger, while it cannot answer, or without an operator chat or
;   outbox, nothing is published; the next cycle tries again.
; - The text names only host labels and the configured provider and model:
;   provider bodies and exception text may carry credentials.
!(import! &self (library json))
!(import! &self (library math))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/turn_health_cetta.metta)
!(import! &self ./src/ledger_client.metta)
!(import! &self ./src/effect_receipt_values_cetta.metta)
!(import! &self ./channels/telegram_control_io.metta)

!(bind! &fnote-queued (new-space))

(= (fnote:socket) (system:getenv-or-default "METTACLAW_INPUT_LEDGER_SOCKET" ""))

; The open episode of the health record, or None.
(= (fnote:open-episode $record)
   (case (sf:get $record "failure_episode" JsonNull)
     (((JsonObject $pairs)
       (let $episode (JsonObject $pairs)
         (if (== $pairs ()) None
           (if (health:truthy? (sf:get $episode "resolved_at" JsonNull)) None $episode))))
      ($other None))))

(= (fnote:reason $kind)
   (if (== $kind "provider-rejected-429")
     "HTTP 429: the provider refused the request (rate or quota limit). Check its balance/limits, or choose a model with /models."
     (if (or (== $kind "provider-rejected-401") (== $kind "provider-rejected-403"))
       "The provider refused authorization. Check its credentials/access, or choose a model with /models."
       (str:join "" ("The turn failed: " (erv:str $kind)
                     ". /activity reports the retry or blocked state; /models remains available.")))))

; The notice's text, or None when the episode lacks its kind, provider or
; first failure time.
(= (fnote:text $episode)
   (let* (($kind (sf:get $episode "kind" FnoteMissing))
          ($provider (sf:get $episode "provider" FnoteMissing))
          ($first (sf:get $episode "first_at" FnoteMissing)))
     (if (or (== $kind FnoteMissing) (or (== $provider FnoteMissing) (not (== (get-type $first) Number)))) None
       (let* (($identity (json:stringify-python-with
                           (JsonObject ((JsonPair "provider" $provider) (JsonPair "model" (sf:get $episode "model" ""))))
                           ((ensure-ascii True))))
              ($stamp (system:format-time "%Y-%m-%d %H:%M:%S UTC" (math:floor $first) "utc")))
         (str:join "" ("Turn failure observed at " $stamp ": " (fnote:reason $kind)
                       "\nAttempted configuration: " $identity
                       "\nYour messages are retained. No model was switched automatically. /activity shows the current state."))))))

(= (fnote:queued? $id) (not (== (collapse (match &fnote-queued (FnoteQueuedV1 $id) True)) ())))
(= (fnote:remember-queued $id)
   (if (fnote:queued? $id) True (add-atom &fnote-queued (FnoteQueuedV1 $id))))

; The intent an answer of the ledger names, or None: (INTENT QUEUED) with
; INTENT the stored JSON text.
(= (fnote:intent-of $text)
   (case (catch (json:parse $text))
     (((JsonObject $pairs)
       (let $key (sf:get (JsonObject $pairs) "key" JsonNull)
         (if (and (== (get-type $key) String) (not (== $key ""))) (FnoteIntentV1 $key (JsonObject $pairs)) None)))
      ($other None))))

; Publish the stored intent and mark it queued: 1 when the ledger recorded
; that, else 0.
(= (fnote:publish $socket $id $text)
   (case (fnote:intent-of $text)
     ((None 0)
      ((FnoteIntentV1 $key $intent)
       (if (not (== (tio:store-intent $key $intent) True)) 0
         (case (catch (ledger-client:call $socket "provider-notice-queued" ($id (system:uuid4-hex))))
           ((True (let $_ (fnote:remember-queued $id) 1))
            ($unanswered 0))))))))

; Reserve a new intent under the episode and publish whichever intent the
; ledger keeps.
(= (fnote:reserve $socket $id $episode)
   (let $text (fnote:text $episode)
     (if (== $text None) 0
       (let $key (tio:operator-key)
         (if (not (== (get-type $key) String)) 0
           (let* (($intent (JsonObject ((JsonPair "key" $key)
                                        (JsonPair "commands" (JsonArray ((JsonArray ("send" $text "plain"))))))))
                  ($encoded (json:stringify-python-with $intent ((ensure-ascii False)))))
             (case (catch (ledger-client:call $socket "provider-notice-reserve" ($id $encoded (system:uuid4-hex))))
               ((($stored 0) (fnote:publish $socket $id $stored))
                (($stored 1) (let $_ (fnote:remember-queued $id) 0))
                ($unanswered 0)))))))))

; Called once per responder cycle: 1 when this cycle published the notice of
; the open episode, else 0.
(= (fnote:notify)
   (let $episode (fnote:open-episode (health:read))
     (if (== $episode None) 0
       (let* (($id (sf:get $episode "id" JsonNull)) ($socket (fnote:socket)))
         (if (not (and (== (get-type $id) String) (not (== $socket "")))) 0
           (if (fnote:queued? $id) 0
             (case (catch (ledger-client:call $socket "provider-notice" ($id)))
               ((none (fnote:reserve $socket $id $episode))
                (($stored 0) (fnote:publish $socket $id $stored))
                (($stored 1) (let $_ (fnote:remember-queued $id) 0))
                ($unanswered 0)))))))))

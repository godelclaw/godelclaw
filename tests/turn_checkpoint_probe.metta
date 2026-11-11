!(import! &self (library lib_import))
!(import! &self ./channels/telegram.metta)
!(import! &self ./src/working_state)

; No network or model traffic: the prepared frontier and durable checkpoint
; authorize acknowledgement, while a later arrival stays pending.
!(tg:activity-add "" "42" "first input" False "full" "101" False)
!(tg:activity-batch)
!(assert (== (tg:ack-activity-batch) 0))
!(assert (not (py-call (os.path.exists (tg:ledger)))))
!(assert (== (tg:pending-count) 1))
!(working:turn-commit-stage 1 1 1)
!(tg:activity-add "" "42" "later input" False "full" "102" False)
!(assert (== (working:save 9 1 "successor feedback" 0 0 0 0) 1))
!(assert (== (py-call (input_ack.already_committed "101")) 1))
!(assert (== (py-call (input_ack.already_committed "102")) 0))
; Reconstructing the client before upstream ack must exclude settled input
; from the next cognitive presentation and retain the later arrival.
!(tg:activity-batch)
!(assert (== (tg:pending-count) 1))
!(assert (== (tg:prepared-count) 1))
!(println! TURN_CHECKPOINT_OK)

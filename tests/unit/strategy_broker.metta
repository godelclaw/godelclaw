; Unit tests of src/strategy_broker_cetta.metta; no Python. Without the
; observed strategy, a broker turn never reaches the host.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/strategy_broker_cetta.metta)
!(let* (($_ (nt:require strategy-off-by-default (nt:equal (strategy:enabled-flag) 0)))
        ($_ (nt:require broker-turn-is-the-loop-turn-when-off (nt:equal (strategy:broker-turn 7) 7)))
        ; The count limit is the answer's length: it never decides which call depends on which.
        ($_ (nt:require limit-is-every-call (nt:equal (strategy:batch-limit 5 (quote ((pin "note") (send "message")))) 2)))
        ($_ (nt:require limit-ignores-the-request (nt:equal (strategy:batch-limit 1 (quote ((help "shell") (vitals)))) 2)))
        ($_ (nt:require limit-of-no-call (nt:equal (strategy:batch-limit 3 (quote ())) 0)))
        ($_ (nt:require batch-description-unchanged
              (nt:equal (str:sha256 (strategy:batch-description 5))
                        "92c9b31e560084d366b25b50d63a512cd071a2d730f6b3d276a6bc40e9ec5053")))
        ($_ (nt:require description-unchanged
              (nt:equal (str:sha256 (strategy:description)) "af0a3f5370065312927180c994e1ca131a79987586ccbef85a93bac9e36aacd5"))))
   (println! UNIT_STRATEGY_BROKER_OK))

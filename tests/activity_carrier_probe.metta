!(import! &self ./src/loop_host_cetta.metta)

(= (assertEqual $A $B) (assert (== $A $B)))

!(assertEqual (loop-host:nonempty? "") False)
!(assertEqual (loop-host:nonempty? ()) False)
!(assertEqual (loop-host:nonempty? "activity") True)
!(println! ACTIVITY_CARRIER_OK)

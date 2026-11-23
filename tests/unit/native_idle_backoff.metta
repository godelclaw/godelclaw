; The native retry wait stores one computed deadline and one watch token, and
; arms a timer for the retry; a replaced token replaces, not accumulates.
!(import! &self (library lib_import))
!(import! &self ../../src/idle_cetta.metta)
!(idle:backoff)
!(idle:backoff)
!(assert (> (idle:retry-until) (system:wall-time)))
!(assert (== (size-atom (collapse (match &self (idle:cwp-retry $time) $time))) 1))
!(idle:set-token "old-watch")
!(idle:set-token "current-watch")
!(assert (== (idle:token) "current-watch"))
!(assert (== (size-atom (collapse (match &self (idle:cwp-token $token) $token))) 1))
!(let ((retry $id)) (idle:cwp 1)
   (let $_ (io:cancel $id) (println! IDLE_BACKOFF_OK)))

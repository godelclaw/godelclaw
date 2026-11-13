; The keys of a receipt notice: its send key and the key of its deletion, each
; derived from stable names with sha256, so a restart or a lost reply repeats
; the same key. The ledger (src/input_ledger.metta) derives the delete key; the
; control responder's notice entries (src/input_notice_cetta.metta) the send key.
!(import! &self (library str))

; int.from_bytes(sha256(<identity as UTF-8>).digest()[:8], "big"): the first 16
; hex digits of str:sha256 (64 lowercase hex digits of the text's bytes), read
; as an unsigned number up to 2^64-1. Engine integers are unbounded.
(= (input-ledger:hex-digit $text $at)
   (str:find "0123456789abcdef" (str:byte-slice $text $at (+ $at 1))))

(= (input-ledger:hex-value $text $at $count $sum)
   (if (== $count 0)
       $sum
       (input-ledger:hex-value $text (+ $at 1) (- $count 1)
                               (+ (* $sum 16) (input-ledger:hex-digit $text $at)))))

(= (input-ledger:key-number $identity)
   (input-ledger:hex-value (str:sha256 $identity) 0 16 0))

; "%020d": the decimal text, preceded by zeros up to 20 digits.
(= (input-ledger:pad20 $number)
   (let $digits (repr $number)
     (str:byte-slice (str:concat "00000000000000000000" $digits)
                     (str:byte-length $digits)
                     (+ (str:byte-length $digits) 20))))

(= (input-ledger:derived-key $lane $identity)
   (str:concat $lane (str:concat "." (input-ledger:pad20 (input-ledger:key-number $identity)))))

; lane + "." + "%020d" of the number of instance + ":input-receipt:" + task.
(= (input-ledger:send-key $instance $lane $task)
   (input-ledger:derived-key $lane
     (str:concat $instance (str:concat ":input-receipt:" $task))))

; lane + "." + "%020d" of the number of "notice-delete:" + send_key.
(= (input-ledger:delete-key $lane $send-key)
   (input-ledger:derived-key $lane (str:concat "notice-delete:" $send-key)))

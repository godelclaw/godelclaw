; The Telegram client's clocks and keys, native on CeTTa.
;
; channels/telegram.metta imports this module only on the CeTTa engine. The
; Python originals in src/helper.py (local_clock, pad20, rest_notice_text)
; stay as the SWI-PeTTa path and as the oracle tests/test_telegram_clock.py
; compares these against. Times are read in the local zone, as Python's
; time.localtime reads them; a fractional time counts its whole seconds
; below it, as time.localtime does.

!(import! &self (library str))
!(import! &self (library math))
!(import! &self (library system))
!(import! &self (library io))

(= (tg-clock:now) (system:wall-time))
(= (tg-clock:iso-stamp $epoch)
   (system:format-time "%Y-%m-%dT%H:%M:%S%z" (math:floor $epoch) "local"))
(= (tg-clock:now-iso) (tg-clock:iso-stamp (tg-clock:now)))

; int(float(value)) for a number or number text; an Error otherwise.
(= (tg-clock:whole $value)
   (if (== (get-type $value) Number) (catch (math:trunc (* 1.0 $value)))
     (if (== (get-type $value) String)
       (case (catch (str:string->number (str:trim $value)))
         (((Error $call $why) (Error $call $why))
          ($n (tg-clock:whole $n))))
       (Error (tg-clock:whole $value) "expected a number or number text"))))

; helper.local_clock: Telegram's send time, local, in the format of TIME;
; "" for what reads as no time.
(= (tg-clock:local-clock $epoch)
   (case (tg-clock:whole $epoch)
     (((Error $call $why) "")
      ($seconds
       (case (catch (system:format-time "%Y-%m-%d %H:%M:%S" $seconds "local"))
         (((Error $call $why) "")
          ($text $text)))))))

; helper.pad20: "%020d" % int(number). What Python's int rejects goes to
; helper.pad20 itself, so it raises exactly as it did.
(= (tg-clock:pad20 $number)
   (case (if (== (get-type $number) Number) (catch (math:trunc $number)) (Error $number "not a number"))
     (((Error $call $why) (py-call (helper.pad20 $number)))
      ($n (if (< $n 0) (str:concat "-" (tg-clock:zeros (str:number->string (- 0 $n)) 19))
            (tg-clock:zeros (str:number->string $n) 20))))))
(= (tg-clock:zeros $digits $width)
   (if (< (str:char-length $digits) $width) (tg-clock:zeros (str:concat "0" $digits) $width) $digits))

; helper.rest_notice_text at a given time NOW: the one notice an operator
; gets when writing to a resting agent.
(= (tg-clock:rest-notice-text $left $now)
   (let* (($whole (math:trunc (* 1.0 $left)))
          ($seconds (if (< $whole 0) 0 $whole))
          ($wake (system:format-time "%H:%M" (math:floor (+ $now $seconds)) "local"))
          ($secs (% $seconds 60))
          ($mins (/ (- $seconds $secs) 60))
          ($span (if (== $mins 0) (format-args "{}s" ($secs))
                   (format-args "{}m{}s" ($mins (tg-clock:zeros (str:number->string $secs) 2))))))
     (format-args "💤 resting — wakes in {} (at {}). Your message is queued and will be read then; send /wake to end the rest now." ($span $wake))))

; time.sleep for a nonnegative number of seconds, on an owned timer so other
; clients' completions stay theirs. Anything else goes to time.sleep, which
; rejects it as before.
(= (tg-clock:sleep $seconds)
   (if (and (== (get-type $seconds) Number) (>= $seconds 0))
     (let (io:pending $id) (io:submit (timer:after (math:ceil (* $seconds 1000))))
       (tg-clock:await $id))
     (py-call (time.sleep $seconds))))
(= (tg-clock:await $id)
   (case (io:wait $id 30000)
     (((io:event $id (timer:fired $_)) ())
      ((io:idle) (tg-clock:await $id))
      ($error $error))))

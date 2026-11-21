!(import! &self ../native_test_support.metta)
!(import! &self ../../channels/telegram_clock.metta)
; A send time that is not a number reads as no time, with one answer: the
; whole-number helper's error must not vanish on the way to local-clock.
!(let* (($_ (nt:require whole-number (== (tg-clock:whole 12.7) 12)))
        ($_ (nt:require whole-text (== (tg-clock:whole " 5 ") 5)))
        ($bad (collapse (tg-clock:whole (not-a-number x))))
        ($_ (nt:require bad-value-is-one-error
              (case $bad ((((Error $call $why)) True) ($other False)))))
        ($_ (nt:require bad-clock-reads-as-no-time (== (collapse (tg-clock:local-clock (not-a-number x))) (""))))
        ($_ (nt:require clock-formats (== (size-atom (collapse (tg-clock:local-clock 0))) 1))))
   (println! UNIT_TELEGRAM_CLOCK_OK))

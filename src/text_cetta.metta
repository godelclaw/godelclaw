; Small, effect-free text helpers shared by native agent adapters.
!(import! &self (library str))

(= (claw-text:replace $text $old $new)
   (str:join $new (str:split $old $text)))
(= (claw-text:decode-safe $value)
   (let $text (format-args "{}" ($value))
     (claw-text:replace
       (claw-text:replace (claw-text:replace $text "_newline_" "\n") "_quote_" "\"")
       "_apostrophe_" "'")))
(= (claw-text:newlines $text)
   (claw-text:replace (claw-text:replace $text "\r\n" "\n") "\r" "\n"))
(= (claw-text:nonempty-lines $lines)
   (if (== $lines ()) ()
     (let* (($head (car-atom $lines))
            ($tail (claw-text:nonempty-lines (cdr-atom $lines))))
       (if (== (str:trim $head) "") $tail (cons-atom $head $tail)))))

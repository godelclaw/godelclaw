!(import! &self (library proc))
!(import! &self (library system))
!(import! &self (library math))
!(import! &self ./src/text_cetta)
!(import! &self ./src/utils_cetta)

(= (tmux-io:run $arguments)
   (let $argv (cons-atom "tmux" $arguments)
     (case (proc:run $argv (system:cwd) inherit 15000 0)
       (((proc:exited $code $out $err)
         (TmuxResult $code
           (str:trim (claw-text:newlines (if (== $out "") $err $out)))))
        ((proc:timed-out $_ $_) (TmuxResult 1 "process timed out after 15 seconds"))
        ((proc:signaled $signal $_ $_)
         (TmuxResult 1 (str:concat "process signal " (repr $signal))))
        ((proc:failed $why) (TmuxResult 1 $why))
        ($error (TmuxResult 1 (repr $error)))))))

(= (tmux-windows)
   (case (tmux-io:run ("list-windows" "-a" "-F" "#{session_name}:#{window_index} #{window_name}"))
     (((TmuxResult 0 $out) (if (== $out "") "(no tmux windows)" $out))
      ((TmuxResult $_ $why) (str:concat "tmux-windows failed: " $why)))))

; Numeric counts truncate toward zero; invalid text retains the old default.
(= (tmux-io:count $value)
   (let $number (catch (if (== (get-type $value) Number) $value
                          (tmux-io:integer-text (format-args "{}" ($value)))))
     (case $number
       (((Error $_ $_) 25)
        ($n (max 1 (math:floor $n)))))))
(= (tmux-io:integer-text $value)
   (let* (($text (str:trim $value))
          ($digits (if (or (str:starts-with $text "-") (str:starts-with $text "+"))
                       (str:byte-slice $text 1 (str:byte-length $text)) $text)))
     (if (and (!= $digits "") (str:is-all-in digit $digits))
         (str:string->number $text) 25)))
(= (tmux-io:drop $lines $count)
   (if (or (== $lines ()) (<= $count 0)) $lines
     (tmux-io:drop (cdr-atom $lines) (- $count 1))))
(= (tmux-peek $window $count)
   (let* (($n (tmux-io:count $count))
          ($target (format-args "{}" ($window))))
     (case (tmux-io:run ("capture-pane" "-p" "-S" (repr (- 0 $n)) "-t" $target))
       (((TmuxResult 0 $out)
         (let* (($lines (claw-text:nonempty-lines (str:split "\n" $out)))
                ($tail (tmux-io:drop $lines (- (length $lines) $n))))
           (if (== $tail ()) "(pane is empty)" (str:join "\n" $tail))))
        ((TmuxResult $_ $why)
         (str:concat "tmux-peek failed: "
           (str:concat $why " — name a window from (tmux-windows), e.g. \"session:window\"")))))))

(= (tmux-send $window $value)
   (let* (($target (format-args "{}" ($window)))
          ($text (str:trim (claw-text:decode-safe $value))))
     (if (== $text "") "tmux-send: empty message"
       (case (tmux-io:run ("send-keys" "-l" "-t" $target "--" $text))
         (((TmuxResult 0 $_)
           ; Paste and Enter are separate events for composition-aware TUIs.
           (progn (sleep 2.0)
             (case (tmux-io:run ("send-keys" "-t" $target "Enter"))
               (((TmuxResult 0 $_)
                 (str:join "" ("sent to " $target " — the session will see it as user input; "
                                "(tmux-peek \"" $target "\" 30) in a later turn to read the reply")))
                ((TmuxResult $_ $why) (str:concat "tmux-send submit failed: " $why))))))
          ((TmuxResult $_ $why) (str:concat "tmux-send failed: " $why)))))))

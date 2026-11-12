; The display stream's per-attempt framing cursor and provider sequence
; numbers, one atom each per attempt id, removed when the attempt closes.
; remove-atom takes its argument unevaluated, so every removal names the atom
; through variables bound by the match, never through a call such as car-atom.
!(import! &self ./stream_sse)

(= (stream:current $id)
   (let $found (collapse (match &self (StreamCursor $id $value) $value))
     (if (== $found ()) (sse:new) (car-atom $found))))
(= (stream:forget $id)
   (collapse (match &self (StreamCursor $id $cell) (remove-atom &self (StreamCursor $id $cell)))))
(= (stream:remember $id $state)
   (let $forgotten (stream:forget $id) (add-atom &self (StreamCursor $id $state))))
(= (stream:close $id)
   (let* (($cursors (stream:forget $id))
          ($sequences (collapse (match &self (StreamSequence $id $n $text)
                                  (remove-atom &self (StreamSequence $id $n $text))))))
     True))
; 1 admits a newer sequence number, 0 an exact repeat, -1 a conflict.
(= (stream:sequence $id $n $text)
   (let $found (collapse (match &self (StreamSequence $id $old $body) ($old $body)))
     (if (== $found ())
       (let $added (add-atom &self (StreamSequence $id $n $text)) 1)
       (let ($old $body) (car-atom $found)
         (if (> $n $old)
           (let* (($removed (remove-atom &self (StreamSequence $id $old $body)))
                  ($added (add-atom &self (StreamSequence $id $n $text)))) 1)
           (if (and (== $n $old) (== $text $body)) 0 -1))))))

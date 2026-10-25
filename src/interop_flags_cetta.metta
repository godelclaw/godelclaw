; Shared typed host values, explicitly imported by their consumers.
; Atom would pass an inline host call as syntax rather than compute its flag.
(: host:flag (-> %Undefined% Bool))
(= (host:flag $value)
   (case $value ((True True) (true True) ("true" True) ($other False))))

; Python text crosses the historical bridge as a Symbol; native text is a
; grounded String. Normalize at ingress and refuse unrelated value kinds.
(: host:text (-> %Undefined% %Undefined%))
(= (host:text $value)
   (if (== (get-type $value) String) $value
     (if (== (get-metatype $value) Symbol) (format-args "{}" ($value))
       (Error host:text expected-text))))

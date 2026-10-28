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

; Python text crossed py-call as the symbol it spells before CeTTa b8d3580b,
; as SWI-PeTTa's Janus still hands it over; on CeTTa it is a String since.
; Where the runtime reads such a value as before, make the symbol again. Text
; holding NUL, and "", make no symbol and stay text.
(= (host:legacy-atom $value)
   (if (== (get-type $value) String)
     (case (str:string->symbol $value) (((Error $call $why) $value) ($symbol $symbol)))
     $value))
(= (host:legacy-atoms $value)
   (if (== (get-metatype $value) Expression)
     (if (== $value ()) ()
       (cons-atom (host:legacy-atoms (car-atom $value)) (host:legacy-atoms (cdr-atom $value))))
     (host:legacy-atom $value)))

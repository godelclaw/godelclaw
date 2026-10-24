; A shared value predicate, explicitly imported by each module that uses it.
; Atom would pass an inline host call as syntax rather than compute its flag.
(: host:flag (-> %Undefined% Bool))
(= (host:flag $value)
   (case $value ((True True) (true True) ("true" True) ($other False))))

; The byte string names changed in CeTTa. Test for the new operation
; through its result type so a selected older engine remains usable.
!(import! &self str)

(= (str-compat:byte-length $text)
   (if (== (get-type (str:byte-length $text)) Number)
       (str:byte-length $text)
       (str:length $text)))

(= (str-compat:byte-slice $text $start $end)
   (if (== (get-type (str:byte-length $text)) Number)
       (str:byte-slice $text $start $end)
       (str:slice $text $start $end)))

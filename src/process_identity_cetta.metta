; Process identity shared by provider leases and coding-session observation.
!(import! &self fs)
!(import! &self str)
!(import! &self system)
; HE's equality compares authored syntax; this typed boundary evaluates the
; operands first, as PeTTa already does. Birth/liveness checks need values.
(: process:equal (-> %Undefined% %Undefined% Bool))
(= (process:equal $left $right) (== $left $right))
(= (process:last $items)
   (if (process:equal (cdr-atom $items) ()) (car-atom $items) (process:last (cdr-atom $items))))
(: process:birth (-> %Undefined% %Undefined%))
(= (process:birth $pid)
   (case (fs:read-text-result (format-args "/proc/{}/stat" ($pid)) 0 "raw")
     ((("ok" $stat)
       (let $fields (str:split-whitespace (process:last (str:split ")" $stat)))
         (if (or (< (size-atom $fields) 20)
                 (or (process:equal (car-atom $fields) "Z") (process:equal (car-atom $fields) "X")))
           JsonNull (index-atom $fields 19))))
      ($other JsonNull))))
(= (process:owner)
   (let* (($pid (system:pid)) ($birth (process:birth $pid))
          ($stamp (if (== $birth JsonNull) "portable" $birth)))
     (format-args "{}:{}" ($pid $stamp))))
(: process:alive? (-> %Undefined% Bool))
(= (process:alive? $owner)
   (let $parts (str:split ":" $owner)
     (if (not (process:equal (size-atom $parts) 2)) False
       (let* (($pid-text (car-atom $parts)) ($birth (index-atom $parts 1)))
         (if (or (== $pid-text "") (not (str:is-all-in digit $pid-text))) False
           (let $pid (str:string->number $pid-text)
             (if (<= $pid 0) False
               (if (== $birth "portable") (system:pid-alive? $pid)
                 (process:equal (process:birth $pid) $birth)))))))))

; The worklog as a sink of public notes: whether sharing is on and its epoch,
; and a note added in an epoch. The worklog is the directory named by
; METTACLAW_PUBLIC_WORK_DIR (src/public_worklog_cetta.metta); without one,
; sharing is off. A plugin library named by METTACLAW_PUBLIC_WORK_LIB is the
; host's (src/effect_receipts_plugin.py); py-call imports it at its first use.
; Its users: the effect receipts' public events (src/effect_receipts_cetta.metta)
; and the commentary observer (src/commentary_observer_cetta.metta).
!(import! &self (library system))
!(import! &self ./public_worklog_cetta.metta)

(= (worklog:setting $name) (erv:strip (system:getenv-or-default $name "")))

; (ErSinkStatus $enabled $epoch), or the worklog's failure.
(= (worklog:status)
   (let* (($root (worklog:setting "METTACLAW_PUBLIC_WORK_DIR")) ($library (worklog:setting "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") (ErSinkStatus False JsonNull)
       (if (not (== $library ""))
         (let $value (py-call (effect_receipts_plugin.status $root $library))
           (case $value ((($enabled $epoch) (ErSinkStatus (== $enabled 1) $epoch)) ($other $other))))
         (let $state (pw:transact $root "status" "" JsonNull)
           (if (erv:failed? $state) $state
             (let $epoch (erv:int (erv:get $state "epoch" 0))
               (if (erv:failed? $epoch) $epoch
                 (ErSinkStatus (erv:truth (erv:get $state "enabled" False)) $epoch)))))))))

; 1 when the worklog kept the note, 0 when it refused it (sharing off, or
; another epoch), or the worklog's failure.
(= (worklog:add $text $epoch)
   (let* (($root (worklog:setting "METTACLAW_PUBLIC_WORK_DIR")) ($library (worklog:setting "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") 0
       (if (not (== $library "")) (py-call (effect_receipts_plugin.add $root $library $text $epoch))
         (let $state (pw:transact $root "add" $text $epoch)
           (if (erv:failed? $state) $state
             (if (erv:truth (erv:get $state "accepted" True)) 1 0)))))))

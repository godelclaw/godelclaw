; A sharing sink uses the native worklog transaction or an explicit native plugin.
!(import! &self (library system))
!(import! &self ./public_worklog_cetta.metta)
!(bind! &worklog-module (new-state ""))
(= (worklog:setting $name) (erv:strip (system:getenv-or-default $name "")))
; A configured extension supplies worklog.metta and worklog-plugin:transact.
; A missing or incompatible extension is an explicit failure, never an off state.
(= (worklog:transact $root $library $operation $text $epoch)
   (if (== $library "") (pw:transact $root $operation $text $epoch)
     (let $file (path:join $library "worklog.metta")
       (if (!= (get-state &worklog-module) "")
         (if (== (get-state &worklog-module) $file)
           (worklog:plugin $root $operation $text $epoch)
           (ErFailure NativeCapabilityError "worklog plugin changed; restart required" $file))
         (let $imported (collapse (catch (import! &self $file)))
           (if (!= $imported (True))
             (ErFailure NativeCapabilityError "configured worklog plugin needs a native worklog.metta adapter" $file)
             (let $_ (change-state! &worklog-module $file)
               (worklog:plugin $root $operation $text $epoch))))))))
(= (worklog:plugin $root $operation $text $epoch)
   (let $result (collapse (catch (worklog-plugin:transact $root $operation $text $epoch)))
     (case $result
       ((((JsonObject $pairs)) (JsonObject $pairs))
        ($bad (ErFailure NativeCapabilityError "native worklog plugin returned no transaction" $bad))))))
(= (worklog:status)
   (let* (($root (worklog:setting "METTACLAW_PUBLIC_WORK_DIR"))
          ($library (worklog:setting "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") (ErSinkStatus False JsonNull)
       (let $state (worklog:transact $root $library "status" "" JsonNull)
         (if (erv:failed? $state) $state
           (let $epoch (erv:int (erv:get $state "epoch" 0))
             (if (erv:failed? $epoch) $epoch
               (ErSinkStatus (erv:truth (erv:get $state "enabled" False)) $epoch))))))))
(= (worklog:add $text $epoch)
   (let* (($root (worklog:setting "METTACLAW_PUBLIC_WORK_DIR"))
          ($library (worklog:setting "METTACLAW_PUBLIC_WORK_LIB")))
     (if (== $root "") 0
       (let $state (worklog:transact $root $library "add" $text $epoch)
         (if (erv:failed? $state) $state
           (if (erv:truth (erv:get $state "accepted" True)) 1 0))))))

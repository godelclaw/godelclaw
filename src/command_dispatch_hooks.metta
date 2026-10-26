; Existing host services, kept separate from the shared MeTTa interpreter.
; State/history ports can replace these without a second dispatcher.
!(import! &self ./src/effect_receipts.metta)
(= (dispatch:reserve $turn $command)
   (py-call (strategy_host.reserve_command $turn $command)))
(= (dispatch:stopped) (py-call (strategy_host.stopped)))
(= (dispatch:shadow $command) (py-call (effect_backend.dispatch $command)))
(= (dispatch:argument-error $command)
   (case (api:argument-error $command)
     (((APINotRegistered $name) (py-call (skills_help.command_error $command)))
      ($result $result))))
(= (dispatch:normalize $value)
   (case $value
     (((Error (python_error $type $object) $_)
        (format-args "{}: {}" ($type (py-call (str $object)))))
      ($other (format-args "{}" ($value))))))
(= (dispatch:record $turn $status $command $result)
   (catch (effect-receipts:record-command $turn $status $command $result)))
; Fixes the public-sharing epoch before the batch runs; never raises.
(= (dispatch:capture $turn)
   (catch (effect-receipts:capture-public-epoch $turn)))

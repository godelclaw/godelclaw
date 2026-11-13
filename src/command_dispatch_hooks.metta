; Existing host services, kept separate from the shared MeTTa interpreter.
; State/history ports can replace these without a second dispatcher.
!(import! &self ./src/effect_receipts.metta)
!(import! &self ./src/skills_help.metta)
!(import! &self ./src/lifecycle_cetta.metta)
; Each command of the broker's batch is recorded as attempted before it runs
; (src/strategy_attempts_cetta.metta).
(= (dispatch:reserve $turn $command) (strategy:reserve $turn $command))
; Commands are withheld while the operator latch does not grant cognition.
; A latch that cannot be read as running withholds them too.
(= (dispatch:stopped)
   (case (catch (lifecycle-native:enabled)) ((1 0) ($other 1))))
(= (dispatch:shadow $command) (py-call (effect_backend.dispatch $command)))
(= (dispatch:argument-error $command)
   (case (api:argument-error $command)
     (((APINotRegistered $name) (help:command-error $command))
      ($result $result))))
(= (dispatch:normalize $value)
   (case $value
     (((Error (python_error $type $object) $_)
        (format-args "{}: {}" ($type (py-call (str $object)))))
      ((Error mode-state (PythonException $type $message $_))
        (format-args "{}: {}" ($type $message)))
      ($other (format-args "{}" ($value))))))
(= (dispatch:record $turn $status $command $result)
   (catch (effect-receipts:record-command $turn $status $command $result)))
; Fixes the public-sharing epoch before the batch runs; never raises.
(= (dispatch:capture $turn)
   (catch (effect-receipts:capture-public-epoch $turn)))

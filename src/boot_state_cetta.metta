; Capture the selected policy once; the active record describes its launcher.
!(import! &self ./src/loop_modes_cetta.metta)

(= (boot-state:record $mode $pid $directory)
  (let* (($path (mode-path:active-loop))
         ($temporary (str:join "" ($path ".tmp." (repr (system:pid)))))
         ($number (mv:int $pid)))
    (if (mv:failed? $number) $number
      (let $text (json:stringify-python
          (JsonObject ((JsonPair "mode" (loop-mode:canonical $mode))
                       (JsonPair "pid" $number)
                       (JsonPair "started_at" (system:wall-time))
                       (JsonPair "state_directory" $directory))))
        (mio:save $path $temporary $text True ())))))
(= (boot-state:mode) (loop-mode:current-result))
(= (boot-state:capture $pid)
  (let $mode (boot-state:mode)
    (if (mv:failed? $mode) $mode
      (if (or (== $mode "iter") (== $mode "omega")) (BootModeV1 $mode)
        (let $recorded (boot-state:record $mode $pid "")
          (if (== $recorded 1) (BootModeV1 $mode) $recorded))))))
(= (boot-state:recycle-selected)
  (if (== (mio:env "METTACLAW_ACTIVE_LOOP_MODE" "") "") False
    (let $requested (boot-state:mode)
      (if (mv:failed? $requested) $requested
        (let $active (loop-mode:process) (!= $requested $active))))))
(= (boot-state:recycle)
  (let $path (mio:env "METTACLAW_RECYCLE_REQUEST_PATH" "")
    (if (== $path "") (boot-state:recycle-selected)
      (case (catch (fs:is-file $path))
        ((True True) (False (boot-state:recycle-selected))
         ($bad (ErFailure NativeEvaluationError "recycle flag cannot be inspected" $bad)))))))
(= (boot-state:dispatch $operation)
  (case $operation
    (("capture" (boot-state:capture (mio:env "METTACLAW_BOOT_PARENT_PID" "")))
     ("current" (let $mode (boot-state:mode)
       (if (mv:failed? $mode) $mode (BootModeV1 $mode))))
     ("recycle" (let $answer (boot-state:recycle)
       (case $answer ((True (BootRecycleV1 True)) (False (BootRecycleV1 False)) ($bad $bad)))))
     ($bad (BootFailureV1 invalid-operation)))))

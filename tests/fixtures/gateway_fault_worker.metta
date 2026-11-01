; Test-only crash-cutpoint wrapper over production gateway_queue.metta.
; Production modules carry zero fault-injection hooks.
!(import! &self "../../src/execution_gateway/metta/gateway_queue.metta")
!(import! &self (library proc))

!(bind! &fault:mode (new-state NoFaultV1))

(= (fault:sigkill-self)
  (let $pid (str:number->string (system:pid))
    (proc:run ("/bin/kill" "-9" $pid) ())))

(= (fault:run $message)
  (case $message
    (((ArmFault $mode)
      (let $_ (change-state! &fault:mode $mode) (FaultArmedV1 $mode)))
     ((ApplyOpen $request $command)
      (case (get-state &fault:mode)
        ((CrashBeforePublishV1
          (case (get-state &gw:owner-state)
            (((GatewayOpenStateV1 $path $agent $owner)
              (case (store:snapshot $owner)
                (((StoreSnapshot $view $epoch $revision)
                  ; Run full validation and transition planning, then deliver
                  ; SIGKILL strictly before durable-space:commit-policy publishes.
                  (let* (($_plan (jobs:transition $view $command))
                         ($_ (store:close $view)))
                    (fault:sigkill-self)))
                 ($failure $failure))))
             ($closed (GatewayRefusedV1 owner-closed)))))
         (CrashAfterPublishBeforeReplyV1
          ; Execute the real production commit (including fsync to root.csl),
          ; then deliver SIGKILL strictly before cetta-value-worker replies.
          (let $_committed (gw:dispatch (ApplyOpen $request $command))
            (fault:sigkill-self)))
         ($normal (gw:dispatch (ApplyOpen $request $command))))))
     ($other (gw:dispatch $other)))))

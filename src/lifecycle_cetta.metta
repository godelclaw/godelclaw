; Durable operator latch only. Watcher observation remains Python-owned.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self (library json))
!(import! &self ./src/mode_paths_cetta.metta)
!(import! &self ./src/mode_io_cetta.metta)
!(import! &self ./src/mode_values_cetta.metta)

(= (lifecycle-native:read)
   (let $path (mode-path:lifecycle)
     (if (mv:failed? $path) (if (mio:os-error? $path) "stopped" $path)
       (let $value (mio:load-object $path)
         (if (mv:failed? $value) $value
           (let (JsonObject $pairs) $value
             (if (and (== (size-atom $pairs) 2)
                      (erv:equal (erv:get $value "schema" JsonNull) 1))
               (let $state (erv:get $value "state" JsonNull)
                 (if (or (== $state "running") (== $state "stopped")) $state "stopped"))
               "stopped")))))))
(= (lifecycle-native:enabled)
   (let $state (lifecycle-native:read)
     (if (mv:failed? $state) (mv:raise $state) (if (== $state "running") 1 0))))
(= (lifecycle-native:view)
   (let $state (lifecycle-native:read)
     (if (mv:failed? $state) (mv:raise $state) (str:concat $state " (operator latch)"))))
(= (lifecycle-native:body $owner $state)
   (let $mode (mio:once (collapse (catch (fs:set-mode $owner 384))))
     (if (== $mode fs:mode-set)
       (mio:write-body $owner
         (str:concat (json:stringify-python-with
           (JsonObject ((JsonPair "schema" 1) (JsonPair "state" $state))) ((sort-keys True))) "\n") True ()) $mode)))
(= (lifecycle-native:publish $path $temporary $state)
   (let $owner (mio:open $temporary ("create" "exclusive") 384)
     (if (mv:failed? $owner) $owner
       (let* (($closed (mio:close $owner (lifecycle-native:body $owner $state)))
              ($result (if (== $closed 1) (mio:rename $temporary $path) $closed)))
         (if (== $result 1) 1
           ; Only remove a temporary that THIS invocation exclusively created.
           ; Cleanup failure is retained in detail and never replays the write.
           (let $removed (mio:once (collapse (catch (fs:remove $temporary))))
             (case $result
               (((ErFailure $class $message $detail)
                 (ErFailure $class $message (LifecycleCleanup $detail $removed)))
                ($other (ErFailure NativeEvaluationError "missing lifecycle write outcome" ($result $removed)))))))))))
(= (lifecycle-native:write $state)
   (let $path (mode-path:lifecycle)
     (if (mv:failed? $path) $path
       (let $parent (mio:mkdir (mode-path:parent $path))
         (if (!= $parent ()) $parent
           (let $temporary (mio:join (mode-path:parent $path)
             (str:concat ".lifecycle-" (system:uuid4-hex)))
             (lifecycle-native:publish $path $temporary $state)))))))
(= (lifecycle-native:start)
   (let $result (lifecycle-native:write "running")
     (if (== $result 1) "started: cognition enabled by operator latch" (mv:raise $result))))
(= (lifecycle-native:stop)
   (let $result (lifecycle-native:write "stopped")
     (if (== $result 1) "stopped: cognition revoked by operator latch" (mv:raise $result))))

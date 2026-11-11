!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/mode_paths_cetta.metta)
!(import! &self ./src/mode_io_cetta.metta)
!(import! &self ./src/mode_values_cetta.metta)

; godelclaw is PeTTa-dialect MeTTa on CeTTa: one engine, nothing to select.
(= (engine-native:command $value)
   (let $name (mv:requested $value)
     (mv:raise (if (mv:failed? $name) $name (mode-path:commands $name)))))
(= (engine-native:recycle-result)
   (let $path (mio:env "METTACLAW_RECYCLE_REQUEST_PATH" "")
     (if (== $path "") False
       (let* (($temporary (str:join "" ($path ".tmp." (repr (system:pid)))))
              ($parent (mio:parent $path)))
         (if (!= $parent ()) $parent
           (let $owner (mio:open $temporary ("create" "truncate") 438)
             (if (mv:failed? $owner) $owner
               ; Read the clock only after the temporary has been opened.
               (let* (($payload (str:join "" ("requested_at="
                         (str:format-fixed (system:wall-time) 6) " reason=mode-switch\n")))
                      ($closed (mio:close $owner (mio:write-body $owner $payload True ()))))
                 (mio:engine-finish $temporary $path $closed)))))))))
(= (engine-native:recycle)
   (let $result (engine-native:recycle-result)
     (if (== $result 1) True
       (if (== $result False) False
         (if (mio:os-error? $result)
           (let (ErFailure $class $_ $_) $result
             (let $_ (system:write stdout (str:concat "[engine-mode] recycle request failed: "
               (str:concat (str:symbol->string $class) "\n"))) False))
           (mv:raise $result))))))

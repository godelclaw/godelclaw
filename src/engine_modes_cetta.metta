!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library system))
!(import! &self ./src/engine_active_cetta.metta)
!(import! &self ./src/mode_paths_cetta.metta)
!(import! &self ./src/mode_io_cetta.metta)
!(import! &self ./src/mode_values_cetta.metta)

(= (engine-native:names) ("petta" "cetta"))
(= (engine-native:description "petta") "SWI-PeTTa; stable default")
(= (engine-native:description "cetta") "CeTTa running the PeTTa language profile")
(= (engine-native:alias $name)
   (case $name (("swi" "petta") ("swi-petta" "petta") ($other $other))))
(= (engine-native:canonical $value)
   (let $name (mv:requested $value)
     (if (mv:failed? $name) $name (engine-native:alias $name))))
(= (engine-native:command $value)
   (let $name (engine-native:canonical $value)
     (mv:raise (if (mv:failed? $name) $name (mode-path:commands $name)))))
(= (engine-native:available $name)
   (if (not (mv:contains (engine-native:names) $name)) False
     (let $paths (mode-path:commands $name)
       (if (mv:failed? $paths) $paths
         (if (== $paths ()) False
           (if (fs:is-file (car-atom $paths)) (fs:access (car-atom $paths) ("execute")) False))))))
(= (engine-native:selected-result)
   (let* (($raw (mio:env "METTACLAW_ENGINE" "petta"))
          ($fallback (engine-native:environment-name $raw))
          ($path (mode-path:engine)))
     (if (mv:failed? $path) (if (mio:os-error? $path) $fallback $path)
       (let $text (mio:readline $path)
         (if (mv:failed? $text)
           (if (mio:os-error? $text) $fallback $text)
           (let $name (engine-native:alias (mode-text:name $text))
             (if (mv:contains (engine-native:names) $name) $name "petta")))))))
(= (engine-native:selected) (mv:raise (engine-native:selected-result)))
(= (engine-native:failure-lines $lines $fields)
   (if (== $lines ()) $fields
     (let* (($line (car-atom $lines)) ($parts (str:split "=" $line)))
       (engine-native:failure-lines (cdr-atom $lines)
         (if (> (size-atom $parts) 1)
           (erv:put $fields (car-atom $parts) (str:join "=" (cdr-atom $parts))) $fields)))))
(= (engine-native:failure)
   (let $path (mode-path:failure)
     (if (mv:failed? $path) (if (mio:os-error? $path) JsonNull $path)
       (let $text (mio:read $path)
         (if (mv:failed? $text)
           (if (mio:os-error? $text) JsonNull $text)
           (let $fields (engine-native:failure-lines (str:split "\n" $text) (JsonObject ()))
             (if (and (mv:contains (engine-native:names) (erv:get $fields "engine" ""))
                      (!= (erv:get $fields "reason" "") "")) $fields JsonNull)))))))
(= (engine-native:view-result)
   (let* (($active (engine-native:active)) ($selected (engine-native:selected-result)))
     (if (mv:failed? $selected) $selected
       (let $failure (engine-native:failure)
         (if (mv:failed? $failure) $failure
           (str:join "" ("active engine: " $active " — " (engine-native:description $active)
             (if (!= $selected $active) (str:concat "; requested " (str:concat $selected " for next process")) "")
             (if (== $failure JsonNull) ""
               (str:join "" ("; last " (erv:get $failure "engine" "") " failure: "
                 (erv:get $failure "reason" "") " (status " (erv:get $failure "status" "unknown") ")"))))))))))
(= (engine-native:view) (mv:raise (engine-native:view-result)))
(= (engine-native:save $name)
   (let $path (mode-path:engine)
     (if (mv:failed? $path) $path
       (let $result (mio:save $path
         (str:join "" ($path ".tmp." (repr (system:pid)))) (str:concat $name "\n") True 384)
         (if (== $result 1) True
           (if (mio:os-error? $result)
             (let (ErFailure $class $_ $_) $result
               (let $_ (system:write stdout (str:concat "[engine-mode] save failed: "
                 (str:concat (str:symbol->string $class) "\n"))) False)) $result))))))
(= (engine-native:set $value)
   (let $requested (mv:requested $value)
     (if (mv:failed? $requested) (mv:raise $requested)
       (let $name (engine-native:alias $requested)
         (if (== $name "pleatta")
           "engine-set failed: PLeaTTa is temporarily disabled; its current integration is not functional"
           (if (not (mv:contains (engine-native:names) $name)) "engine-set failed: choose one of petta, cetta"
             (let $available (engine-native:available $name)
               (if (mv:failed? $available) (mv:raise $available)
                 (if (not $available) (str:concat "engine-set failed: " (str:concat $name " is unavailable"))
                   (let $saved (engine-native:save $name)
                     (if (mv:failed? $saved) (mv:raise $saved)
                       (if (not $saved) "engine-set failed: could not persist"
                         (let $alias (if (!= $requested $name) (str:join "" (" (from alias '" $requested "')")) "")
                           (if (== $name (engine-native:active))
                             (str:join "" ("engine '" $name "' is already active" $alias))
                             (str:join "" ("engine set to '" $name "'" $alias "; recycling into it"))))))))))))))))
(= (engine-native:recycle-result)
   (let $path (mio:env "METTACLAW_RECYCLE_REQUEST_PATH" "")
     (if (== $path "") False
       (let* (($temporary (str:join "" ($path ".tmp." (repr (system:pid)))))
              ($parent (mio:parent $path)))
         (if (!= $parent ()) $parent
           (let $owner (mio:open $temporary ("create" "truncate") 438)
             (if (mv:failed? $owner) $owner
               ; Like time.time() in stream.write's argument, read the clock
               ; only AFTER the temporary has been successfully opened.
               (let* (($payload (str:join "" ("requested_at="
                         (str:format-fixed (system:wall-time) 6) " reason=engine-switch\n")))
                      ($closed (mio:close $owner (mio:write-body $owner $payload True 384))))
                 (if (== $closed 1) (mio:rename $temporary $path) $closed)))))))))
(= (engine-native:recycle)
   (let $result (engine-native:recycle-result)
     (if (== $result 1) True
       (if (== $result False) False
         (if (mio:os-error? $result)
           (let (ErFailure $class $_ $_) $result
             (let $_ (system:write stdout (str:concat "[engine-mode] recycle request failed: "
               (str:concat (str:symbol->string $class) "\n"))) False))
           (mv:raise $result))))))

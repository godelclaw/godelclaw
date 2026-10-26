; Python-compatible worklog values and effects; no Python persistence fallback.
; Root/lock is the SAME flock file used by public_worklog.py and stays owned
; across the entire read/check/note/temp/replace transaction.
!(import! &self (library fs))
!(import! &self (library str))
!(import! &self (library json))
!(import! &self (library system))
!(import! &self (library list))
!(import! &self ./src/effect_receipt_values_cetta.metta)
!(import! &self ./src/effect_receipt_io_cetta.metta)

(= (pw:defaults) (JsonObject ((JsonPair "enabled" False) (JsonPair "seq" 0) (JsonPair "epoch" 0))))
(= (pw:clean $value)
   (let $text (erv:str $value) (if (erv:failed? $text) $text (erv:clean $text))))
(= (pw:parsed $value)
   (case $value
     (((JsonPythonErrorV1 JSONDecodeError $message $msg $pos $line $column)
       (ErFailure JSONDecodeError $message (JsonLocation $msg $pos $line $column)))
      ((JsonPythonErrorV1 ValueError $message) (erv:fail ValueError $message))
      ((JsonFailureV1 $phase $kind $source $message)
       (ErFailure NativeCapabilityError "unexpected JSON ingestion failure" $value))
      ($other $other))))
(= (pw:decode $raw)
   (case (str:utf8-error $raw)
     ((StrUtf8WellFormedV1 (erio:universal $raw))
      ((StrUtf8ErrorV1 $start $end $byte $reason)
       (ErFailure UnicodeDecodeError
         (str:join "" ("'utf-8' codec can't decode "
           (if (== $end (+ $start 1))
             (str:join "" ("byte 0x" (erv:hex $byte 2) " in position " (repr $start)))
             (str:join "" ("bytes in position " (repr $start) "-" (repr (- $end 1)))))
           ": " (str:join " " (str:split "-" (str:symbol->string $reason)))))
         (DecodeRange $start $end)))
      ($other (ErFailure NativeCapabilityError "missing strict UTF-8 observation" $other)))))
(= (pw:read-text $path)
   ; This read API exposes tags, not errno/strerror. Keep the native diagnostic
   ; boundary explicit; do not fabricate Python's filename-bearing OS message.
   (case (fs:read-text-result $path 0 "raw")
     ((("ok" $raw) (pw:decode $raw))
      (("missing" $cwd) (ErFailure FileNotFoundError "native read-text-result: missing" ($path $cwd)))
      (("directory" $detail) (ErFailure IsADirectoryError "native read-text-result: directory" ($path $detail)))
      (("denied" $detail) (ErFailure PermissionError "native read-text-result: denied" ($path $detail)))
      (("error" $class $detail) (ErFailure OSError $detail ($class $path)))
      ($other (ErFailure NativeCapabilityError "missing raw file read outcome" $other)))))
(= (pw:read-default $value)
   (case $value
     (((ErFailure FileNotFoundError $_ $_) (pw:defaults))
      ((ErFailure IsADirectoryError $_ $_) (pw:defaults))
      ((ErFailure PermissionError $_ $_) (pw:defaults))
      ((ErFailure OSError $_ $_) (pw:defaults))
      ((ErFailure UnicodeDecodeError $_ $_) (pw:defaults))
      ($other $other))))
(= (pw:parse-default $value)
   (case $value
     (((ErFailure JSONDecodeError $_ $_) (pw:defaults))
      ((ErFailure ValueError $_ $_) (pw:defaults))
      ($other $other))))
(= (pw:state $root)
   (let $raw (pw:read-default (pw:read-text (erio:join $root "state.json")))
     (let $value (if (== (get-type $raw) String)
       (pw:parse-default (pw:parsed (json:parse-python $raw))) $raw)
         (case $value
           (((JsonObject $_)
             (erv:setdefault (erv:setdefault (erv:setdefault $value "enabled" False) "seq" 0) "epoch" 0))
            ((ErFailure $_ $_ $_) $value)
            ($other (erv:fail AttributeError (str:join "" ("'" (erv:typename $other) "' object has no attribute 'setdefault'")))))))))
(= (pw:increment $value)
   (case $value
     ((True 2) (False 1)
      ($other (if (== (get-type $other) Number) (+ $other 1)
        (if (== (get-type $other) String) (erv:fail TypeError "can only concatenate str (not \"int\") to str")
          (case $other
            (((JsonArray $_) (erv:fail TypeError "'int' object is not iterable"))
             ($v (erv:fail TypeError (str:join "" ("unsupported operand type(s) for +=: '" (erv:typename $v) "' and 'int'"))))))))))))
(= (pw:json-result $wire)
   (case $wire
     (((JsonPythonErrorV1 $class $message $prefix) (ErFailure $class $message (DumpPrefix $prefix)))
      ((JsonPythonErrorV1 $class $message) (erv:fail $class $message))
      ($other (if (== (get-type $other) String) $other
        (ErFailure NativeCapabilityError "missing Python JSON emission outcome" $other))))))
; Already opened in the oracle BEFORE dumps; an emission/encoding error may
; leave an empty notes file. No streaming prefix is published on this path.
(= (pw:note-write $owner $entry)
   (let $wire (pw:json-result (json:stringify-python-with $entry ((ensure-ascii False))))
     (if (erv:failed? $wire) $wire
       (let $encoded (erv:encode (str:concat $wire "\n"))
         (if (erv:failed? $encoded) $encoded (erio:write-sync $owner $encoded))))))
(= (pw:append-note $path $entry)
   (let $owner (erio:open $path ("append" "create") 384)
     (if (erv:failed? $owner) $owner
       (let $outcome (erio:single (collapse (catch (pw:note-write $owner $entry))))
         (erio:finish $owner False $outcome)))))
(= (pw:dump-write $owner $state)
   ; CPython resource-limit failures depend on the surrounding stack; this
   ; explicit-stack native emission has no corresponding Python recursion limit.
   (let $wire (pw:json-result (json:stringify-python $state))
     (case $wire
       (((ErFailure $class $message (DumpPrefix $prefix))
         (let $written (erio:write $owner $prefix)
           (if (erv:failed? $written) $written $wire)))
        ((ErFailure $_ $_ $_) $wire)
        ($payload (erio:write-sync $owner $payload))))))
(= (pw:save $root $state $result)
   (let* (($temporary (erio:join $root (str:concat "state.tmp." (repr (system:pid)))))
          ($owner (erio:open $temporary ("truncate" "create") 384)))
     (if (erv:failed? $owner) $owner
       (let* (($outcome (erio:single (collapse (catch (pw:dump-write $owner $state)))))
              ($closed (erio:finish $owner False $outcome)))
         (if (erv:failed? $closed) $closed
           (let $renamed (erio:single (collapse (catch (fs:rename $temporary (erio:join $root "state.json")))))
             (case $renamed
               ((() $result) ((ErFailure $_ $_ $_) $renamed)
                ($other (ErFailure NativeCapabilityError "missing state rename outcome" $other))))))))))
(= (pw:reject $reason)
   (JsonObject ((JsonPair "accepted" False) (JsonPair "reason" $reason))))
(= (pw:add $root $state $text $expected)
   (if (and (not (== $expected JsonNull)) (not (erv:equal (erv:get $state "epoch" 0) $expected)))
     (pw:reject "sharing epoch changed")
     (if (not (erv:truth (erv:get $state "enabled" False)))
       (pw:reject "display off; note not retained")
       (let $seq (pw:increment (erv:get $state "seq" 0))
         (if (erv:failed? $seq) $seq
           (let* (($next (erv:put $state "seq" $seq))
                  ($at (erio:iso (system:wall-time) "local"))
                  ($clean (pw:clean $text)))
             (if (erv:failed? $at) $at
               (if (erv:failed? $clean) $clean
                 (let* (($entry (JsonObject ((JsonPair "seq" $seq) (JsonPair "time" $at) (JsonPair "text" $clean))))
                        ($written (pw:append-note (erio:join $root "notes.jsonl") $entry)))
                   (if (erv:failed? $written) $written
                     (pw:save $root $next (erv:put $next "accepted" True))))))))))))
(= (pw:parse-lines $lines)
   (if (== $lines ()) (JsonArray ())
     (let $first (pw:parsed (json:parse-python (car-atom $lines)))
       (if (erv:failed? $first) $first
         (let $rest (pw:parse-lines (cdr-atom $lines))
           (case $rest
             (((JsonArray $values) (JsonArray (cons-atom $first $values)))
              ($failure $failure))))))))
(= (pw:read $root $state)
   (let $log (erio:join $root "notes.jsonl")
     (let $entries (if (erv:truth (erv:get $state "enabled" False))
       (let $exists (erio:exists $log)
         (if (erv:failed? $exists) $exists
           (if $exists (let $raw (pw:read-text $log)
             (if (erv:failed? $raw) $raw (pw:parse-lines (erio:lines $raw)))) (JsonArray ())))) (JsonArray ()))
       (if (erv:failed? $entries) $entries (PublicWorkRead $state $entries)))))
(= (pw:body $root $action $text $expected)
   (let $state (pw:state $root)
     (if (erv:failed? $state) $state
       (case $action
         (("add" (pw:add $root $state $text $expected))
          ("read" (pw:read $root $state))
          ("status" (pw:save $root $state $state))
          ($other (if (or (== $other "on") (== $other "off"))
            (let $epoch (pw:increment (erv:get $state "epoch" 0))
              (if (erv:failed? $epoch) $epoch
                (let $next (erv:put (erv:put $state "enabled" (== $other "on")) "epoch" $epoch)
                  (pw:save $root $next $next))))
            (erv:fail ValueError "unknown public-work action"))))))))
(= (pw:transact $root $action $text $expected)
   (if (== (get-type $root) String) (pw:transact-path $root $action $text $expected)
     (erv:fail TypeError (str:join "" ("argument should be a str or an os.PathLike object where __fspath__ returns a str, not '"
       (erv:typename $root) "'")))))
(= (pw:transact-path $root $action $text $expected)
   (let* (($path (erio:path $root)) ($made (erio:mkdir $path 448)))
     (if (erv:failed? $made) $made
       (let $owner (erio:open (erio:join $path "lock") ("append" "create") 438)
         (if (erv:failed? $owner) $owner
           (let* (($locked (erio:lock $owner))
                  ($outcome (if (erv:failed? $locked) $locked
                    (erio:single (collapse (catch (pw:body $path $action $text $expected)))))))
             (erio:finish $owner (not (erv:failed? $locked)) $outcome)))))))

; Full tool output is retained independently of its bounded presentation.
!(import! &self ./state_file_cetta.metta)
!(import! &self ./diagnostic_cetta.metta)
!(import! &self (library list))
(= (artifact:directory)
   (let $configured (system:getenv-or-default "METTACLAW_TOOL_OUTPUT_DIR" "")
     (if (!= $configured "") $configured
       (let $engine (system:getenv-or-default "METTACLAW_ENGINE_STATE_PATH" "")
         (if (!= $engine "") (path:join (path:dirname $engine) "tool-results")
           (path:join (system:getenv-or-default "XDG_STATE_HOME" (system:expanduser "~/.local/state"))
                      (str:join "/" ("godelclaw" (system:getenv-or-default "METTACLAW_INSTANCE" "agent") "tool-results"))))))))
(= (artifact:retain-text $text)
   (let* (($root (artifact:directory)) ($_ (fs:make-directories $root 504))
          ($digest (str:sha256 $text)) ($path (path:join $root (str:concat $digest ".txt")))
          ($written (sf:replace $path $text 432 True)))
     (if (!= $written True) (Error tool-output (Fault retain-failed))
       (JsonObject ((JsonPair "path" $path) (JsonPair "bytes" (str:byte-length $text)) (JsonPair "sha256" $digest))))))
(= (artifact:text-view $text $chars)
   (if (or (<= $chars 0) (<= (str:char-length $text) $chars)) $text
     (let $artifact (artifact:retain-text $text)
       (if (artifact:failed? $artifact) $artifact
         (str:join "" ((str:char-slice $text 0 $chars) "\n[preview; full output: "
           (sf:get $artifact "path" "") "; " (repr (sf:get $artifact "bytes" 0))
           " bytes; sha256 " (sf:get $artifact "sha256" "") "]"))))))

(= (artifact:failed? $value)
   (case $value (((Error $_ $_) True) ($other False))))
(= (artifact:retain-file $path)
   (let $flushed (artifact:sync-file $path)
     (if (!= $flushed True) $flushed (artifact:retain-synced-file $path))))
(= (artifact:sync-file $path)
   (let $owner (fs:open-read $path)
     (case $owner
       (((NativeHandle $_ $_)
         (let* (($synced (fs:sync $owner)) ($_ (fs:close $owner)))
           (if (sf:failed? $synced) (Error tool-output (Incomplete file-sync-failed)) True)))
        ($bad (Error tool-output (Fault file-open-failed)))))))
(= (artifact:retain-synced-file $path)
   (case (fs:sha256-file $path)
     (((fs:hashed-v1 $digest (fs:info-v2 $_ $bytes $_ $_ $_ $_ $_))
       (let* (($destination (path:join (artifact:directory) (str:concat $digest ".txt")))
              ($published (fs:rename $path $destination)))
         (if (sf:failed? $published) (Error tool-output (Fault publish-failed))
           (let $synced (sf:sync-directory (artifact:directory))
             (if (!= $synced True) (Error tool-output (Incomplete (directory-sync $destination)))
               (JsonObject ((JsonPair "path" $destination) (JsonPair "bytes" $bytes) (JsonPair "sha256" $digest))))))))
      ($bad (Error tool-output (Fault hash-failed))))))
(= (artifact:head $path $characters)
   (let $owner (fs:open-read $path)
     (case $owner
       (((NativeHandle $_ $_)
         (let* (($chunk (fs:read-chunk $owner (* 4 $characters)))
                ($_ (fs:close $owner))
                ($decoder (str:utf8-new replace)))
           (case $chunk
             (((fs:chunk $bytes $_)
               (case (str:utf8-feed $decoder $bytes False)
                 (((Utf8DecodedV1 $text $_) (str:char-slice $text 0 (min $characters (str:char-length $text))))
                  ($bad (Error tool-output (Fault decode-failed))))))
              ((fs:end $_) "")
              ($bad (Error tool-output (Fault read-failed)))))))
        ($bad (Error tool-output (Fault open-failed)))))))
(= (artifact:file-view $record $characters)
   (if (artifact:failed? $record) $record
     (let $text (artifact:head (sf:get $record "path" "") $characters)
       (if (artifact:failed? $text) $text
         (if (<= (sf:get $record "bytes" 0) (str:byte-length $text)) $text
           (str:join "" ($text "\n[preview; full output: " (sf:get $record "path" "") "; "
             (repr (sf:get $record "bytes" 0)) " bytes; sha256 " (sf:get $record "sha256" "") "]")))))))

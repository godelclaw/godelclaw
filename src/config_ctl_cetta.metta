; Configuration inspector and editor for config/local.toml and .env
; (scripts/config_ctl): exclusive file locking, AST-verified surgical TOML
; updates, secret-key protection, conflict warnings, and durable atomic
; publication via src/configuration_cetta.metta.
!(import! &self ./configuration_cetta.metta)
!(import! &self ./state_file_cetta.metta)
!(import! &self ./cli_values_cetta.metta)
!(import! &self (library list))

!(bind! &cctl-re-setting (new-state (re:compile "^[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+$")))
!(bind! &cctl-re-section-hdr (new-state (re:compile "^\\s*\\[")))
!(bind! &cctl-re-int (new-state (re:compile "^-?[0-9]+$")))

(= (cctl:contains? $hay $needle)
   (if (!= (get-type $hay) String) False
     (case (str:find $hay $needle)
       (((TextNotFound $_) False)
        ($at (== (get-type $at) Number))))))

(= (cctl:secret-key? $key)
   (let $low (str:lower $key)
     (or (cctl:contains? $low "secret")
         (or (cctl:contains? $low "password")
             (cctl:contains? $low "api_key")))))

(= (cctl:secret-env? $env)
   (let $low (str:lower $env)
     (or (cctl:contains? $low "key")
         (or (cctl:contains? $low "token")
             (or (cctl:contains? $low "secret")
                 (cctl:contains? $low "password"))))))

(= (cctl:toml->json $value)
   (case $value
     (((TomlTableV1 $fields)
       (JsonObject (map-atom $fields $f (let (TomlFieldV1 $k $v) $f (JsonPair $k (cctl:toml->json $v))))))
      ((TomlArrayV1 $items)
       (JsonArray (map-atom $items $it (cctl:toml->json $it))))
      ((TomlTemporalV1 $_kind $text) $text)
      ((JsonObject $pairs)
       (JsonObject (map-atom $pairs $p (let (JsonPair $k $v) $p (JsonPair $k (cctl:toml->json $v))))))
      ((JsonArray $items)
       (JsonArray (map-atom $items $it (cctl:toml->json $it))))
      ($scalar $scalar))))

(= (cctl:canonical-str $value)
   (json:stringify-python-with (cctl:toml->json $value)
     ((ensure-ascii False) (sort-keys True) (separators "," ":"))))

(= (cctl:json-keys $obj)
   (case $obj
     (((JsonObject $pairs) (map-atom $pairs $p (let (JsonPair $k $_v) $p $k)))
      ($other ()))))

(= (cctl:unique-sorted $items)
   (cctl:dedup (list:sort $items) CctlNone))

(= (cctl:dedup $items $prev)
   (if (== $items ()) ()
     (let ($h $t) ((car-atom $items) (cdr-atom $items))
       (if (== $h $prev) (cctl:dedup $t $prev)
         (cons-atom $h (cctl:dedup $t $h))))))

(= (cctl:obj-has? $obj $key)
   (!= (sf:get $obj $key CctlMissing) CctlMissing))

(= (cctl:show-keys $sec $base $given $keys)
   (if (== $keys ()) ()
     (let* (($k (car-atom $keys))
            ($rest (cctl:show-keys $sec $base $given (cdr-atom $keys))))
       (if (cctl:secret-key? $k) $rest
         (let* (($in-given (cctl:obj-has? $given $k))
                ($in-base (cctl:obj-has? $base $k))
                ($origin (if $in-given (if $in-base "local.toml" "local.toml (no default)") "default.toml"))
                ($val (if $in-given (sf:get $given $k JsonNull) (sf:get $base $k JsonNull)))
                ($row (JsonObject ((JsonPair "setting" (str:join "." ($sec $k)))
                                   (JsonPair "value" $val)
                                   (JsonPair "origin" $origin)))))
           (cons-atom $row $rest))))))

(= (cctl:show-sections $defaults $current $secs)
   (if (== $secs ()) ()
     (let* (($sec (car-atom $secs))
            ($base (sf:get $defaults $sec (JsonObject ())))
            ($given (sf:get $current $sec (JsonObject ())))
            ($rest (cctl:show-sections $defaults $current (cdr-atom $secs))))
       (if (and (cfg:table? $base) (cfg:table? $given))
         (let* (($keys (cctl:unique-sorted (list:append (cctl:json-keys $base) (cctl:json-keys $given))))
                ($rows (cctl:show-keys $sec $base $given $keys)))
           (list:append $rows $rest))
         $rest))))

(= (cctl:show $defaults $current)
   (let* (($dj (cctl:toml->json $defaults))
          ($cj (cctl:toml->json $current))
          ($secs (cctl:unique-sorted (list:append (cctl:json-keys $dj) (cctl:json-keys $cj)))))
     (cctl:show-sections $dj $cj $secs)))

(= (cctl:split-lines $text)
   (if (== $text "") ()
     (let* (($trimmed-nl (if (str:ends-with $text "\n")
                           (str:byte-slice $text 0 (- (str:byte-length $text) 1)) $text)))
       (str:split "\n" $trimmed-nl))))

(= (cctl:match-section-line? $line $section)
   (let $t (str:trim $line)
     (let $hdr (str:join "" ("[" $section "]"))
       (if (== $t $hdr) True
         (if (str:starts-with $t $hdr)
           (let $after (str:trim (str:byte-slice $t (str:byte-length $hdr) (str:byte-length $t)))
             (or (== $after "") (str:starts-with $after "#")))
           False)))))

(= (cctl:match-key-line? $line $key)
   (let $t (str:trim $line)
     (if (not (str:starts-with $t $key)) False
       (let $after (str:trim (str:byte-slice $t (str:byte-length $key) (str:byte-length $t)))
         (str:starts-with $after "=")))))

(= (cctl:find-section $lines $section $idx)
   (if (== $lines ()) -1
     (if (cctl:match-section-line? (car-atom $lines) $section) $idx
       (cctl:find-section (cdr-atom $lines) $section (+ $idx 1)))))

(= (cctl:find-section-end $lines $idx)
   (if (== $lines ()) $idx
     (if (re:is-match (get-state &cctl-re-section-hdr) (car-atom $lines)) $idx
       (cctl:find-section-end (cdr-atom $lines) (+ $idx 1)))))

(= (cctl:find-key-in $lines $key $idx $end)
   (if (or (== $lines ()) (>= $idx $end)) -1
     (if (cctl:match-key-line? (car-atom $lines) $key) $idx
       (cctl:find-key-in (cdr-atom $lines) $key (+ $idx 1) $end))))

(= (cctl:replace-at $lines $idx $target $new-line)
   (if (== $lines ()) ()
     (let ($h $t) ((car-atom $lines) (cdr-atom $lines))
       (if (== $idx $target) (cons-atom $new-line $t)
         (cons-atom $h (cctl:replace-at $t (+ $idx 1) $target $new-line))))))

(= (cctl:insert-after $lines $idx $target $new-line)
   (if (== $lines ()) ($new-line)
     (let ($h $t) ((car-atom $lines) (cdr-atom $lines))
       (if (== $idx $target) (cons-atom $h (cons-atom $new-line $t))
         (cons-atom $h (cctl:insert-after $t (+ $idx 1) $target $new-line))))))

(= (cctl:drop-n $lines $n)
   (if (or (<= $n 0) (== $lines ())) $lines
     (cctl:drop-n (cdr-atom $lines) (- $n 1))))

(= (cctl:trim-right-nl $text)
   (let $len (str:byte-length $text)
     (if (== $len 0) ""
       (let $ch (str:byte-slice $text (- $len 1) $len)
         (if (or (== $ch "\n") (or (== $ch "\r") (or (== $ch " ") (== $ch "\t"))))
           (cctl:trim-right-nl (str:byte-slice $text 0 (- $len 1)))
           $text)))))

(= (cctl:json-put-nested $obj $section $key $val)
   (let* (($sec-obj (sf:get $obj $section (JsonObject ())))
          ($sec2 (sf:put $sec-obj $key $val)))
     (sf:put $obj $section $sec2)))

; Surgical TOML update, accepted only if the re-parsed TOML AST equals the
; expected table: the old table with exactly this one setting changed.
(= (cctl:replace-setting $text $section $key $value)
   (case (cfg:parse $text)
     (((TomlTableV1 $fields)
       (let* (($before-json (cctl:toml->json (TomlTableV1 $fields)))
              ($expected (cctl:json-put-nested $before-json $section $key $value))
              ($encoded (json:stringify-python-with $value ((ensure-ascii False))))
              ($new-line (str:join "" ($key " = " $encoded)))
              ($lines (cctl:split-lines $text))
              ($start (cctl:find-section $lines $section 0))
              ($candidate
                (if (< $start 0)
                  (str:join "" ((cctl:trim-right-nl $text) "\n\n[" $section "]\n" $new-line "\n"))
                  (let* (($after (cctl:drop-n $lines (+ $start 1)))
                         ($end (cctl:find-section-end $after (+ $start 1)))
                         ($hit (cctl:find-key-in $after $key (+ $start 1) $end))
                         ($updated (if (>= $hit 0)
                                     (cctl:replace-at $lines 0 $hit $new-line)
                                     (cctl:insert-after $lines 0 $start $new-line))))
                    (str:concat (str:join "\n" $updated) "\n")))))
         (case (cfg:parse $candidate)
           (((TomlTableV1 $after-fields)
             (if (== (cctl:canonical-str (TomlTableV1 $after-fields)) (cctl:canonical-str $expected))
               (CctlOk $candidate)
               (CctlError "complex TOML setting requires a direct TOML edit")))
            ($bad-after (CctlError "complex TOML setting requires a direct TOML edit"))))))
      ($bad-before (CctlError "invalid TOML configuration")))))

(= (cctl:validate-and-render $root $defaults $toml-text)
   (case (cfg:parse $toml-text)
     (((TomlTableV1 $fields)
       (let $local (TomlTableV1 $fields)
         (case (cfg:validate $defaults $local)
           ((ConfigValidV1
             (case (cfg:render-fields $defaults $local $root (cfg:fields))
               (((ConfigTextV1 $body)
                 (CctlOk (str:concat "# Generated by native CeTTa configuration.\n" $body)))
                ($bad-render (CctlError "configuration validation failed")))))
            ($bad-val (CctlError "configuration validation failed"))))))
      ($bad-parse (CctlError "configuration validation failed")))))

(= (cctl:py-repr $s)
   (str:join "" ("'" (cfg:replace (cfg:replace $s "\\" "\\\\") "'" "\\'") "'")))

(= (cctl:warn-conflicts $defaults $local $root $fields)
   (if (== $fields ()) True
     (let (ConfigFieldV1 $env $section $key $fallback $mode) (car-atom $fields)
       (let* (($raw (cfg:value $defaults $local $section $key $fallback))
              ($val (cfg:convert $raw $root $mode))
              ($inherited (system:getenv-or-default $env ConfigEnvUnsetV1))
              ($done
                (if (or (== $val JsonNull) (or (== $val "") (== $inherited ConfigEnvUnsetV1))) True
                  (let $rendered (cfg:env-text $val)
                    (if (or (!= (get-type $rendered) String) (== $inherited $rendered)) True
                      (let* (($sec (cctl:secret-env? $env))
                             ($s-inh (if $sec "(hidden)" (cctl:py-repr $inherited)))
                             ($s-ren (if $sec "(hidden)" (cctl:py-repr $rendered)))
                             ($msg (str:join "" ("config: " $env "=" $s-inh
                                     " from the caller's environment is replaced by " $s-ren
                                     " from config/local.toml or config/default.toml; set it there instead\n"))))
                        (system:write stderr $msg)))))))
         (cctl:warn-conflicts $defaults $local $root (cdr-atom $fields))))))

(= (cctl:integer? $v)
   (and (cfg:number? $v)
        (re:is-match (get-state &cctl-re-int) (json:stringify-python $v))))

(= (cctl:parse-value $default $raw)
   (if (== (get-type $default) String)
     (CctlOk $raw)
     (let $parsed (json:parse-python $raw)
       (case $parsed
         (((JsonPythonErrorV1 $_1 $_2 $_3) (CctlError "invalid JSON value"))
          ($val
           (if (== (get-type $default) Bool)
             (if (== (get-type $val) Bool) (CctlOk $val) (CctlError "wrong type"))
             (if (== (get-type $default) Number)
               (if (cfg:number? $val)
                 (if (or (cctl:integer? $default) (not (cctl:integer? $val)))
                   (CctlOk $val) (CctlError "wrong type"))
                 (CctlError "wrong type"))
               (case ($default $val)
                 ((((TomlArrayV1 $_a) (JsonArray $_b)) (CctlOk $val))
                  (((JsonArray $_a) (JsonArray $_b)) (CctlOk $val))
                  (((TomlTableV1 $_a) (JsonObject $_b)) (CctlOk $val))
                  (((JsonObject $_a) (JsonObject $_b)) (CctlOk $val))
                  ($mismatch (CctlError "wrong type"))))))))))))

(= (cctl:run-locked $root $op $setting $raw-val $dry-run)
   (let* (($local-path (path:join $root "config/local.toml"))
          ($default-path (path:join $root "config/default.toml"))
          ($text (catch (fs:read-text $local-path)))
          ($defaults (cfg:read $default-path)))
     (if (or (!= (get-type $text) String) (not (cfg:table? $defaults)))
       (CctlError "cannot read configuration")
       (case (cfg:parse $text)
         (((TomlTableV1 $cfields)
           (let $current (TomlTableV1 $cfields)
             (case $op
               (("show"
                 (let* (($rows (cctl:show $defaults $current))
                        ($w (map-atom $rows $row
                              (system:write stdout (str:concat (json:stringify-python-with $row ((ensure-ascii False))) "\n")))))
                   (CctlDone 0)))
                ("check"
                 (case (cctl:validate-and-render $root $defaults $text)
                   (((CctlOk $_env)
                     (let $w (system:write stdout "{\"valid\": true}\n") (CctlDone 0)))
                    ($err $err))))
                ("render"
                 (case (cctl:validate-and-render $root $defaults $text)
                   (((CctlOk $env-text)
                     (let* (($warn (cctl:warn-conflicts $defaults $current $root (cfg:fields)))
                            ($saved (if $dry-run True (sf:replace (path:join $root ".env") $env-text 384 True))))
                       (if (== $saved True)
                         (let $w (system:write stdout "{\"valid\": true}\n") (CctlDone 0))
                         (CctlError "cannot write .env"))))
                    ($err $err))))
                ($get-or-set
                 (if (or (== $setting "") (not (re:is-match (get-state &cctl-re-setting) $setting)))
                   (CctlError "setting must be a section.key")
                   (let* (($parts (str:split "." $setting))
                          ($section (index-atom $parts 0))
                          ($key (index-atom $parts 1))
                          ($def-sec (cfg:get $defaults $section CctlMissing))
                          ($def-val (if (cfg:table? $def-sec) (cfg:get $def-sec $key CctlMissing) CctlMissing)))
                     (if (or (== $def-val CctlMissing) (cctl:secret-key? $key))
                       (CctlError "setting is not an editable nonsecret configuration key")
                       (if (== $op "get")
                         (let* (($cur-sec (cfg:get $current $section (JsonObject ())))
                                ($old (if (cfg:table? $cur-sec) (cfg:get $cur-sec $key $def-val) $def-val))
                                ($out (JsonObject ((JsonPair "setting" $setting) (JsonPair "value" (cctl:toml->json $old)))))
                                ($w (system:write stdout (str:concat (json:stringify-python-with $out ((ensure-ascii False))) "\n"))))
                           (CctlDone 0))
                         (if (== $raw-val CctlNoVal)
                           (CctlError "set requires a value")
                           (case (cctl:parse-value $def-val $raw-val)
                             (((CctlOk $val)
                               (case (cctl:replace-setting $text $section $key $val)
                                 (((CctlOk $candidate)
                                   (case (cctl:validate-and-render $root $defaults $candidate)
                                     (((CctlOk $env-text)
                                       (if (and (not $dry-run) (fs:exists (path:join $root ".runtime-update-hold")))
                                         (CctlError "paired runtime update currently holds this configuration")
                                         (let* (($s1 (if $dry-run True (sf:replace $local-path $candidate 384 True)))
                                                ($s2 (if (or $dry-run (!= $s1 True)) $s1
                                                       (sf:replace (path:join $root ".env") $env-text 384 True))))
                                           (if (== $s2 True)
                                             (let* (($applied (not $dry-run))
                                                    ($out (JsonObject ((JsonPair "setting" $setting)
                                                                       (JsonPair "value" $val)
                                                                       (JsonPair "applied" $applied)
                                                                       (JsonPair "restart_required" $applied))))
                                                    ($w (system:write stdout (str:concat (json:stringify-python-with $out ((ensure-ascii False))) "\n"))))
                                               (CctlDone 0))
                                             (CctlError "cannot write updated configuration")))))
                                      ($err-r $err-r))))
                                  ($err-rep $err-rep))))
                              ($err-val $err-val)))))))))))))
          ($bad-cur (CctlError "invalid TOML configuration")))))))

(= (cctl:parse-args $args $pos $opts)
   (if (== $args ()) (CctlParsed (list:reverse $pos) $opts)
     (let ($head $rest) ((car-atom $args) (cdr-atom $args))
       (if (== $head "--dry-run")
         (cctl:parse-args $rest $pos (sf:put $opts "--dry-run" True))
         (if (== $head "--root")
           (if (== $rest ()) (CctlError "--root requires a path")
             (cctl:parse-args (cdr-atom $rest) $pos (sf:put $opts "--root" (car-atom $rest))))
           (if (and (== $pos ()) (str:starts-with $head "-"))
             (CctlError (str:concat "unknown option: " $head))
             (cctl:parse-args $rest (cons-atom $head $pos) $opts)))))))

(= (cctl:reject)
   (progn (system:write stderr "{\"error\": \"configuration update rejected; check the key, type and TOML syntax\"}\n") 2))

(= (cctl:main $args)
   (case (cctl:parse-args (cli:strings $args) () (JsonObject ()))
     (((CctlParsed $pos $opts)
       (if (== $pos ()) (cctl:reject)
         (let* (($op (car-atom $pos))
                ($tail (cdr-atom $pos))
                ($setting (if (>= (size-atom $tail) 1) (index-atom $tail 0) ""))
                ($raw-val (if (>= (size-atom $tail) 2) (index-atom $tail 1) CctlNoVal))
                ($root-opt (sf:get $opts "--root" ""))
                ($root (cfg:absolute (if (== $root-opt "") (system:getcwd) $root-opt)))
                ($dry-run (sf:get $opts "--dry-run" False)))
           (if (not (cli:member? $op ("get" "set" "check" "render" "show")))
             (cctl:reject)
             (let* (($lock-path (path:join $root "config/.edit.lock"))
                    ($owner (fs:open-write $lock-path ("create" "append") 384)))
               (if (sf:failed? $owner) (cctl:reject)
                 (let* (($m (fs:set-mode $owner 384))
                        ($held (fs:lock $owner exclusive)))
                   (case $held
                     ((fs:locked
                       (let* (($res (cctl:run-locked $root $op $setting $raw-val $dry-run))
                              ($u (fs:unlock $owner))
                              ($c (fs:close $owner)))
                         (case $res (((CctlDone $code) $code) ($err (cctl:reject))))))
                      ($busy (progn (fs:close $owner) (cctl:reject))))))))))))
      ($bad (cctl:reject)))))

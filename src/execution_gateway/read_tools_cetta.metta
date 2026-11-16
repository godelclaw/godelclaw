; Isolated native read adapters. Input and output are JSON data, not code.
!(import! &self ../file_io.metta)
!(import! &self ../cli_values_cetta.metta)
!(import! &self (library proc))
(= (ngr:keys? $pairs $allowed)
   (if (== $pairs ()) True
     (let (JsonPair $key $_) (car-atom $pairs)
       (and (cli:member? $key $allowed) (ngr:keys? (cdr-atom $pairs) $allowed)))))
(= (ngr:git $args)
   (let $argv (cons-atom "git" $args)
     (case (proc:run $argv (system:getcwd) inherit 3000 0)
       (((proc:exited 0 $out $err) (str:trim $out))
        ($failure (GatewayReadFailureV1 "git failed"))))))
(= (ngr:execute $name $value)
   (case $value
     (((JsonObject $pairs)
       (case $name
         (("project-status"
           (if (!= $pairs ()) (GatewayReadFailureV1 "project-status takes an empty object")
             (let* (($commit (ngr:git ("rev-parse" "HEAD"))) ($branch (ngr:git ("branch" "--show-current")))
                    ($changes (ngr:git ("status" "--porcelain" "-uno"))))
               (if (or (!= (get-type $commit) String) (or (!= (get-type $branch) String) (!= (get-type $changes) String)))
                 (GatewayReadFailureV1 "git failed")
                 (JsonObject ((JsonPair "commit" $commit) (JsonPair "branch" $branch)
                   (JsonPair "tracked_changes" (if (== $changes "") 0 (size-atom (str:split "\n" $changes))))))))))
          ("vitals"
           (if (!= $pairs ()) (GatewayReadFailureV1 "vitals takes an empty object")
             (let $loads (str:split " " (str:trim (fs:read-text "/proc/loadavg")))
               (JsonObject ((JsonPair "load" (JsonArray ((parse (car-atom $loads))
                   (parse (car-atom (cdr-atom $loads))) (parse (car-atom (cdr-atom (cdr-atom $loads)))))))
                 (JsonPair "memory" (fs:read-text "/proc/meminfo"))
                 (JsonPair "memory_pressure" (fs:read-text "/proc/pressure/memory")))))))
          ("read-lines"
           (if (and (ngr:keys? $pairs ("path" "start" "count")) (== (get-type (sf:get $value "path" JsonNull)) String))
             (read-lines (sf:get $value "path" "") (sf:get $value "start" 1) (sf:get $value "count" 60))
             (GatewayReadFailureV1 "read-lines needs path, optional start and count")))
          ("ls-tree"
           (if (and (ngr:keys? $pairs ("path" "depth")) (== (get-type (sf:get $value "path" JsonNull)) String))
             (ls-tree (sf:get $value "path" "") (sf:get $value "depth" 2))
             (GatewayReadFailureV1 "ls-tree needs path and optional depth")))
          ("grep-files"
           (if (and (ngr:keys? $pairs ("path" "pattern"))
                 (and (== (get-type (sf:get $value "path" JsonNull)) String)
                      (== (get-type (sf:get $value "pattern" JsonNull)) String)))
             (grep-files (sf:get $value "pattern" "") (sf:get $value "path" ""))
             (GatewayReadFailureV1 "grep-files needs path and pattern")))
          ($unknown (GatewayReadFailureV1 "unknown read-only tool")))))
      ($bad (GatewayReadFailureV1 "tool input must be an object")))))
(= (ngr:main $name $input-path)
   (let $result (ngr:execute $name (json:parse-python (fs:read-text $input-path)))
     (case $result
       (((GatewayReadFailureV1 $message) (progn (system:write stderr $message) (system:exit 2)))
        ($value (system:write stdout (str:concat (json:stringify-python (JsonObject
          ((JsonPair "agent" (system:getenv-or-default "METTACLAW_GATEWAY_AGENT" ""))
           (JsonPair "tool" $name) (JsonPair "value" $value)))) "\n")))))))

; Common tool schema and process carrier for the mode hosts.
!(import! &self ./runtime_host_cetta.metta)
(= (mode-tool:app-root)
   (system:getenv-or-default "METTACLAW_RUNTIME_ROOT" (system:getenv-or-default "METTACLAW_AGENT_ROOT" (system:getcwd))))
(= (mode-tool:schema $name $description $parameters)
   (JsonObject ((JsonPair "type" "function") (JsonPair "function" (JsonObject
     ((JsonPair "name" (turn:text $name)) (JsonPair "description" $description)
      (JsonPair "parameters" (JsonObject
        ((JsonPair "type" "object")
         (JsonPair "properties" (JsonObject (map-atom $parameters $parameter
           (JsonPair (turn:text $parameter) (JsonObject ((JsonPair "type" "string")))))))
         (JsonPair "required" (JsonArray (map-atom $parameters $parameter (turn:text $parameter))))
         (JsonPair "additionalProperties" False))))))))))
(= (mode-tool:shell $command)
   (let $result (proc:run ("/bin/sh" "-c" $command) (system:getcwd) inherit 5000 0)
     (case $result
       (((proc:exited 0 $out $err) (str:join "" ("SUCCESS, RETURN: " $out $err)))
        ((proc:exited $code $out $err)
          (str:join "" ("Tool failed (exit " (repr $code) "): " $out $err)))
        ((proc:timed-out $out $err)
          (str:join "" ("Tool result and effects are unknown after the 5s deadline.\n" $out $err)))
        ($bad (Error component-call (Fault process-failed)))))))
(= (mode-tool:directory? $path)
   (case (fs:metadata $path no-follow)
     (((fs:info directory $_ $_) True) ($other False))))

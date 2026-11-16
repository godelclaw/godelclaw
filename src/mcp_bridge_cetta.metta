; Public MCP operations retain wire data; session policy stays native.
!(import! &self ./mcp_http_cetta.metta)
!(import! &self ./effect_receipts_cetta.metta)
!(import! &self ./tool_output_cetta.metta)
(= (mcp:output $value)
   (let* (($encoded (json:stringify-python-with $value ((ensure-ascii True) (sort-keys True))))
          ($number (str:string->number (system:getenv-or-default "METTACLAW_MCP_MAX_OUTPUT_CHARS" "40000")))
          ($limit (if (== (get-type $number) Number) $number 40000)))
     (if (or (<= $limit 0) (<= (str:char-length $encoded) $limit)) $encoded
       (let $artifact (artifact:retain-text $encoded)
         (if (mcp:failed? $artifact) "{\"error\":\"full MCP output could not be retained\"}"
           (json:stringify-python-with
             (JsonObject ((JsonPair "truncated" True) (JsonPair "chars" (str:char-length $encoded))
                          (JsonPair "prefix" (str:char-slice $encoded 0 $limit)) (JsonPair "artifact" $artifact)))
             ((ensure-ascii True) (sort-keys True))))))))
(= (mcp:public $value)
   (case $value
     (((McpFailureV1 $kind $detail)
       (mcp:output (JsonObject ((JsonPair "error" (diagnostic:text $detail 800)) (JsonPair "kind" $kind)))))
      ((Error $where $why) (mcp:output (JsonObject ((JsonPair "error" "native MCP evaluation failed")))))
      ($ok (mcp:output $ok)))))
(= (mcp:server-rows $pairs)
   (if (== $pairs ()) ()
     (let* (((JsonPair $name $spec) (car-atom $pairs)) ($rest (mcp:server-rows (cdr-atom $pairs))))
       (case $spec
         (((JsonObject $_) (cons-atom
            (JsonObject ((JsonPair "name" $name) (JsonPair "type" (sf:get $spec "type" "stdio")))) $rest))
          ($other $rest))))))
(= (mcp:servers)
   (let $configuration (mcp:configuration)
     (mcp:public
       (case (state:canonical $configuration)
         (((JsonObject $pairs) (JsonObject ((JsonPair "servers" (JsonArray (mcp:server-rows $pairs))))))
          ($bad $bad))))))
(= (mcp:tools-array $result)
   (case (sf:get $result "tools" JsonNull)
     (((JsonArray $tools) $tools)
      ($bad (mcp:failure "protocol" "tools/list result has no tools array")))))
(= (mcp:tool-lines $tools)
   (if (== $tools ()) ()
     (let* (($tool (car-atom $tools)) ($name (sf:get $tool "name" JsonNull))
            ($description (sf:get $tool "description" "")))
       (if (!= (get-type $name) String) (mcp:failure "protocol" "tool name must be text")
         (let* (($text (if (== $description JsonNull) "" $description))
                ($first (car-atom (str:split "\n" $text))) ($rest (mcp:tool-lines (cdr-atom $tools))))
           (if (mcp:failed? $rest) $rest
             (cons-atom (JsonObject ((JsonPair "name" $name)
                         (JsonPair "description" (str:char-slice $first 0 (min 300 (str:char-length $first)))))) $rest)))))))
(= (mcp:find-tool $tools $wanted)
   (if (== $tools ())
     (JsonObject ((JsonPair "error" (str:concat "unknown tool " $wanted)) (JsonPair "available" (JsonArray ()))))
     (let $tool (car-atom $tools)
       (if (== (sf:get $tool "name" "") $wanted) $tool (mcp:find-tool (cdr-atom $tools) $wanted)))))
(= (mcp:operation $state (McpListV1))
   (let $result (mcp:call $state "tools/list" (JsonObject ()))
     (if (mcp:failed? $result) $result
       (let $tools (mcp:tools-array $result)
         (if (mcp:failed? $tools) $tools
           (let $lines (mcp:tool-lines $tools)
             (if (mcp:failed? $lines) $lines
               (JsonObject ((JsonPair "server" (mcp:get $state "name" "")) (JsonPair "tools" (JsonArray $lines)))))))))))
(= (mcp:operation $state (McpInfoV1 $name))
   (let $result (mcp:call $state "tools/list" (JsonObject ()))
     (if (mcp:failed? $result) $result
       (let $tools (mcp:tools-array $result)
         (if (mcp:failed? $tools) $tools (mcp:find-tool $tools $name))))))
(= (mcp:operation $state (McpCallV1 $tool $arguments))
   (let $result (mcp:call $state "tools/call"
                   (JsonObject ((JsonPair "name" $tool) (JsonPair "arguments" $arguments))))
     (let $_ (if (mcp:failed? $result) 0
               (collapse (catch (mcp:atlas-record (mcp:get $state "name" "") $tool $result))))
       $result)))
(= (mcp:one $stage $answers)
   (case $answers
     ((((Error $where $why)) (mcp:failure "native" (diagnostic:text ($stage $where $why) 600)))
      (($value) $value)
      ($other (mcp:failure "incomplete" (str:concat $stage ": result and effects are unknown; inspect before retrying"))))))
(= (mcp:with-client $server $operation)
   (let $state (mcp:one "configuration" (collapse (catch (mcp:open $server))))
     (if (mcp:failed? $state) (mcp:public $state)
       (let* (($initialized (mcp:one "initialize" (collapse (catch (mcp:initialize $state)))))
              ($result (if (mcp:failed? $initialized) $initialized
                         (mcp:one "operation" (collapse (catch (mcp:operation $state $operation))))))
              ($_ (collapse (catch (mcp:close $state))))) (mcp:public $result)))))
(= (mcp:tools $server) (mcp:with-client $server (McpListV1)))
(= (mcp:tool-info $server $tool) (mcp:with-client $server (McpInfoV1 $tool)))
(= (mcp:call-tool $server $tool $arguments)
   (case (catch (json:parse (if (== $arguments "") "{}" $arguments)))
     (((JsonObject $pairs) (mcp:with-client $server (McpCallV1 $tool (JsonObject $pairs))))
      ($bad (mcp:public (mcp:failure "configuration" "arguments must be a JSON object"))))))
(= (mcp:atlas-payload $result)
   (if (or (sf:get $result "isError" False) (sf:get $result "is_error" False)) JsonNull
     (case (sf:get $result "structuredContent" (sf:get $result "structured_content" JsonNull))
       (((JsonObject $pairs) (JsonObject $pairs))
        ($other
          (if (!= (sf:get $result "receipt" JsonNull) JsonNull) $result
            (case (sf:get $result "content" JsonNull)
              (((JsonArray $parts) (mcp:atlas-parts $parts)) ($missing JsonNull)))))))))
(= (mcp:atlas-parts $parts)
   (if (== $parts ()) JsonNull
     (let $part (car-atom $parts)
       (if (== (sf:get $part "type" "") "text")
         (case (catch (json:parse (sf:get $part "text" "")))
           (((JsonObject $pairs) (JsonObject $pairs))
            ($bad (mcp:atlas-parts (cdr-atom $parts)))))
         (mcp:atlas-parts (cdr-atom $parts))))))
(= (mcp:atlas-record $server $tool $result)
   (let* (($payload (mcp:atlas-payload $result)) ($receipt (sf:get $payload "receipt" JsonNull))
          ($id (sf:get $receipt "receipt_id" "")))
     (if (== $id "") 0
       (let $identity
           (if (== $tool "atlas_query")
             (let* (($view (sf:get $payload "view_id" "")) ($revision (sf:get $payload "state_revision" "")))
               (if (or (== $view "") (== $revision "")) JsonNull
                 (JsonObject ((JsonPair "kind" "view") (JsonPair "view_id" $view) (JsonPair "state_revision" $revision)
                              (JsonPair "receipt_id" $id) (JsonPair "status" (sf:get $payload "status" "unknown"))))))
             (if (== $tool "atlas_revise")
               (let $revision (sf:get $payload "revision_id" "")
                 (if (== $revision "") JsonNull
                   (JsonObject ((JsonPair "kind" "revision") (JsonPair "revision_id" $revision) (JsonPair "receipt_id" $id)
                                (JsonPair "conflicts_witnessed" (sf:get $payload "conflicts_witnessed" False))))))
               JsonNull))
         (if (== $identity JsonNull) 0
           (er:append (JsonObject ((JsonPair "schema" 1) (JsonPair "recorded_at" (system:wall-time))
                                  (JsonPair "disposition" "atlas-result-returned") (JsonPair "server" (str:char-slice $server 0 (min 200 (str:char-length $server))))
                                  (JsonPair "tool" $tool) (JsonPair "atlas" $identity)))))))))

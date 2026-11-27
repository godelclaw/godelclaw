; A bounded preview never erases the complete tool output: a long text keeps
; every byte in a file whose size and SHA-256 the preview names, and an MCP
; result past its character bound is a prefix that points to the complete
; structured result.
!(import! &self ../native_test_support.metta)
!(import! &self ../../src/mcp_bridge_cetta.metta)
!(import! &self (library list))
!(import! &self (library json))

; "evidence λ\n" doubled 17 times: 131,072 lines, 1.4 MB.
(= (to:large $text $n) (if (== $n 0) $text (to:large (str:concat $text $text) (- $n 1))))
(= (to:between $text $open $close)
   (case (str:find $text $open)
     (((TextNotFound $m) "")
      ($at (let $rest (str:byte-slice $text (+ $at (str:byte-length $open)) (str:byte-length $text))
             (case (str:find $rest $close) (((TextNotFound $m) $rest) ($end (str:byte-slice $rest 0 $end)))))))))

!(let* (($raw (to:large "evidence λ\n" 17))
        ($view (artifact:text-view $raw 123))
        ($path (to:between $view "full output: " ";"))
        ($bytes (str:byte-length $raw)))
   (nt:require a-large-text-keeps-every-byte-and-a-verifiable-identity
     (and (str:starts-with $view (str:concat (str:char-slice $raw 0 123) "\n[preview; full output: "))
     (and (== (fs:read-text $path) $raw)
     (and (nt:contains $view (str:join "" ("; " (repr $bytes) " bytes; sha256 " (str:sha256 $raw) "]")))
          (< (str:char-length $view) 400))))))

!(let* (($data (json:parse (str:join "" ("{\"rows\": [" (str:join ", " (map-atom (list:range 0 1000) $i (repr $i))) "]}"))))
        ($result (json:parse (mcp:output $data)))
        ($artifact (json:get $result "artifact")))
   (nt:require an-mcp-preview-points-to-the-complete-structured-result
     (and (== (json:get $result "truncated") True)
     (and (== (str:char-length (json:get $result "prefix")) 100)
          (== (json:stringify-python-with (json:parse (fs:read-text (json:get $artifact "path"))) ((sort-keys True)))
              (json:stringify-python-with $data ((sort-keys True))))))))

!(println! UNIT_TOOL_OUTPUT_OK)

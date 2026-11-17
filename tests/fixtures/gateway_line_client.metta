; Native JSON-line peer; request text stays data throughout the exchange.
!(import! &self ../../src/line_rpc_cetta.metta)
!(import! &self ../../src/cli_values_cetta.metta)
!(import! &self (library fs))
!(let* (($address (cli:text (system:arg 0))) ($request-path (cli:text (system:arg 1)))
         ($reply-path (cli:text (system:arg 2))) ($request (fs:read-text $request-path)))
   (case (rpc:line $address $request 3000 1048576)
     (((RpcReplyV1 $text) (fs:write $reply-path $text))
      ($bad (progn (println! (GATEWAY_LINE_CLIENT_FAILED $bad)) (system:exit 1))))))

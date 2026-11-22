; Message authority is explicit. Cache layout and quoted text cannot assign
; roles. The context renderer carries this value through compaction and binds
; coverage to its final serialized bytes, before provider admission.
; Only core import! is needed here. The PeTTa-only lib_import package provides
; Prolog/git import helpers, none of which the request renderer calls.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self ./src/commentary_contract_cetta.metta)


(= (request:text $value)
   (if (== (get-type $value) String) $value (repr $value)))
(= (request:message $role $text)
   (JsonObject ((JsonPair "role" (JsonString $role))
                (JsonPair "content" (JsonString (request:text $text))))))
(= (request:append-list $left $right)
   (if (== $left ()) $right
     (cons-atom (car-atom $left) (request:append-list (cdr-atom $left) $right))))
(= (request:initial-messages $system $activity)
   (case $system
     (((InstructionContext $instructions $evidence)
       (coding-message-list (coding-message system $instructions)
         (coding-message-list (coding-message assistant $evidence)
           (coding-message-list (coding-message user $activity) coding-messages-empty))))
      ($plain (coding-message-list (coding-message system $plain)
        (coding-message-list (coding-message user $activity) coding-messages-empty))))))
(= (request:append $request $text)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (RoleContext $messages $advertised $observations $history $index
         (str:join "\n\n" ($data $text)) $instructions))
      ($plain (str:join "\n\n" ($plain $text))))))
(= (request:instruct $request $text)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (RoleContext $messages $advertised $observations $history $index $data
         (str:join "\n\n" ($instructions $text))))
      ($plain (str:concat $plain $text)))))
(= (request:rewrite-messages coding-messages-empty) coding-messages-empty)
(= (request:rewrite-messages (coding-message-list (coding-message $role $text) $tail))
   (coding-message-list
     (coding-message $role
       (if (or (== $role system) (== $role developer))
         (commentary:rewrite (request:text $text)) $text))
     (request:rewrite-messages $tail)))
(= (request:format $request)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (RoleContext (request:rewrite-messages $messages) $advertised $observations $history $index $data
         (str:join "\n\n" ($instructions (commentary:instruction-tail)))))
      ($plain (commentary:prompt (request:text $plain))))))
(= (request:place
     (RoleContext $messages $advertised $observations $history $old-index $data $instructions) $chunks $index)
   (RoleContext $messages $advertised $observations $chunks $index $data $instructions))
; The request index is a list of chunk texts once placed (one evidence message
; each, so a new request rewrites only the last one), and text before that.
(= (request:index-list $index)
   (if (== (get-type $index) String) ($index) $index))
(= (request:data-messages $chunks)
   (if (== $chunks ()) ()
     (if (== (car-atom $chunks) "") (request:data-messages (cdr-atom $chunks))
       (cons-atom (request:message "assistant" (car-atom $chunks))
         (request:data-messages (cdr-atom $chunks))))))
; Instructions retain their explicit roles. Free-form tool observations are
; evidence, not native tool results (which require a matching call identity).
(= (request:conversation coding-messages-empty) ())
(= (request:conversation (coding-message-list (coding-message $role $text) $tail))
   (cons-atom (request:message (if (== $role tool) "assistant" (repr $role)) $text)
     (request:conversation $tail)))
(= (request:partition coding-messages-empty $instructions $conversation)
   (RequestParts $instructions $conversation))
(= (request:partition (coding-message-list (coding-message $role $text) $tail) $instructions $conversation)
   (if (or (== $role system) (== $role developer))
     (request:partition $tail
       (request:append-list $instructions ((request:message (repr $role) $text))) $conversation)
     (RequestParts $instructions
       (request:append-list $conversation
         (request:conversation (coding-message-list (coding-message $role $text) $tail))))))
; Cache points index the rendered messages (the policy is message 0). Only the
; Anthropic route reads them: it reuses a cached prefix only at a marked block,
; looking back at most 20 blocks for an earlier entry, with at most four marks
; per request. Transcript chunks are immutable, and a new request rewrites only
; the last chunk of the request index, so the marks go on the last two index
; chunks, the end of the transcript and the end of the trusted instructions.
; Observations and data change on every call: a mark there is written at the
; cache-write price and never read back.
(= (request:cache-points $trusted $past $requests)
   (let* (($history-end (+ $trusted $past)) ($index-end (+ $history-end $requests)))
     (if (> $requests 1) ($index-end (- $index-end 1) $history-end $trusted)
       (if (== $requests 1) ($index-end $history-end $trusted) ($history-end $trusted)))))
(= (request:numbers $values)
   (if (== $values ()) ()
     (cons-atom (JsonNumber (repr (car-atom $values))) (request:numbers (cdr-atom $values)))))
(= (request:render $request)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (let* (((RequestParts $trusted $conversation) (request:partition $messages () ()))
              ($policy (request:message "system" (str:join "\n\n"
                 ((str:concat "ADVERTISED_CAPABILITIES:\n" $advertised) $instructions))))
              ($past (request:data-messages $history))
              ($requests (request:data-messages (request:index-list $index)))
              ($current (request:data-messages ($observations $data)))
              ($evidence (request:append-list $past (request:append-list $requests $current)))
              ($all (cons-atom $policy (request:append-list $trusted
                       (request:append-list $evidence $conversation))))
              ($points (request:cache-points (length $trusted) (length $past) (length $requests))))
         (json-stringify (JsonObject ((JsonPair "kind" (JsonString "role-context-v1"))
                                     (JsonPair "messages" (JsonArray $all))
                                     (JsonPair "cache_points" (JsonArray (request:numbers $points))))))))
      ($plain $plain))))
(= (request:bytes $request) (str:byte-length (request:render $request)))

; Only a caller-authored outer envelope declares roles. Content is never
; rescanned for cache delimiters, role labels or nested request envelopes.
(= (request:decode $text)
   (if (str:starts-with $text "{")
     (let $json (json-parse $text)
       (if (== (json-object-get $json "kind") (JsonString "role-context-v1"))
         (let (JsonArray $messages) (json-object-get $json "messages")
           (RoleMessages $messages (json-object-get $json "cache_points"))) LegacyPrompt))
     LegacyPrompt))

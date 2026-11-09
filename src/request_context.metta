; Message authority is explicit. Cache layout and quoted text cannot assign
; roles. The context renderer carries this value through compaction and binds
; coverage to its final serialized bytes, before provider admission.
; Only core import! is needed here. The PeTTa-only lib_import package provides
; Prolog/git import helpers, none of which the request renderer calls.
!(import! &self (library json))
!(import! &self (library str))
!(import! &self ./src/model_commentary.py)
; Rendering needs the active evaluator, not mode switching, receipt storage,
; or process control. Keep the provider entrypoint's dependency closure small.
!(import! &self ./src/mode_runtime.metta)
!(if (mode-runtime:cetta?)
   (import! &self ./src/engine_active_cetta.metta)
   (import! &self ./src/engine_modes_python.metta))

(= (request:cetta?)
   (let $active (if (mode-runtime:cetta?) (engine-native:active) (engine-python:active))
     (case $active ((cetta True) ("cetta" True) ($other False)))))

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
         (str:concat "" (py-call (model_commentary.rewrite_instruction (request:text $text)))) $text))
     (request:rewrite-messages $tail)))
(= (request:format $request)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (RoleContext (request:rewrite-messages $messages) $advertised $observations $history $index $data
         (str:join "\n\n" ($instructions (str:concat "" (py-call (model_commentary.instruction_tail)))))))
      ($plain (py-call (model_commentary.prompt $plain))))))
(= (request:place
     (RoleContext $messages $advertised $observations $history $old-index $data $instructions) $chunks $index)
   (RoleContext $messages $advertised $observations $chunks $index $data $instructions))
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
(= (request:render $request)
   (case $request
     (((RoleContext $messages $advertised $observations $history $index $data $instructions)
       (let* (((RequestParts $trusted $conversation) (request:partition $messages () ()))
              ($policy (request:message "system" (str:join "\n\n"
                 ((str:concat "ADVERTISED_CAPABILITIES:\n" $advertised) $instructions))))
              ($evidence (request:data-messages
                 (request:append-list $history ($index $observations $data))))
              ($all (cons-atom $policy (request:append-list $trusted
                       (request:append-list $evidence $conversation)))))
         (json-stringify (JsonObject ((JsonPair "kind" (JsonString "role-context-v1"))
                                     (JsonPair "messages" (JsonArray $all))
                                     (JsonPair "cache_points" (JsonArray
                                       ((JsonNumber (repr (length $trusted)))
                                        (JsonNumber (repr (+ (length $trusted) (length $history))))
                                        (JsonNumber (repr (- (+ (length $trusted) (length $history)) 1)))
                                        (JsonNumber (repr (- (length $all) 2)))))))))))
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

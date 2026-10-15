; Grandfathered embedding/collection transport for the separate SWI-PeTTa path.
(= (semantic:remember $text) (legacy-remember $text))
(= (semantic:query $text $count) (legacy-query $text $count))
(= (semantic:maintain) True)
(= (semantic:io-ids) ())
(= (semantic:retry-wait) ())
(= (semantic:health) (py-call (memory_health.engine_query_probe)))
(= (forget $id) (py-call (lib_chromadb.forget_id $id)))
(= (forget $id $reason) (forget $id))
(= (memory-read $id) (py-call (lib_chromadb.query_by_id $id)))
(= (memory-edit $id $text) "MEMORY_EDIT_UNAVAILABLE: select CeTTa for native revision and retry storage")
(= (memory-pending) ())
(= (memory-retry) "MEMORY_RETRY_UNAVAILABLE: select CeTTa for durable write retries")
(= (memory-history $id) "MEMORY_HISTORY_UNAVAILABLE: select CeTTa for retained memory changes")

(= (semantic:query $text $count $mode) (legacy-query $text $count))
(= (am:context) (AttentionMemory unavailable-on-this-engine))
(= (attention-policy) "ECAN is available on CeTTa; legacy similarity retrieval is unchanged")
(= (attention-policy $key $value) (attention-policy))
(= (attention-use $id) (attention-policy))
(= (attention-use $id $amount) (attention-policy))
(= (attention-link $from $to $weight) (attention-policy))
(= (attention-focus) (attention-policy))
(= (attention-focus $count) (attention-policy))
(= (attention-last) (attention-policy))

(= (am:begin-turn) True)

(= (attention-policy $key) (attention-policy))

(= (attention-reload) (attention-policy))

; Core upstream modes remain usable; full native catalogue belongs to CeTTa.
(= (settings) "Full settings catalogue requires CeTTa")
(= (settings $ns) (settings))
(= (setting $ns $key) (settings))
(= (setting $ns $key $value) (settings))
(= (settings:context) (SettingsIndex unavailable-on-this-engine))

(= (settings:protocol) (SettingsProtocol available-on-CeTTa))

; Native components publish their API on CeTTa. Core legacy tools keep their
; existing help and do not import the native component registry.
(= (api:prompt-lines) ())
(= (api:help-index) "")
(= (api:help $name) (APINotRegistered $name))
(= (api:check $command) (APINotRegistered ""))

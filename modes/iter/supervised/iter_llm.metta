;; Iter's model call in a supervised godelclaw agent. It replaces the fork's
;; iter_llm.metta (the same three functions), so iter.metta and utils.metta
;; run unchanged: the agent's selected provider and model are used through
;; godelclaw's native provider, keys stay in the host's credential aliases,
;; and each request first passes the operator gate and acknowledges the
;; input that the saved history holds. See godelclaw_host.metta.
!(import! &self godelclaw_host)

;; The endpoint, key and timeout of iter.metta's configuration belong to the
;; standalone client; here the host's provider settings apply.
(= (init_client $base_url $api_key $timeout) (host:boot))

(= (exception_text $error) (repr $error))

;; returns message_json finish_reason response usage, as the fork's client
(= (chat_completion $messages $tools $model $max_tokens)
   (host:chat-completion $messages $tools $model $max_tokens))

; Shared value and workspace policy helpers. No mutable process state.
!(import! &self str)
!(import! &self system)
!(import! &self json)

(= (coding:member $pairs $key)
   (if (== $pairs ()) JsonNull
     (let (JsonPair $name $value) (car-atom $pairs)
       (if (== $name $key) $value (coding:member (cdr-atom $pairs) $key)))))
(= (coding:field $value $key)
   (case $value (((JsonObject $pairs) (coding:member $pairs $key)) ($other JsonNull))))
(= (coding:text $value) (case $value (((JsonString $text) $text) ($other ""))))
(= (coding:items $value) (case $value (((JsonArray $items) $items) ($other ()))))
(= (coding:number $value $default)
   (case $value (((JsonNumber $text) (str:string->number $text)) ($other $default))))

; Workspace commands do not inherit the agent's credentials or control files.
(= (coding:workspace-shell $cmd)
   (let $keep-ssh (== (system:getenv-or-default "METTACLAW_CODING_ALLOW_SSH_AUTH" "0") "1")
     (str:join "" (
       "for v in $(compgen -e); do case $v in METTACLAW_*|CETTA_IO_AUTH_*|*_API_KEY|*_TOKEN|*_SECRET|*_PASSWORD|AWS_*|AZURE_*|GCLOUD_*|GOOGLE_APPLICATION_CREDENTIALS|TG_BOT_TOKEN) unset $v;; esac; done; unset PYTHONPATH"
       (if $keep-ssh "" " SSH_AUTH_SOCK GIT_ASKPASS")
       "; " $cmd))))

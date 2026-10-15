; SPDX-License-Identifier: MIT
; Client and receipt observer share the declared command vocabulary.
; A control handled outside cognition is never a queued cognitive question.
!(import! &self str)
!(import! &self ./channels/telegram_commands.metta)

(= (ti:control-text? $text)
   (let $words (str:split-whitespace $text)
     (if (== $words ()) False
       (let $name (str:lower (car-atom (str:split "@" (car-atom $words))))
         (> (size-atom (collapse (match &self (tg-cmd:command $name $kind $help) True))) 0)))))

(= (ti:field $object $key)
   (case $object (((JsonObject $pairs) (ti:pair $pairs $key)) ($other JsonNull))))
(= (ti:pair $pairs $key)
   (if (== $pairs ()) JsonNull
     (case (car-atom $pairs)
       (((JsonPair $key $value) $value) ($other (ti:pair (cdr-atom $pairs) $key))))))
(= (ti:text $message)
   (case (ti:field $message "text")
     (((JsonString $text) (if (== $text "") (ti:caption $message) $text))
      ($other (ti:caption $message)))))
(= (ti:caption $message)
   (case (ti:field $message "caption") (((JsonString $text) $text) ($other ""))))
(= (ti:message $update $keys)
   (if (== $keys ()) JsonNull
     (case (ti:field $update (car-atom $keys))
       ((JsonNull (ti:message $update (cdr-atom $keys))) ($message $message)))))
(= (ti:operator-control? $kind $role $update)
   (if (and (== $kind "message") (== $role "operator"))
     (ti:control-text? (ti:text (ti:message $update ("message" "edited_message" "channel_post" "edited_channel_post")))) False))

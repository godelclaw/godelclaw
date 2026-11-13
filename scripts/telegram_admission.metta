; What the access policy's facts file adds to the chats Telegram's channel
; service admits, for the service launcher, as one line:
;   TELEGRAM_ADMISSION {"chats": [...], "owners": [...], "private": B, "all_groups": B}
; A refused facts file prints TELEGRAM_ADMISSION_REFUSED and exits 78.
!(import! &self json)
!(import! &self ../channels/access_policy.metta)

(= (ta:texts $items)
   (JsonArray (collapse (let $x (superpose $items) (JsonString $x)))))

!(let $ready (ap:ensure)
   (if (not (== (ap:get refused none) none))
     (let $said (println! TELEGRAM_ADMISSION_REFUSED) (system:exit 78))
     (case (ap:telegram-admission)
       (((TelegramAdmissionV1 $chats $owners $private $groups)
         (system:write stdout (str:concat "TELEGRAM_ADMISSION "
           (str:concat (json-stringify (JsonObject ((JsonPair "chats" (ta:texts $chats))
                                                    (JsonPair "owners" (ta:texts $owners))
                                                    (JsonPair "private" (JsonBool $private))
                                                    (JsonPair "all_groups" (JsonBool $groups)))))
                       "\n"))))))))

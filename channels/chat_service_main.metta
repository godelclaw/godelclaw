; A chat channel's own service process, for the channel METTACLAW_CHANNEL
; names: irc or mattermost. Run by `run.sh chat-channel`, beside the loop.
!(import! &self (library system))
!(import! &self ./channels/irc_service.metta)
!(import! &self ./channels/mattermost_service.metta)

!(case (system:getenv-or-default "METTACLAW_CHANNEL" "")
   (("irc" (irc:serve))
    ("mattermost" (mm:serve))
    ($other (println! (CHAT_SERVICE_HAS_NO_CHANNEL $other)))))

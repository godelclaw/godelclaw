; Where a reply goes: an explicit chat; otherwise the configured primary
; chat, which the last chat the agent heard from cannot displace; otherwise
; that last chat. Replays the 2026-08-27 cross-chat routing failure.
!(import! &self ../native_test_support.metta)

(= (rt:root) (nt:required-env "TEST_REPOSITORY"))
(= (rt:targets $primary)
   (let* (($home (nt:required-env "HOME"))
          ($source (path:join $home "reply-target.metta"))
          ($_ (fs:write $source (str:join "" (
                "!(import! &self (library lib_import))\n"
                "!(import! &self " (repr (path:join (rt:root) "channels/telegram.metta")) ")\n"
                "!(let $_ (tg:set reply-chat \"protobots\") (println! (TARGETS (tg:reply-target \"\") (tg:reply-target \"research-group\"))))\n")))))
     (nt:exit-ok (proc:run ((path:join (nt:required-env "NATIVE_TEST_CORE") "cetta") "--lang" "petta"
                            "--profile" "extended" "--import-mode" "ancestor-walk" $source)
                           (rt:root)
                           (cons-atom ("METTACLAW_TELEGRAM_PRIMARY_CHAT_ID" $primary) (nt:environment $home))
                           60000 1048576))))

!(let* (($_ (nt:require without-a-primary-the-last-chat-answers
              (nt:contains (rt:targets "") "(TARGETS \"protobots\" \"research-group\")")))
        ($_ (nt:require the-primary-chat-survives-cross-chat-state
              (nt:contains (rt:targets "operator-private") "(TARGETS \"operator-private\" \"research-group\")"))))
   (println! UNIT_TELEGRAM_REPLY_TARGET_OK))
